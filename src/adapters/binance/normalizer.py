"""
Binance data normalizer.

Converts Binance-specific JSON formats to our unified Pydantic models.
Handles both perpetual futures and spot market data.

Binance Order Book Formats:

    Futures / Spot diff-depth stream (has "e": "depthUpdate"):
        {
            "e": "depthUpdate",
            "E": 1234567890,  # Event time (ms)
            "s": "BTCUSDT",   # Symbol
            "U": 157,         # First update ID
            "u": 160,         # Final update ID (lastUpdateId)
            "b": [["50000.00", "1.5"], ...],  # Bids
            "a": [["50001.00", "1.2"], ...]   # Asks
        }

    Spot partial depth stream  (@depth<N>@<speed>, e.g. btcusdt@depth20@100ms):
        {
            "lastUpdateId": 160,
            "bids": [["50000.00", "1.5"], ...],
            "asks": [["50001.00", "1.2"], ...]
        }
        NOTE: This format contains NO "e" field, NO symbol ("s"), and NO
        event timestamp ("E").  The symbol must be supplied by the caller
        (derived from the stream name or subscription context), and the
        local receipt time is used in place of the missing server timestamp.

Binance Ticker Format (24hr):
    {
        "e": "24hrTicker",
        "E": 1234567890,
        "s": "BTCUSDT",
        "c": "50000.00",  # Last price
        "v": "1000.5",    # Volume (base)
        "q": "50025000",  # Volume (quote/USD)
        "h": "51000.00",  # High
        "l": "49000.00"   # Low
    }

Binance Mark Price Format (perpetuals only):
    {
        "e": "markPriceUpdate",
        "E": 1234567890,
        "s": "BTCUSDT",
        "p": "50001.50",  # Mark price
        "i": "49999.00",  # Index price
        "r": "0.0001"     # Funding rate
    }
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog

from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class BinanceNormalizer:
    """
    Normalizes Binance data to unified models.

    Handles conversion from Binance-specific formats to our standardized
    OrderBookSnapshot and TickerSnapshot models. All financial values are
    converted to Decimal for precision.

    Example:
        >>> normalizer = BinanceNormalizer()
        >>> snapshot = normalizer.normalize_orderbook(
        ...     raw_message=binance_depth_update,
        ...     instrument="BTC-USDT-PERP",
        ...     instrument_type="perpetual"
        ... )
        >>> print(f"Spread: {snapshot.spread_bps} bps")
    """

    @staticmethod
    def _is_spot_partial_depth(raw_message: Dict[str, Any]) -> bool:
        """
        Determine whether a raw message is a Spot partial depth snapshot.

        Spot partial depth messages (e.g. btcusdt@depth20@100ms on the spot
        endpoint) have "lastUpdateId", "bids", and "asks" at the top level
        and do NOT contain an "e" event-type field.  This distinguishes them
        from the futures / diff-depth "depthUpdate" messages that do carry "e".

        Args:
            raw_message: Parsed JSON message from the WebSocket.

        Returns:
            bool: True if the message matches the spot partial depth format.
        """
        return (
            "e" not in raw_message
            and "lastUpdateId" in raw_message
            and "bids" in raw_message
            and "asks" in raw_message
        )

    @staticmethod
    def normalize_orderbook(
        raw_message: Dict[str, Any],
        instrument: str,
        instrument_type: str,
    ) -> OrderBookSnapshot:
        """
        Normalize Binance order book update to OrderBookSnapshot.

        Supports two wire formats transparently:

        1. Futures / diff-depth ("depthUpdate") format:
               Keys: "e", "E", "s", "U", "u", "b", "a"
        2. Spot partial depth (@depth<N>@<speed>) format:
               Keys: "lastUpdateId", "bids", "asks"
               No "e", no "s", no "E".

        Args:
            raw_message: Raw Binance message (either format).
            instrument: Normalized instrument ID (e.g., "BTC-USDT-PERP").
            instrument_type: "perpetual" or "spot".

        Returns:
            OrderBookSnapshot: Normalized order book.

        Raises:
            ValueError: If message format is invalid or required fields
                are missing.

        Example:
            >>> # Futures depthUpdate
            >>> snapshot = BinanceNormalizer.normalize_orderbook(
            ...     raw_message={
            ...         "e": "depthUpdate",
            ...         "E": 1234567890,
            ...         "s": "BTCUSDT",
            ...         "u": 160,
            ...         "b": [["50000.00", "1.5"]],
            ...         "a": [["50001.00", "1.2"]]
            ...     },
            ...     instrument="BTC-USDT-PERP",
            ...     instrument_type="perpetual"
            ... )
            >>> # Spot partial depth
            >>> snapshot = BinanceNormalizer.normalize_orderbook(
            ...     raw_message={
            ...         "lastUpdateId": 160,
            ...         "bids": [["50000.00", "1.5"]],
            ...         "asks": [["50001.00", "1.2"]]
            ...     },
            ...     instrument="BTC-USDT-SPOT",
            ...     instrument_type="spot"
            ... )
        """
        try:
            local_timestamp = datetime.now(timezone.utc)
            is_partial_depth = BinanceNormalizer._is_spot_partial_depth(raw_message)

            if is_partial_depth:
                # --- Spot partial depth format ---
                # No server-side event timestamp; use local receipt time.
                timestamp = local_timestamp
                sequence_id = raw_message["lastUpdateId"]
                raw_bids = raw_message["bids"]
                raw_asks = raw_message["asks"]

                logger.debug(
                    "normalizing_spot_partial_depth",
                    instrument=instrument,
                    sequence_id=sequence_id,
                )
            else:
                # --- Futures / diff-depth ("depthUpdate") format ---
                timestamp_ms = raw_message["E"]
                timestamp = datetime.fromtimestamp(
                    timestamp_ms / 1000, tz=timezone.utc
                )
                sequence_id = raw_message["u"]
                raw_bids = raw_message.get("b", [])
                raw_asks = raw_message.get("a", [])

            # Parse bids (sorted descending - best first)
            bids: List[PriceLevel] = []
            for price_str, qty_str in raw_bids:
                price = Decimal(price_str)
                quantity = Decimal(qty_str)
                # Skip zero quantity levels
                if quantity > 0:
                    bids.append(PriceLevel(price=price, quantity=quantity))

            # Parse asks (sorted ascending - best first)
            asks: List[PriceLevel] = []
            for price_str, qty_str in raw_asks:
                price = Decimal(price_str)
                quantity = Decimal(qty_str)
                # Skip zero quantity levels
                if quantity > 0:
                    asks.append(PriceLevel(price=price, quantity=quantity))

            # Binance sends data pre-sorted, but ensure it
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            depth_levels = max(len(bids), len(asks))

            snapshot = OrderBookSnapshot(
                exchange="binance",
                instrument=instrument,
                timestamp=timestamp,
                local_timestamp=local_timestamp,
                sequence_id=sequence_id,
                bids=bids,
                asks=asks,
                depth_levels=depth_levels,
            )

            logger.debug(
                "normalized_orderbook",
                instrument=instrument,
                sequence_id=sequence_id,
                bids_count=len(bids),
                asks_count=len(asks),
            )

            return snapshot

        except KeyError as e:
            logger.error(
                "orderbook_normalization_failed_missing_field",
                instrument=instrument,
                missing_field=str(e),
                message=raw_message,
            )
            raise ValueError(f"Missing required field in Binance message: {e}")
        except (ValueError, TypeError) as e:
            logger.error(
                "orderbook_normalization_failed_invalid_data",
                instrument=instrument,
                error=str(e),
                message=raw_message,
            )
            raise ValueError(f"Invalid data in Binance message: {e}")

    @staticmethod
    def normalize_ticker(
        raw_24hr_ticker: Dict[str, Any],
        raw_mark_price: Optional[Dict[str, Any]],
        instrument: str,
    ) -> TickerSnapshot:
        """
        Normalize Binance ticker data to TickerSnapshot.

        Combines 24hr ticker stream and mark price stream (for perpetuals).
        For spot, only 24hr ticker is used.

        Args:
            raw_24hr_ticker: Raw Binance 24hrTicker message.
            raw_mark_price: Raw Binance markPriceUpdate message (perpetuals only).
            instrument: Normalized instrument ID.

        Returns:
            TickerSnapshot: Normalized ticker.

        Raises:
            ValueError: If message format is invalid.

        Example:
            >>> ticker = BinanceNormalizer.normalize_ticker(
            ...     raw_24hr_ticker={...},
            ...     raw_mark_price={...},  # None for spot
            ...     instrument="BTC-USDT-PERP"
            ... )
        """
        try:
            # Extract timestamp from 24hr ticker
            timestamp_ms = raw_24hr_ticker["E"]
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            # Core prices from 24hr ticker
            last_price = Decimal(raw_24hr_ticker["c"])
            high_24h = Decimal(raw_24hr_ticker["h"])
            low_24h = Decimal(raw_24hr_ticker["l"])
            volume_24h = Decimal(raw_24hr_ticker["v"])
            volume_24h_usd = Decimal(raw_24hr_ticker["q"])

            # Derivatives-specific data from mark price stream
            mark_price: Optional[Decimal] = None
            index_price: Optional[Decimal] = None
            funding_rate: Optional[Decimal] = None
            next_funding_time: Optional[datetime] = None

            if raw_mark_price:
                mark_price = Decimal(raw_mark_price["p"])
                index_price = Decimal(raw_mark_price["i"])
                funding_rate = Decimal(raw_mark_price["r"])

                # Next funding time from mark price message
                if "T" in raw_mark_price:
                    next_funding_ms = raw_mark_price["T"]
                    next_funding_time = datetime.fromtimestamp(
                        next_funding_ms / 1000, tz=timezone.utc
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
                next_funding_time=next_funding_time,
            )

            logger.debug(
                "normalized_ticker",
                instrument=instrument,
                last_price=str(last_price),
                mark_price=str(mark_price) if mark_price else None,
            )

            return ticker

        except KeyError as e:
            logger.error(
                "ticker_normalization_failed_missing_field",
                instrument=instrument,
                missing_field=str(e),
            )
            raise ValueError(f"Missing required field in Binance ticker: {e}")
        except (ValueError, TypeError) as e:
            logger.error(
                "ticker_normalization_failed_invalid_data",
                instrument=instrument,
                error=str(e),
            )
            raise ValueError(f"Invalid data in Binance ticker: {e}")

    @staticmethod
    def parse_instrument_type(symbol: str, ws_endpoint: str) -> str:
        """
        Determine instrument type from symbol and WebSocket endpoint.

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT").
            ws_endpoint: WebSocket endpoint URL.

        Returns:
            str: "perpetual" or "spot".
        """
        if "fstream.binance.com" in ws_endpoint:
            return "perpetual"
        elif "stream.binance.com" in ws_endpoint:
            return "spot"
        else:
            # Default to spot
            return "spot"
