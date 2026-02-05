"""
OKX data normalizer.

Converts OKX-specific JSON formats to our unified Pydantic models.
Handles both perpetual swaps and spot market data.

OKX Order Book Format:
    {
        "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
        "action": "snapshot",  // or "update"
        "data": [
            {
                "asks": [["50001.0", "1.5", "0", "2"], ...],
                "bids": [["50000.0", "2.0", "0", "3"], ...],
                "ts": "1234567890123",
                "checksum": -123456789,
                "seqId": 123456789
            }
        ]
    }

Price Level Format:
    [price, quantity, deprecated, num_orders]
    We only need price and quantity.

OKX Ticker Format:
    {
        "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "last": "50000.0",
                "lastSz": "0.5",
                "askPx": "50001.0",
                "bidPx": "50000.0",
                "high24h": "51000.0",
                "low24h": "49000.0",
                "vol24h": "1000.5",
                "volCcy24h": "50025000",
                "ts": "1234567890123"
            }
        ]
    }

OKX Mark Price Format (perpetuals):
    {
        "arg": {"channel": "mark-price", "instId": "BTC-USDT-SWAP"},
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "markPx": "50001.5",
                "idxPx": "49999.0",
                "fundingRate": "0.0001",
                "nextFundingTime": "1234567890123",
                "ts": "1234567890123"
            }
        ]
    }

Instrument ID Mapping:
    OKX Format → Our Format
    BTC-USDT-SWAP → BTC-USDT-PERP
    BTC-USDT → BTC-USDT-SPOT
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog

from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class OKXNormalizer:
    """
    Normalizes OKX data to unified models.

    Handles conversion from OKX-specific formats to our standardized
    OrderBookSnapshot and TickerSnapshot models. All financial values are
    converted to Decimal for precision.

    Example:
        >>> normalizer = OKXNormalizer()
        >>> snapshot = normalizer.normalize_orderbook(
        ...     raw_message=okx_books_message,
        ...     instrument="BTC-USDT-PERP"
        ... )
        >>> print(f"Spread: {snapshot.spread_bps} bps")
    """

    # Instrument ID mapping: OKX format -> Our format
    INSTRUMENT_MAPPING = {
        "BTC-USDT-SWAP": "BTC-USDT-PERP",
        "BTC-USDT": "BTC-USDT-SPOT",
    }

    # Reverse mapping: Our format -> OKX format
    REVERSE_INSTRUMENT_MAPPING = {v: k for k, v in INSTRUMENT_MAPPING.items()}

    @staticmethod
    def normalize_instrument_id(okx_inst_id: str) -> str:
        """
        Normalize OKX instrument ID to our format.

        Args:
            okx_inst_id: OKX instrument ID (e.g., "BTC-USDT-SWAP").

        Returns:
            str: Normalized instrument ID (e.g., "BTC-USDT-PERP").

        Example:
            >>> OKXNormalizer.normalize_instrument_id("BTC-USDT-SWAP")
            'BTC-USDT-PERP'
        """
        return OKXNormalizer.INSTRUMENT_MAPPING.get(okx_inst_id, okx_inst_id)

    @staticmethod
    def to_okx_instrument_id(normalized_id: str) -> str:
        """
        Convert our normalized instrument ID to OKX format.

        Args:
            normalized_id: Our instrument ID (e.g., "BTC-USDT-PERP").

        Returns:
            str: OKX instrument ID (e.g., "BTC-USDT-SWAP").

        Example:
            >>> OKXNormalizer.to_okx_instrument_id("BTC-USDT-PERP")
            'BTC-USDT-SWAP'
        """
        return OKXNormalizer.REVERSE_INSTRUMENT_MAPPING.get(
            normalized_id, normalized_id
        )

    @staticmethod
    def normalize_orderbook(
        raw_message: Dict[str, Any],
        instrument: str,
    ) -> OrderBookSnapshot:
        """
        Normalize OKX order book update to OrderBookSnapshot.

        Args:
            raw_message: Raw OKX books channel message.
            instrument: Normalized instrument ID (e.g., "BTC-USDT-PERP").

        Returns:
            OrderBookSnapshot: Normalized order book.

        Raises:
            ValueError: If message format is invalid.
            KeyError: If required fields are missing.

        Example:
            >>> snapshot = OKXNormalizer.normalize_orderbook(
            ...     raw_message={
            ...         "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
            ...         "data": [{
            ...             "asks": [["50001.0", "1.5", "0", "2"]],
            ...             "bids": [["50000.0", "2.0", "0", "3"]],
            ...             "ts": "1234567890123",
            ...             "seqId": 123456789
            ...         }]
            ...     },
            ...     instrument="BTC-USDT-PERP"
            ... )
        """
        try:
            # Extract data array (should have one element)
            data_array = raw_message["data"]
            if not data_array:
                raise ValueError("Empty data array in OKX message")

            data = data_array[0]

            # Extract timestamp (milliseconds string to datetime)
            timestamp_ms = int(data["ts"])
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            local_timestamp = datetime.now(timezone.utc)

            # Extract sequence ID (seqId)
            sequence_id = int(data["seqId"])

            # Parse bids (sorted descending - best first)
            # OKX format: [price, quantity, deprecated, num_orders]
            bids: List[PriceLevel] = []
            for level in data.get("bids", []):
                price = Decimal(level[0])
                quantity = Decimal(level[1])
                # Skip zero quantity levels
                if quantity > 0:
                    bids.append(PriceLevel(price=price, quantity=quantity))

            # Parse asks (sorted ascending - best first)
            asks: List[PriceLevel] = []
            for level in data.get("asks", []):
                price = Decimal(level[0])
                quantity = Decimal(level[1])
                # Skip zero quantity levels
                if quantity > 0:
                    asks.append(PriceLevel(price=price, quantity=quantity))

            # OKX sends data pre-sorted, but ensure it
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            depth_levels = max(len(bids), len(asks))

            snapshot = OrderBookSnapshot(
                exchange="okx",
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
                exchange="okx",
                instrument=instrument,
                sequence_id=sequence_id,
                bids_count=len(bids),
                asks_count=len(asks),
            )

            return snapshot

        except KeyError as e:
            logger.error(
                "orderbook_normalization_failed_missing_field",
                exchange="okx",
                instrument=instrument,
                missing_field=str(e),
                message=raw_message,
            )
            raise ValueError(f"Missing required field in OKX message: {e}")
        except (ValueError, TypeError, IndexError) as e:
            logger.error(
                "orderbook_normalization_failed_invalid_data",
                exchange="okx",
                instrument=instrument,
                error=str(e),
                message=raw_message,
            )
            raise ValueError(f"Invalid data in OKX message: {e}")

    @staticmethod
    def normalize_ticker(
        raw_ticker: Dict[str, Any],
        raw_mark_price: Optional[Dict[str, Any]],
        instrument: str,
    ) -> TickerSnapshot:
        """
        Normalize OKX ticker data to TickerSnapshot.

        Combines ticker stream and mark price stream (for perpetuals).
        For spot, only ticker stream is used.

        Args:
            raw_ticker: Raw OKX tickers channel message data.
            raw_mark_price: Raw OKX mark-price channel message data (perpetuals only).
            instrument: Normalized instrument ID.

        Returns:
            TickerSnapshot: Normalized ticker.

        Raises:
            ValueError: If message format is invalid.

        Example:
            >>> ticker = OKXNormalizer.normalize_ticker(
            ...     raw_ticker={...},
            ...     raw_mark_price={...},  # None for spot
            ...     instrument="BTC-USDT-PERP"
            ... )
        """
        try:
            # Extract timestamp from ticker
            timestamp_ms = int(raw_ticker["ts"])
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            # Core prices from ticker
            last_price = Decimal(raw_ticker["last"])
            high_24h = Decimal(raw_ticker["high24h"])
            low_24h = Decimal(raw_ticker["low24h"])
            volume_24h = Decimal(raw_ticker["vol24h"])
            volume_24h_usd = Decimal(raw_ticker["volCcy24h"])

            # Derivatives-specific data from mark price stream
            mark_price: Optional[Decimal] = None
            index_price: Optional[Decimal] = None
            funding_rate: Optional[Decimal] = None
            next_funding_time: Optional[datetime] = None

            if raw_mark_price:
                mark_price = Decimal(raw_mark_price["markPx"])
                index_price = Decimal(raw_mark_price["idxPx"])
                funding_rate = Decimal(raw_mark_price["fundingRate"])

                # Next funding time
                if "nextFundingTime" in raw_mark_price:
                    next_funding_ms = int(raw_mark_price["nextFundingTime"])
                    next_funding_time = datetime.fromtimestamp(
                        next_funding_ms / 1000, tz=timezone.utc
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
                "normalized_ticker",
                exchange="okx",
                instrument=instrument,
                last_price=str(last_price),
                mark_price=str(mark_price) if mark_price else None,
            )

            return ticker

        except KeyError as e:
            logger.error(
                "ticker_normalization_failed_missing_field",
                exchange="okx",
                instrument=instrument,
                missing_field=str(e),
            )
            raise ValueError(f"Missing required field in OKX ticker: {e}")
        except (ValueError, TypeError) as e:
            logger.error(
                "ticker_normalization_failed_invalid_data",
                exchange="okx",
                instrument=instrument,
                error=str(e),
            )
            raise ValueError(f"Invalid data in OKX ticker: {e}")
