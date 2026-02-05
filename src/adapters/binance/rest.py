"""
Binance REST API client.

Provides fallback data fetching via REST API when WebSocket is unavailable.
Implements rate limiting to avoid exchange bans.

Endpoints:
    Perpetuals: https://fapi.binance.com/fapi/v1/depth
    Spot: https://api.binance.com/api/v3/depth

Rate Limits:
    - Binance: 2400 requests per minute (40 per second)
    - Conservative default: 10 per second with token bucket

Response Format:
    {
        "lastUpdateId": 1234567890,
        "bids": [["50000.00", "1.5"], ...],
        "asks": [["50001.00", "1.2"], ...]
    }
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import aiohttp
import structlog

from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""

    pass


class BinanceRestClient:
    """
    Async REST API client for Binance.

    Provides fallback order book and ticker fetching via REST API.
    Implements token bucket rate limiting.

    Attributes:
        base_url: REST API base URL.
        rate_limit_per_second: Maximum requests per second.
        session: aiohttp ClientSession.

    Example:
        >>> client = BinanceRestClient(
        ...     base_url="https://fapi.binance.com",
        ...     rate_limit_per_second=10
        ... )
        >>> snapshot = await client.get_orderbook("BTCUSDT", limit=20)
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
            logger.debug("rest_client_session_closed", base_url=self.base_url)

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
            Dict[str, Any]: Parsed JSON response.

        Raises:
            RateLimitError: If rate limited by exchange.
            ConnectionError: If request fails.
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
                        url=url,
                        retry_after=retry_after,
                    )
                    raise RateLimitError(f"Rate limited, retry after {retry_after}s")

                # Check for errors
                if response.status >= 400:
                    error_text = await response.text()
                    logger.error(
                        "rest_request_failed",
                        url=url,
                        status=response.status,
                        error=error_text,
                    )
                    raise ConnectionError(
                        f"REST request failed with status {response.status}: {error_text}"
                    )

                data = await response.json()
                return data

        except aiohttp.ClientError as e:
            logger.error("rest_client_error", url=url, error=str(e))
            raise ConnectionError(f"REST request failed: {e}")
        except asyncio.TimeoutError:
            logger.error("rest_timeout", url=url, timeout=self.timeout_seconds)
            raise ConnectionError(f"REST request timeout after {self.timeout_seconds}s")

    async def get_orderbook(
        self,
        symbol: str,
        limit: int = 20,
        instrument: str = "UNKNOWN",
    ) -> OrderBookSnapshot:
        """
        Fetch order book snapshot via REST API.

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT").
            limit: Number of levels to fetch (5, 10, 20, 50, 100, 500, 1000).
            instrument: Normalized instrument ID for logging.

        Returns:
            OrderBookSnapshot: Current order book state.

        Raises:
            ValueError: If response format is invalid.
            ConnectionError: If request fails.

        Example:
            >>> snapshot = await client.get_orderbook("BTCUSDT", limit=20)
        """
        # Determine endpoint based on base URL
        if "fapi.binance.com" in self.base_url:
            endpoint = "/fapi/v1/depth"
        else:  # spot
            endpoint = "/api/v3/depth"

        params = {"symbol": symbol.upper(), "limit": limit}

        try:
            data = await self._request("GET", endpoint, params)

            # Parse response
            timestamp = datetime.now(timezone.utc)
            sequence_id = data["lastUpdateId"]

            # Parse bids
            bids = [
                PriceLevel(price=Decimal(price), quantity=Decimal(qty))
                for price, qty in data.get("bids", [])
                if Decimal(qty) > 0
            ]

            # Parse asks
            asks = [
                PriceLevel(price=Decimal(price), quantity=Decimal(qty))
                for price, qty in data.get("asks", [])
                if Decimal(qty) > 0
            ]

            # Ensure sorted
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            snapshot = OrderBookSnapshot(
                exchange="binance",
                instrument=instrument,
                timestamp=timestamp,
                local_timestamp=timestamp,
                sequence_id=sequence_id,
                bids=bids,
                asks=asks,
                depth_levels=limit,
            )

            logger.debug(
                "rest_orderbook_fetched",
                instrument=instrument,
                symbol=symbol,
                sequence_id=sequence_id,
                bids_count=len(bids),
                asks_count=len(asks),
            )

            return snapshot

        except KeyError as e:
            logger.error(
                "rest_orderbook_parse_error",
                instrument=instrument,
                symbol=symbol,
                missing_field=str(e),
            )
            raise ValueError(f"Invalid order book response: missing {e}")

    async def get_ticker(
        self,
        symbol: str,
        instrument: str = "UNKNOWN",
    ) -> TickerSnapshot:
        """
        Fetch ticker snapshot via REST API.

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT").
            instrument: Normalized instrument ID.

        Returns:
            TickerSnapshot: Current ticker state.

        Raises:
            ValueError: If response format is invalid.
            ConnectionError: If request fails.

        Example:
            >>> ticker = await client.get_ticker("BTCUSDT")
        """
        # Determine endpoints based on base URL
        if "fapi.binance.com" in self.base_url:
            ticker_endpoint = "/fapi/v1/ticker/24hr"
            mark_price_endpoint = "/fapi/v1/premiumIndex"
        else:  # spot
            ticker_endpoint = "/api/v3/ticker/24hr"
            mark_price_endpoint = None

        params = {"symbol": symbol.upper()}

        try:
            # Fetch 24hr ticker
            ticker_data = await self._request("GET", ticker_endpoint, params)

            timestamp = datetime.now(timezone.utc)
            last_price = Decimal(ticker_data["lastPrice"])
            high_24h = Decimal(ticker_data["highPrice"])
            low_24h = Decimal(ticker_data["lowPrice"])
            volume_24h = Decimal(ticker_data["volume"])
            volume_24h_usd = Decimal(ticker_data["quoteVolume"])

            # Fetch mark price for perpetuals
            mark_price: Optional[Decimal] = None
            index_price: Optional[Decimal] = None
            funding_rate: Optional[Decimal] = None

            if mark_price_endpoint:
                try:
                    mark_data = await self._request("GET", mark_price_endpoint, params)
                    mark_price = Decimal(mark_data["markPrice"])
                    index_price = Decimal(mark_data["indexPrice"])
                    funding_rate = Decimal(mark_data["lastFundingRate"])
                except Exception as e:
                    logger.warning(
                        "rest_mark_price_fetch_failed",
                        instrument=instrument,
                        error=str(e),
                    )

            ticker = TickerSnapshot(
                exchange="binance",
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
                next_funding_time=None,  # Not easily available via REST
            )

            logger.debug(
                "rest_ticker_fetched",
                instrument=instrument,
                symbol=symbol,
                last_price=str(last_price),
            )

            return ticker

        except KeyError as e:
            logger.error(
                "rest_ticker_parse_error",
                instrument=instrument,
                symbol=symbol,
                missing_field=str(e),
            )
            raise ValueError(f"Invalid ticker response: missing {e}")

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"BinanceRestClient(base_url={self.base_url}, "
            f"rate_limit={self.rate_limit_per_second}/s)"
        )
