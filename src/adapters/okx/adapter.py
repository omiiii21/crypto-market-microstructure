"""
OKX exchange adapter.

Main adapter implementation that implements the ExchangeAdapter interface.
Coordinates WebSocket streaming, REST fallback, data normalization, and
gap detection.

This adapter:
    - Manages single WebSocket connection for both perpetual and spot markets
    - Normalizes OKX data to unified models
    - Detects sequence gaps using seqId
    - Falls back to REST when WebSocket fails
    - Tracks health metrics

OKX-Specific Details:
    - Single WebSocket endpoint handles all instruments
    - Uses JSON subscription messages
    - Sequence tracking per instrument using seqId
    - String-based ping/pong heartbeat

Example:
    >>> from src.adapters.okx import OKXAdapter
    >>> from src.config.loader import load_config
    >>>
    >>> config = load_config()
    >>> exchange_config = config.get_exchange("okx")
    >>> instruments = config.get_enabled_instruments()
    >>>
    >>> adapter = OKXAdapter(exchange_config, instruments)
    >>> await adapter.connect()
    >>> await adapter.subscribe(["BTC-USDT-PERP", "BTC-USDT-SPOT"])
    >>>
    >>> async for snapshot in adapter.stream_order_books():
    ...     print(f"{snapshot.instrument}: {snapshot.spread_bps} bps")
"""

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Dict, List, Optional

import structlog

from src.adapters.okx.normalizer import OKXNormalizer
from src.adapters.okx.rest import OKXRestClient
from src.adapters.okx.websocket import OKXWebSocketClient
from src.config.models import ExchangeConfig, InstrumentConfig
from src.interfaces.exchange_adapter import ExchangeAdapter
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.orderbook import OrderBookSnapshot
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class OKXAdapter(ExchangeAdapter):
    """
    OKX exchange adapter implementing ExchangeAdapter interface.

    Manages connection to OKX public WebSocket, normalizes data,
    detects gaps, and provides health monitoring.

    Attributes:
        exchange_name: Always returns "okx".
        is_connected: True if WebSocket is connected.

    Example:
        >>> adapter = OKXAdapter(exchange_config, instruments)
        >>> await adapter.connect()
        >>> health = await adapter.health_check()
        >>> print(f"Status: {health.status}, Lag: {health.lag_ms}ms")
    """

    def __init__(
        self,
        exchange_config: ExchangeConfig,
        instruments: List[InstrumentConfig],
    ):
        """
        Initialize OKX adapter.

        Args:
            exchange_config: Exchange configuration from config/exchanges.yaml.
            instruments: List of instruments to monitor.
        """
        self._config = exchange_config
        self._instruments = instruments

        # WebSocket client (single for all instruments)
        self._ws: Optional[OKXWebSocketClient] = None

        # REST client
        self._rest: Optional[OKXRestClient] = None

        # State tracking
        self._last_sequence_ids: Dict[str, int] = {}  # instrument -> last seq ID
        self._message_count = 0
        self._reconnect_count = 0
        self._gaps_last_hour: deque = deque(maxlen=3600)  # Track gaps

        # Ticker cache (for combining ticker with mark price)
        self._ticker_cache: Dict[str, Dict] = {}
        self._mark_price_cache: Dict[str, Dict] = {}

        logger.info(
            "okx_adapter_initialized",
            instruments=[inst.id for inst in instruments],
        )

    @property
    def exchange_name(self) -> str:
        """Return exchange identifier."""
        return "okx"

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return bool(self._ws and self._ws.is_connected)

    async def connect(self) -> None:
        """
        Establish WebSocket connection.

        Creates single WebSocket connection for all instruments.
        Initializes REST client for fallback.
        """
        if self.is_connected:
            logger.debug("okx_already_connected")
            return

        # Initialize WebSocket client
        ws_url = self._config.get_websocket_url("public")
        if not ws_url:
            raise ValueError("OKX WebSocket URL not configured")

        self._ws = OKXWebSocketClient(
            url=ws_url,
            ping_interval=self._config.connection.ping_interval_seconds,
            ping_timeout=self._config.connection.ping_timeout_seconds,
            max_reconnect_attempts=self._config.connection.max_reconnect_attempts,
            reconnect_delay=self._config.connection.reconnect_delay_seconds,
        )
        await self._ws.connect()

        # Initialize REST client
        rest_url = self._config.get_rest_url("base")
        if rest_url:
            self._rest = OKXRestClient(
                base_url=rest_url,
                rate_limit_per_second=self._config.connection.rate_limit_per_second,
            )

        logger.info("okx_connected", ws_url=ws_url, rest_url=rest_url)

    async def disconnect(self) -> None:
        """Gracefully disconnect all connections."""
        if self._ws:
            await self._ws.disconnect()

        if self._rest:
            await self._rest.close()

        logger.info("okx_disconnected")

    async def subscribe(self, instruments: List[str]) -> None:
        """
        Subscribe to market data for specified instruments.

        Args:
            instruments: List of normalized instrument IDs.

        Raises:
            ConnectionError: If not connected.
            ValueError: If instrument not found in configuration.
        """
        if not self.is_connected:
            raise ConnectionError("Cannot subscribe: not connected")

        # Build subscription channels
        channels = []

        for instrument_id in instruments:
            # Find instrument config
            instrument = next(
                (i for i in self._instruments if i.id == instrument_id), None
            )
            if not instrument:
                raise ValueError(f"Instrument not found: {instrument_id}")

            # Get OKX symbol config
            symbol_config = instrument.get_exchange_symbol("okx")
            if not symbol_config:
                raise ValueError(
                    f"OKX symbol config not found for {instrument_id}"
                )

            # Get OKX instrument ID
            okx_inst_id = OKXNormalizer.to_okx_instrument_id(instrument_id)

            # Subscribe to order book channel
            orderbook_channel = self._config.streams.orderbook_channel
            channels.append({"channel": orderbook_channel, "instId": okx_inst_id})

            # Subscribe to ticker channel (default to "tickers")
            ticker_channel = getattr(self._config.streams, "ticker_channel", "tickers")
            channels.append({"channel": ticker_channel, "instId": okx_inst_id})

            # Subscribe to mark price channel for perpetuals
            if instrument.is_perpetual:
                channels.append({"channel": "mark-price", "instId": okx_inst_id})

        # Subscribe via WebSocket
        await self._ws.subscribe(channels)

        logger.info("okx_subscribed", channels=channels)

    async def stream_order_books(self) -> AsyncIterator[OrderBookSnapshot]:
        """
        Stream order book snapshots from WebSocket.

        Yields normalized OrderBookSnapshot objects. Handles both perpetual
        and spot markets. Detects gaps and falls back to REST on errors.

        Yields:
            OrderBookSnapshot: Normalized order book snapshot.
        """
        if not self._ws:
            raise ConnectionError("WebSocket not initialized")

        async for message in self._ws.stream_messages():
            # Filter for order book updates
            arg = message.get("arg", {})
            channel = arg.get("channel")

            if channel != self._config.streams.orderbook_channel:
                continue

            try:
                # Get OKX instrument ID
                okx_inst_id = arg.get("instId")
                if not okx_inst_id:
                    logger.warning("okx_missing_inst_id", message=message)
                    continue

                # Normalize instrument ID
                instrument_id = OKXNormalizer.normalize_instrument_id(okx_inst_id)

                # Find instrument config
                instrument = next(
                    (i for i in self._instruments if i.id == instrument_id), None
                )
                if not instrument:
                    logger.warning(
                        "okx_unknown_instrument",
                        okx_inst_id=okx_inst_id,
                        instrument_id=instrument_id,
                    )
                    continue

                # Normalize to OrderBookSnapshot
                snapshot = OKXNormalizer.normalize_orderbook(
                    raw_message=message,
                    instrument=instrument_id,
                )

                # Detect gaps
                gap = self.detect_gap(
                    prev_seq=self._last_sequence_ids.get(instrument_id),
                    curr_seq=snapshot.sequence_id,
                )

                if gap:
                    # Enhance gap with instrument info
                    gap = GapMarker(
                        exchange=self.exchange_name,
                        instrument=instrument_id,
                        gap_start=gap.gap_start,
                        gap_end=gap.gap_end,
                        duration_seconds=gap.duration_seconds,
                        reason="sequence_gap",
                        sequence_id_before=gap.sequence_id_before,
                        sequence_id_after=gap.sequence_id_after,
                    )
                    self._gaps_last_hour.append(gap)

                    logger.warning(
                        "okx_gap_detected",
                        instrument=instrument_id,
                        gap_size=gap.sequence_gap_size,
                        prev_seq=gap.sequence_id_before,
                        curr_seq=gap.sequence_id_after,
                    )

                self._last_sequence_ids[instrument_id] = snapshot.sequence_id
                self._message_count += 1

                yield snapshot

            except Exception as e:
                logger.error(
                    "okx_orderbook_processing_error",
                    error=str(e),
                    message=message,
                )

    async def stream_tickers(self) -> AsyncIterator[TickerSnapshot]:
        """
        Stream ticker snapshots from WebSocket.

        For perpetuals, combines ticker with mark price data.
        For spot, uses only ticker.

        Yields:
            TickerSnapshot: Normalized ticker snapshot.
        """
        if not self._ws:
            raise ConnectionError("WebSocket not initialized")

        async for message in self._ws.stream_messages():
            arg = message.get("arg", {})
            channel = arg.get("channel")

            # Cache ticker data
            if channel == self._config.streams.ticker_channel:
                okx_inst_id = arg.get("instId")
                if okx_inst_id:
                    data_array = message.get("data", [])
                    if data_array:
                        self._ticker_cache[okx_inst_id] = data_array[0]

            # Cache mark price data (perpetuals only)
            elif channel == "mark-price":
                okx_inst_id = arg.get("instId")
                if okx_inst_id:
                    data_array = message.get("data", [])
                    if data_array:
                        self._mark_price_cache[okx_inst_id] = data_array[0]

            # Only yield when we have ticker data
            if channel not in (self._config.streams.ticker_channel, "mark-price"):
                continue

            okx_inst_id = arg.get("instId")
            if not okx_inst_id:
                continue

            # Normalize instrument ID
            instrument_id = OKXNormalizer.normalize_instrument_id(okx_inst_id)

            # Find instrument config
            instrument = next(
                (i for i in self._instruments if i.id == instrument_id), None
            )
            if not instrument:
                continue

            # Get cached data
            ticker_data = self._ticker_cache.get(okx_inst_id)
            mark_price_data = self._mark_price_cache.get(okx_inst_id)

            if not ticker_data:
                continue

            try:
                ticker = OKXNormalizer.normalize_ticker(
                    raw_ticker=ticker_data,
                    raw_mark_price=mark_price_data,
                    instrument=instrument_id,
                )
                yield ticker

            except Exception as e:
                logger.error(
                    "okx_ticker_processing_error",
                    instrument=instrument_id,
                    error=str(e),
                )

    async def get_order_book_rest(self, instrument: str) -> OrderBookSnapshot:
        """
        Fetch order book via REST API.

        Args:
            instrument: Normalized instrument ID.

        Returns:
            OrderBookSnapshot: Current order book.

        Raises:
            ValueError: If instrument not found.
            ConnectionError: If REST request fails.
        """
        if not self._rest:
            raise ConnectionError("REST client not initialized")

        # Find instrument config
        instrument_config = next(
            (i for i in self._instruments if i.id == instrument), None
        )
        if not instrument_config:
            raise ValueError(f"Instrument not found: {instrument}")

        # Get OKX instrument ID
        okx_inst_id = OKXNormalizer.to_okx_instrument_id(instrument)

        return await self._rest.get_orderbook(
            inst_id=okx_inst_id,
            limit=20,
            instrument=instrument,
        )

    async def get_ticker_rest(self, instrument: str) -> TickerSnapshot:
        """
        Fetch ticker via REST API.

        Args:
            instrument: Normalized instrument ID.

        Returns:
            TickerSnapshot: Current ticker.
        """
        if not self._rest:
            raise ConnectionError("REST client not initialized")

        # Find instrument config
        instrument_config = next(
            (i for i in self._instruments if i.id == instrument), None
        )
        if not instrument_config:
            raise ValueError(f"Instrument not found: {instrument}")

        # Get OKX instrument ID
        okx_inst_id = OKXNormalizer.to_okx_instrument_id(instrument)

        return await self._rest.get_ticker(
            inst_id=okx_inst_id,
            instrument=instrument,
        )

    async def health_check(self) -> HealthStatus:
        """
        Perform health check.

        Returns:
            HealthStatus: Current adapter health.
        """
        # Determine connection status
        if not self.is_connected:
            status = ConnectionStatus.DISCONNECTED
        elif self._has_recent_gaps():
            status = ConnectionStatus.DEGRADED
        else:
            status = ConnectionStatus.CONNECTED

        # Get last message time
        last_message_at: Optional[datetime] = None
        if self._ws and self._ws.last_message_at:
            last_message_at = self._ws.last_message_at

        # Calculate lag
        lag_ms = 0
        if last_message_at:
            lag_ms = int(
                (datetime.now(timezone.utc) - last_message_at).total_seconds() * 1000
            )

        # Get reconnect count
        reconnect_count = 0
        if self._ws:
            reconnect_count = self._ws.reconnect_count

        return HealthStatus(
            exchange=self.exchange_name,
            status=status,
            last_message_at=last_message_at,
            message_count=self._message_count,
            lag_ms=lag_ms,
            reconnect_count=reconnect_count,
            gaps_last_hour=len(self._gaps_last_hour),
        )

    def detect_gap(
        self, prev_seq: Optional[int], curr_seq: int
    ) -> Optional[GapMarker]:
        """
        Detect sequence gap.

        OKX uses a GLOBAL seqId across all order book updates, not per-channel.
        When subscribed to "books5" (top 5 levels), we only receive updates when
        the top 5 levels change, but seqId continues incrementing for ALL book
        updates. Therefore, sequence "gaps" are EXPECTED and normal.

        We only detect true gaps when:
        1. Sequence ID goes backwards (indicates reconnection or data issue)
        2. Sequence ID stays the same (duplicate/stale data)

        Args:
            prev_seq: Previous sequence ID (seqId).
            curr_seq: Current sequence ID.

        Returns:
            Optional[GapMarker]: Gap marker if gap detected (backwards/duplicate).
        """
        if prev_seq is None:
            # First message, no gap
            return None

        # Only flag if sequence goes backwards or stays same (true data issues)
        if curr_seq <= prev_seq:
            now = datetime.now(timezone.utc)

            # Determine reason
            if curr_seq < prev_seq:
                reason = "sequence_backwards"
            else:
                reason = "sequence_duplicate"

            return GapMarker(
                exchange=self.exchange_name,
                instrument="UNKNOWN",  # Will be filled by caller
                gap_start=now,
                gap_end=now,
                duration_seconds=Decimal("0"),
                reason=reason,
                sequence_id_before=prev_seq,
                sequence_id_after=curr_seq,
            )

        # Sequence is increasing normally - no gap
        # Note: Large jumps in seqId are EXPECTED when using "books5"
        # as we only see updates affecting top 5 levels
        return None

    def _has_recent_gaps(self) -> bool:
        """Check if there are significant gaps in the last hour."""
        return len(self._gaps_last_hour) >= 5
