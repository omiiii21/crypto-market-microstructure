"""
OKX REST API client.

Provides fallback data fetching via REST API when WebSocket is unavailable.
Implements rate limiting to avoid exchange bans.

Endpoints:
    Base URL: https://www.okx.com
    Order Book: GET /api/v5/market/books?instId={instId}&sz=20
    Ticker: GET /api/v5/market/ticker?instId={instId}
    Mark Price: GET /api/v5/public/mark-price?instType=SWAP&instId={instId}

Rate Limits:
    - OKX: 20 requests per 2 seconds (10 per second)
    - Conservative default: 10 per second with token bucket

Response Format (Order Book):
    {
        "code": "0",
        "msg": "",
        "data": [
            {
                "asks": [["50001.0", "1.5", "0", "2"], ...],
                "bids": [["50000.0", "2.0", "0", "3"], ...],
                "ts": "1234567890123",
                "seqId": 123456789
            }
        ]
    }

Response Format (Ticker):
    {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "last": "50000.0",
                "high24h": "51000.0",
                "low24h": "49000.0",
                "vol24h": "1000.5",
                "volCcy24h": "50025000",
                "ts": "1234567890123"
            }
        ]
    }
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import aiohttp
import structlog

from src.adapters.okx.normalizer import OKXNormalizer
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""

    pass


class OKXRestClient:
    """
    Async REST API client for OKX.

    Provides fallback order book and ticker fetching via REST API.
    Implements token bucket rate limiting.

    Attributes:
        base_url: REST API base URL.
        rate_limit_per_second: Maximum requests per second.
        session: aiohttp ClientSession.

    Example:
        >>> client = OKXRestClient(
        ...     base_url="https://www.okx.com",
        ...     rate_limit_per_second=10
        ... )
        >>> snapshot = await client.get_orderbook("BTC-USDT-SWAP", limit=20)
        >>> print(f"Best bid: {snapshot.best_bid}")
    """

    def __init__(
        self,
        base_url: str,
        rate_limit_per_second: int = 10,
        timeout_seconds: int = 10,
    ):
        """
        Initialize REST client.

        Args:
            base_url: REST API base URL.
            rate_limit_per_second: Maximum requests per second.
            timeout_seconds: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.rate_limit_per_second = rate_limit_per_second
        self.timeout_seconds = timeout_seconds

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time: float = 0.0
        self._request_interval = 1.0 / rate_limit_per_second

        logger.info(
            "rest_client_initialized",
            exchange="okx",
            base_url=base_url,
            rate_limit=rate_limit_per_second,
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure aiohttp session is created."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "crypto-surveillance/1.0"},
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("rest_client_session_closed", exchange="okx", base_url=self.base_url)

    async def _rate_limit(self) -> None:
        """
        Apply rate limiting using simple time-based throttling.

        Ensures minimum interval between requests.
        """
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time

        if time_since_last < self._request_interval:
            wait_time = self._request_interval - time_since_last
            await asyncio.sleep(wait_time)

        self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request with rate limiting and error handling.

        Args:
            method: HTTP method (GET, POST).
            endpoint: API endpoint path.
            params: Query parameters.

        Returns:
            Dict[str, Any]: Parsed JSON response data field.

        Raises:
            RateLimitError: If rate limited by exchange.
            ConnectionError: If request fails.
            ValueError: If response code is not "0" (error).
        """
        await self._rate_limit()

        session = await self._ensure_session()
        url = f"{self.base_url}{endpoint}"

        try:
            async with session.request(method, url, params=params) as response:
                # Check for rate limiting
                if response.status == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(
                        "rest_rate_limited",
                        exchange="okx",
                        url=url,
                        retry_after=retry_after,
                    )
                    raise RateLimitError(f"Rate limited, retry after {retry_after}s")

                # Check for HTTP errors
                if response.status >= 400:
                    error_text = await response.text()
                    logger.error(
                        "rest_request_failed",
                        exchange="okx",
                        url=url,
                        status=response.status,
                        error=error_text,
                    )
                    raise ConnectionError(
                        f"REST request failed with status {response.status}: {error_text}"
                    )

                data = await response.json()

                # Check OKX-specific error code
                if data.get("code") != "0":
                    error_msg = data.get("msg", "Unknown error")
                    logger.error(
                        "rest_okx_error",
                        exchange="okx",
                        url=url,
                        code=data.get("code"),
                        message=error_msg,
                    )
                    raise ValueError(f"OKX API error: {error_msg}")

                return data

        except aiohttp.ClientError as e:
            logger.error("rest_client_error", exchange="okx", url=url, error=str(e))
            raise ConnectionError(f"REST request failed: {e}")
        except asyncio.TimeoutError:
            logger.error(
                "rest_timeout", exchange="okx", url=url, timeout=self.timeout_seconds
            )
            raise ConnectionError(f"REST request timeout after {self.timeout_seconds}s")

    async def get_orderbook(
        self,
        inst_id: str,
        limit: int = 20,
        instrument: str = "UNKNOWN",
    ) -> OrderBookSnapshot:
        """
        Fetch order book snapshot via REST API.

        Args:
            inst_id: OKX instrument ID (e.g., "BTC-USDT-SWAP").
            limit: Number of levels to fetch (1-400, default 20).
            instrument: Normalized instrument ID for logging.

        Returns:
            OrderBookSnapshot: Current order book state.

        Raises:
            ValueError: If response format is invalid.
            ConnectionError: If request fails.

        Example:
            >>> snapshot = await client.get_orderbook("BTC-USDT-SWAP", limit=20)
        """
        endpoint = "/api/v5/market/books"
        params = {"instId": inst_id, "sz": limit}

        try:
            response = await self._request("GET", endpoint, params)

            # Extract data array
            data_array = response.get("data", [])
            if not data_array:
                raise ValueError("Empty data array in OKX order book response")

            data = data_array[0]

            # Parse response
            timestamp_ms = int(data["ts"])
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            sequence_id = int(data["seqId"])

            # Parse bids (OKX format: [price, quantity, deprecated, num_orders])
            bids = [
                PriceLevel(price=Decimal(level[0]), quantity=Decimal(level[1]))
                for level in data.get("bids", [])
                if Decimal(level[1]) > 0
            ]

            # Parse asks
            asks = [
                PriceLevel(price=Decimal(level[0]), quantity=Decimal(level[1]))
                for level in data.get("asks", [])
                if Decimal(level[1]) > 0
            ]

            # Ensure sorted
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            snapshot = OrderBookSnapshot(
                exchange="okx",
                instrument=instrument,
                timestamp=timestamp,
                local_timestamp=datetime.now(timezone.utc),
                sequence_id=sequence_id,
                bids=bids,
                asks=asks,
                depth_levels=limit,
            )

            logger.debug(
                "rest_orderbook_fetched",
                exchange="okx",
                instrument=instrument,
                inst_id=inst_id,
                sequence_id=sequence_id,
                bids_count=len(bids),
                asks_count=len(asks),
            )

            return snapshot

        except KeyError as e:
            logger.error(
                "rest_orderbook_parse_error",
                exchange="okx",
                instrument=instrument,
                inst_id=inst_id,
                missing_field=str(e),
            )
            raise ValueError(f"Invalid order book response: missing {e}")

    async def get_ticker(
        self,
        inst_id: str,
        instrument: str = "UNKNOWN",
    ) -> TickerSnapshot:
        """
        Fetch ticker snapshot via REST API.

        Args:
            inst_id: OKX instrument ID (e.g., "BTC-USDT-SWAP").
            instrument: Normalized instrument ID.

        Returns:
            TickerSnapshot: Current ticker state.

        Raises:
            ValueError: If response format is invalid.
            ConnectionError: If request fails.

        Example:
            >>> ticker = await client.get_ticker("BTC-USDT-SWAP")
        """
        ticker_endpoint = "/api/v5/market/ticker"
        params = {"instId": inst_id}

        try:
            # Fetch ticker
            ticker_response = await self._request("GET", ticker_endpoint, params)

            ticker_data_array = ticker_response.get("data", [])
            if not ticker_data_array:
                raise ValueError("Empty data array in OKX ticker response")

            ticker_data = ticker_data_array[0]

            timestamp_ms = int(ticker_data["ts"])
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            last_price = Decimal(ticker_data["last"])
            high_24h = Decimal(ticker_data["high24h"])
            low_24h = Decimal(ticker_data["low24h"])
            volume_24h = Decimal(ticker_data["vol24h"])
            volume_24h_usd = Decimal(ticker_data["volCcy24h"])

            # Fetch mark price for perpetuals (SWAP instruments)
            mark_price: Optional[Decimal] = None
            index_price: Optional[Decimal] = None
            funding_rate: Optional[Decimal] = None
            next_funding_time: Optional[datetime] = None

            if inst_id.endswith("-SWAP"):
                try:
                    mark_endpoint = "/api/v5/public/mark-price"
                    mark_params = {"instType": "SWAP", "instId": inst_id}
                    mark_response = await self._request("GET", mark_endpoint, mark_params)

                    mark_data_array = mark_response.get("data", [])
                    if mark_data_array:
                        mark_data = mark_data_array[0]
                        mark_price = Decimal(mark_data["markPx"])
                        index_price = Decimal(mark_data.get("idxPx", "0"))

                        # Fetch funding rate
                        funding_endpoint = "/api/v5/public/funding-rate"
                        funding_params = {"instId": inst_id}
                        funding_response = await self._request(
                            "GET", funding_endpoint, funding_params
                        )

                        funding_data_array = funding_response.get("data", [])
                        if funding_data_array:
                            funding_data = funding_data_array[0]
                            funding_rate = Decimal(funding_data["fundingRate"])

                            if "nextFundingTime" in funding_data:
                                next_funding_ms = int(funding_data["nextFundingTime"])
                                next_funding_time = datetime.fromtimestamp(
                                    next_funding_ms / 1000, tz=timezone.utc
                                )

                except Exception as e:
                    logger.warning(
                        "rest_mark_price_fetch_failed",
                        exchange="okx",
                        instrument=instrument,
                        error=str(e),
                    )

            ticker = TickerSnapshot(
                exchange="okx",
                instrument=instrument,
                timestamp=timestamp,
                last_price=last_price,
                mark_price=mark_price,
                index_price=index_price,
                volume_24h=volume_24h,
                volume_24h_usd=volume_24h_usd,
                high_24h=high_24h,
                low_24h=low_24h,
                funding_rate=funding_rate,
                next_funding_time=next_funding_time,
            )

            logger.debug(
                "rest_ticker_fetched",
                exchange="okx",
                instrument=instrument,
                inst_id=inst_id,
                last_price=str(last_price),
            )

            return ticker

        except KeyError as e:
            logger.error(
                "rest_ticker_parse_error",
                exchange="okx",
                instrument=instrument,
                inst_id=inst_id,
                missing_field=str(e),
            )
            raise ValueError(f"Invalid ticker response: missing {e}")

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"OKXRestClient(base_url={self.base_url}, "
            f"rate_limit={self.rate_limit_per_second}/s)"
        )
