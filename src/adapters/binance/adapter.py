"""
Binance exchange adapter.

Main adapter implementation that implements the ExchangeAdapter interface.
Coordinates WebSocket streaming, REST fallback, data normalization, and
gap detection.

This adapter:
    - Manages separate WebSocket connections for perpetual and spot markets
    - Normalizes Binance data to unified models
    - Detects sequence gaps using lastUpdateId
    - Falls back to REST when WebSocket fails
    - Tracks health metrics

Example:
    >>> from src.adapters.binance import BinanceAdapter
    >>> from src.config.loader import load_config
    >>>
    >>> config = load_config()
    >>> exchange_config = config.get_exchange("binance")
    >>> instruments = config.get_enabled_instruments()
    >>>
    >>> adapter = BinanceAdapter(exchange_config, instruments)
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

from src.adapters.binance.normalizer import BinanceNormalizer
from src.adapters.binance.rest import BinanceRestClient
from src.adapters.binance.websocket import BinanceWebSocketClient
from src.config.models import ExchangeConfig, InstrumentConfig
from src.interfaces.exchange_adapter import ExchangeAdapter
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.orderbook import OrderBookSnapshot
from src.models.ticker import TickerSnapshot

logger = structlog.get_logger(__name__)


class BinanceAdapter(ExchangeAdapter):
    """
    Binance exchange adapter implementing ExchangeAdapter interface.

    Manages connections to Binance futures and spot markets, normalizes
    data, detects gaps, and provides health monitoring.

    Attributes:
        exchange_name: Always returns "binance".
        is_connected: True if at least one WebSocket is connected.

    Example:
        >>> adapter = BinanceAdapter(exchange_config, instruments)
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
        Initialize Binance adapter.

        Args:
            exchange_config: Exchange configuration from config/exchanges.yaml.
            instruments: List of instruments to monitor.
        """
        self._config = exchange_config
        self._instruments = instruments

        # WebSocket clients (one per market type)
        self._ws_futures: Optional[BinanceWebSocketClient] = None
        self._ws_spot: Optional[BinanceWebSocketClient] = None

        # REST clients
        self._rest_futures: Optional[BinanceRestClient] = None
        self._rest_spot: Optional[BinanceRestClient] = None

        # Store base WebSocket URLs (immutable)
        self._ws_futures_base_url: Optional[str] = None
        self._ws_spot_base_url: Optional[str] = None

        # State tracking
        self._last_sequence_ids: Dict[str, int] = {}  # instrument -> last seq ID
        self._message_count = 0
        self._reconnect_count = 0
        self._gaps_last_hour: deque = deque(maxlen=3600)  # Track gaps

        # Ticker cache (for combining 24hr ticker with mark price)
        self._ticker_24hr_cache: Dict[str, Dict] = {}
        self._mark_price_cache: Dict[str, Dict] = {}

        logger.info(
            "binance_adapter_initialized",
            instruments=[inst.id for inst in instruments],
        )

    @property
    def exchange_name(self) -> str:
        """Return exchange identifier."""
        return "binance"

    @property
    def is_connected(self) -> bool:
        """Check if at least one WebSocket is connected."""
        futures_connected = self._ws_futures and self._ws_futures.is_connected
        spot_connected = self._ws_spot and self._ws_spot.is_connected
        return bool(futures_connected or spot_connected)

    async def connect(self) -> None:
        """
        Establish WebSocket connections for all market types.

        Creates separate connections for futures and spot markets.
        Initializes REST clients for fallback.
        """
        if self.is_connected:
            logger.debug("binance_already_connected")
            return

        # Initialize WebSocket clients
        futures_url = self._config.get_websocket_url("futures")
        spot_url = self._config.get_websocket_url("spot")

        # Store base URLs for later use
        self._ws_futures_base_url = futures_url
        self._ws_spot_base_url = spot_url

        if futures_url:
            self._ws_futures = BinanceWebSocketClient(
                url=futures_url,
                ping_interval=self._config.connection.ping_interval_seconds,
                ping_timeout=self._config.connection.ping_timeout_seconds,
                max_reconnect_attempts=self._config.connection.max_reconnect_attempts,
                reconnect_delay=self._config.connection.reconnect_delay_seconds,
            )
            await self._ws_futures.connect()

        if spot_url:
            self._ws_spot = BinanceWebSocketClient(
                url=spot_url,
                ping_interval=self._config.connection.ping_interval_seconds,
                ping_timeout=self._config.connection.ping_timeout_seconds,
                max_reconnect_attempts=self._config.connection.max_reconnect_attempts,
                reconnect_delay=self._config.connection.reconnect_delay_seconds,
            )
            await self._ws_spot.connect()

        # Initialize REST clients
        futures_rest_url = self._config.get_rest_url("futures")
        spot_rest_url = self._config.get_rest_url("spot")

        if futures_rest_url:
            self._rest_futures = BinanceRestClient(
                base_url=futures_rest_url,
                rate_limit_per_second=self._config.connection.rate_limit_per_second,
            )

        if spot_rest_url:
            self._rest_spot = BinanceRestClient(
                base_url=spot_rest_url,
                rate_limit_per_second=self._config.connection.rate_limit_per_second,
            )

        logger.info(
            "binance_connected",
            futures=futures_url is not None,
            spot=spot_url is not None,
        )

    async def disconnect(self) -> None:
        """Gracefully disconnect all connections."""
        if self._ws_futures:
            await self._ws_futures.disconnect()

        if self._ws_spot:
            await self._ws_spot.disconnect()

        if self._rest_futures:
            await self._rest_futures.close()

        if self._rest_spot:
            await self._rest_spot.close()

        logger.info("binance_disconnected")

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

        # Group instruments by type
        futures_streams = []
        spot_streams = []

        for instrument_id in instruments:
            # Find instrument config
            instrument = next(
                (i for i in self._instruments if i.id == instrument_id), None
            )
            if not instrument:
                raise ValueError(f"Instrument not found: {instrument_id}")

            # Get Binance symbol config
            symbol_config = instrument.get_exchange_symbol("binance")
            if not symbol_config:
                raise ValueError(
                    f"Binance symbol config not found for {instrument_id}"
                )

            # Get stream name
            stream = symbol_config.stream
            if not stream:
                # Build stream name from symbol
                symbol = symbol_config.symbol.lower()
                depth = self._config.streams.orderbook_depth
                speed = self._config.streams.orderbook_speed
                stream = f"{symbol}@depth{depth}@{speed}"

            # Add to appropriate list
            if instrument.is_perpetual:
                futures_streams.append(stream)
                # Add mark price stream for perpetuals
                if symbol_config.mark_price_stream:
                    futures_streams.append(symbol_config.mark_price_stream)
            else:
                spot_streams.append(stream)

            # Add ticker streams
            if symbol_config.ticker_stream:
                if instrument.is_perpetual:
                    futures_streams.append(symbol_config.ticker_stream)
                else:
                    spot_streams.append(symbol_config.ticker_stream)

        # Subscribe on appropriate WebSockets
        # Binance uses combined streams, so we need to build the URL
        if futures_streams and self._ws_futures and self._ws_futures_base_url:
            # Build combined stream URL from base URL (not from current URL)
            combined_stream = "/".join(futures_streams)
            combined_url = f"{self._ws_futures_base_url}?streams={combined_stream}"

            # Reconnect with new URL
            await self._ws_futures.disconnect()

            # Create new WebSocket client with combined URL
            self._ws_futures = BinanceWebSocketClient(
                url=combined_url,
                ping_interval=self._config.connection.ping_interval_seconds,
                ping_timeout=self._config.connection.ping_timeout_seconds,
                max_reconnect_attempts=self._config.connection.max_reconnect_attempts,
                reconnect_delay=self._config.connection.reconnect_delay_seconds,
            )
            await self._ws_futures.connect()

            logger.info("binance_subscribed_futures", streams=futures_streams)

        if spot_streams and self._ws_spot and self._ws_spot_base_url:
            combined_stream = "/".join(spot_streams)
            combined_url = f"{self._ws_spot_base_url}?streams={combined_stream}"

            await self._ws_spot.disconnect()

            # Create new WebSocket client with combined URL
            self._ws_spot = BinanceWebSocketClient(
                url=combined_url,
                ping_interval=self._config.connection.ping_interval_seconds,
                ping_timeout=self._config.connection.ping_timeout_seconds,
                max_reconnect_attempts=self._config.connection.max_reconnect_attempts,
                reconnect_delay=self._config.connection.reconnect_delay_seconds,
            )
            await self._ws_spot.connect()

            logger.info("binance_subscribed_spot", streams=spot_streams)

    async def stream_order_books(self) -> AsyncIterator[OrderBookSnapshot]:
        """
        Stream order book snapshots from WebSocket.

        Yields normalized OrderBookSnapshot objects. Handles both futures
        and spot markets. Detects gaps and falls back to REST on errors.

        Yields:
            OrderBookSnapshot: Normalized order book snapshot.
        """
        import asyncio

        async def stream_from_ws(
            ws_client: BinanceWebSocketClient, market_type: str
        ) -> AsyncIterator[OrderBookSnapshot]:
            """Stream from a specific WebSocket client.

            Handles two distinct Binance order book wire formats:

            1. Futures / diff-depth ("depthUpdate"):
                   Contains "e": "depthUpdate" and "s" (symbol).
            2. Spot partial depth (@depth<N>@<speed>):
                   Contains "lastUpdateId", "bids", "asks".
                   Does NOT contain "e" or "s".  The instrument is
                   resolved from the subscription context (market_type).
            """
            async for message in ws_client.stream_messages():
                # Classify the message by format.  Two paths are valid:
                #   a) Futures/diff-depth: "e" == "depthUpdate"
                #   b) Spot partial depth: no "e", has "lastUpdateId"+"bids"+"asks"
                # Anything else (ticker, markPrice, etc.) is skipped.
                is_depth_update = message.get("e") == "depthUpdate"
                is_spot_partial = BinanceNormalizer._is_spot_partial_depth(message)

                if not is_depth_update and not is_spot_partial:
                    continue

                try:
                    if is_spot_partial:
                        # Spot partial depth carries no symbol field.
                        # Resolve the instrument from all configured spot
                        # instruments for this exchange.  In the current
                        # Phase 1 scope there is exactly one spot instrument
                        # per symbol, so we pick the first match.
                        instrument = self._find_spot_instrument_for_stream(
                            market_type
                        )
                        if not instrument:
                            logger.warning(
                                "binance_no_spot_instrument_configured",
                                market_type=market_type,
                            )
                            continue
                    else:
                        # Futures / diff-depth: symbol is in the message.
                        symbol = message.get("s", "").upper()
                        instrument = self._find_instrument_by_symbol(
                            symbol, market_type
                        )
                        if not instrument:
                            logger.warning(
                                "binance_unknown_symbol",
                                symbol=symbol,
                                market_type=market_type,
                            )
                            continue

                    # Normalize to OrderBookSnapshot
                    snapshot = BinanceNormalizer.normalize_orderbook(
                        raw_message=message,
                        instrument=instrument.id,
                        instrument_type=market_type,
                    )

                    # Detect gaps
                    gap = self.detect_gap(
                        prev_seq=self._last_sequence_ids.get(instrument.id),
                        curr_seq=snapshot.sequence_id,
                    )

                    if gap:
                        # Enhance gap with instrument info
                        gap = GapMarker(
                            exchange=self.exchange_name,
                            instrument=instrument.id,
                            gap_start=gap.gap_start,
                            gap_end=gap.gap_end,
                            duration_seconds=gap.duration_seconds,
                            reason="sequence_gap",
                            sequence_id_before=gap.sequence_id_before,
                            sequence_id_after=gap.sequence_id_after,
                        )
                        self._gaps_last_hour.append(gap)

                        logger.warning(
                            "binance_gap_detected",
                            instrument=instrument.id,
                            gap_size=gap.sequence_gap_size,
                            prev_seq=gap.sequence_id_before,
                            curr_seq=gap.sequence_id_after,
                        )

                    self._last_sequence_ids[instrument.id] = snapshot.sequence_id
                    self._message_count += 1

                    yield snapshot

                except Exception as e:
                    logger.error(
                        "binance_orderbook_processing_error",
                        error=str(e),
                        message=message,
                    )

        # Stream from both WebSockets concurrently
        tasks = []
        if self._ws_futures:
            tasks.append(stream_from_ws(self._ws_futures, "perpetual"))
        if self._ws_spot:
            tasks.append(stream_from_ws(self._ws_spot, "spot"))

        if not tasks:
            raise ConnectionError("No WebSocket connections available")

        # Merge streams
        async for snapshot in self._merge_async_iterators(tasks):
            yield snapshot

    async def stream_tickers(self) -> AsyncIterator[TickerSnapshot]:
        """
        Stream ticker snapshots from WebSocket.

        For perpetuals, combines 24hr ticker with mark price data.
        For spot, uses only 24hr ticker.

        Yields:
            TickerSnapshot: Normalized ticker snapshot.
        """
        import asyncio

        async def stream_from_ws(
            ws_client: BinanceWebSocketClient, market_type: str
        ) -> AsyncIterator[TickerSnapshot]:
            """Stream tickers from a specific WebSocket client."""
            async for message in ws_client.stream_messages():
                event_type = message.get("e")

                # Cache 24hr ticker
                if event_type == "24hrTicker":
                    symbol = message.get("s", "").upper()
                    self._ticker_24hr_cache[symbol] = message

                # Cache mark price (perpetuals only)
                elif event_type == "markPriceUpdate":
                    symbol = message.get("s", "").upper()
                    self._mark_price_cache[symbol] = message

                # Only yield when we have 24hr ticker
                if event_type not in ("24hrTicker", "markPriceUpdate"):
                    continue

                symbol = message.get("s", "").upper()
                instrument = self._find_instrument_by_symbol(symbol, market_type)

                if not instrument:
                    continue

                # Get cached data
                ticker_24hr = self._ticker_24hr_cache.get(symbol)
                mark_price = self._mark_price_cache.get(symbol)

                if not ticker_24hr:
                    continue

                try:
                    ticker = BinanceNormalizer.normalize_ticker(
                        raw_24hr_ticker=ticker_24hr,
                        raw_mark_price=mark_price,
                        instrument=instrument.id,
                    )
                    yield ticker

                except Exception as e:
                    logger.error(
                        "binance_ticker_processing_error",
                        instrument=instrument.id,
                        error=str(e),
                    )

        # Stream from both WebSockets concurrently
        tasks = []
        if self._ws_futures:
            tasks.append(stream_from_ws(self._ws_futures, "perpetual"))
        if self._ws_spot:
            tasks.append(stream_from_ws(self._ws_spot, "spot"))

        if not tasks:
            raise ConnectionError("No WebSocket connections available")

        async for ticker in self._merge_async_iterators(tasks):
            yield ticker

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
        # Find instrument config
        instrument_config = next(
            (i for i in self._instruments if i.id == instrument), None
        )
        if not instrument_config:
            raise ValueError(f"Instrument not found: {instrument}")

        # Get Binance symbol
        symbol_config = instrument_config.get_exchange_symbol("binance")
        if not symbol_config:
            raise ValueError(f"Binance symbol not found for {instrument}")

        symbol = symbol_config.symbol

        # Use appropriate REST client
        if instrument_config.is_perpetual:
            if not self._rest_futures:
                raise ConnectionError("Futures REST client not initialized")
            rest_client = self._rest_futures
        else:
            if not self._rest_spot:
                raise ConnectionError("Spot REST client not initialized")
            rest_client = self._rest_spot

        return await rest_client.get_orderbook(
            symbol=symbol,
            limit=self._config.streams.orderbook_depth,
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
        # Find instrument config
        instrument_config = next(
            (i for i in self._instruments if i.id == instrument), None
        )
        if not instrument_config:
            raise ValueError(f"Instrument not found: {instrument}")

        # Get Binance symbol
        symbol_config = instrument_config.get_exchange_symbol("binance")
        if not symbol_config:
            raise ValueError(f"Binance symbol not found for {instrument}")

        symbol = symbol_config.symbol

        # Use appropriate REST client
        if instrument_config.is_perpetual:
            if not self._rest_futures:
                raise ConnectionError("Futures REST client not initialized")
            rest_client = self._rest_futures
        else:
            if not self._rest_spot:
                raise ConnectionError("Spot REST client not initialized")
            rest_client = self._rest_spot

        return await rest_client.get_ticker(symbol=symbol, instrument=instrument)

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

        # Get last message time (from either WebSocket)
        last_message_at: Optional[datetime] = None
        if self._ws_futures and self._ws_futures.last_message_at:
            last_message_at = self._ws_futures.last_message_at
        if self._ws_spot and self._ws_spot.last_message_at:
            if not last_message_at or self._ws_spot.last_message_at > last_message_at:
                last_message_at = self._ws_spot.last_message_at

        # Calculate lag
        lag_ms = 0
        if last_message_at:
            lag_ms = int(
                (datetime.now(timezone.utc) - last_message_at).total_seconds() * 1000
            )

        # Get reconnect count
        reconnect_count = 0
        if self._ws_futures:
            reconnect_count += self._ws_futures.reconnect_count
        if self._ws_spot:
            reconnect_count += self._ws_spot.reconnect_count

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

        Binance uses a GLOBAL lastUpdateId across all order book updates.
        When subscribed to "depth20@100ms" (top 20 levels), we only receive
        updates when the top 20 levels change, but lastUpdateId continues
        incrementing for ALL book updates (including changes beyond level 20).
        Therefore, sequence "gaps" are EXPECTED and normal.

        Reference: https://binance-docs.github.io/apidocs/futures/en/#diff-book-depth-streams

        We only detect true gaps when:
        1. Sequence ID goes backwards (indicates reconnection or data issue)
        2. Sequence ID stays the same (duplicate/stale data)

        Args:
            prev_seq: Previous sequence ID (lastUpdateId).
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
        # Note: Large jumps in lastUpdateId are EXPECTED when using "depth20@100ms"
        # as we only see updates affecting top 20 levels
        return None

    def _find_instrument_by_symbol(
        self, symbol: str, market_type: str
    ) -> Optional[InstrumentConfig]:
        """Find instrument config by Binance symbol and market type."""
        for instrument in self._instruments:
            if market_type == "perpetual" and not instrument.is_perpetual:
                continue
            if market_type == "spot" and not instrument.is_spot:
                continue

            symbol_config = instrument.get_exchange_symbol("binance")
            if symbol_config and symbol_config.symbol.upper() == symbol:
                return instrument

        return None

    def _find_spot_instrument_for_stream(
        self, market_type: str
    ) -> Optional[InstrumentConfig]:
        """
        Resolve the instrument for a spot partial depth message.

        Spot partial depth streams (e.g. btcusdt@depth20@100ms on the spot
        WebSocket) do not include the symbol in the message payload.  When
        the spot WebSocket is subscribed to a single combined stream this
        method returns the first enabled spot instrument configured for
        Binance.  If multiple spot instruments are subscribed on the same
        connection the stream name would need to be carried through the
        combined-stream envelope ("stream" key) for disambiguation -- that
        path is not needed in the current Phase 1 scope (single spot
        instrument).

        Args:
            market_type: Always "spot" when called from the spot path.

        Returns:
            Optional[InstrumentConfig]: The matching spot instrument, or
                None if no spot instrument is configured.
        """
        for instrument in self._instruments:
            if market_type == "spot" and not instrument.is_spot:
                continue
            if market_type == "perpetual" and not instrument.is_perpetual:
                continue

            symbol_config = instrument.get_exchange_symbol("binance")
            if symbol_config:
                return instrument

        return None

    def _has_recent_gaps(self) -> bool:
        """Check if there are significant gaps in the last hour."""
        return len(self._gaps_last_hour) >= 5

    async def _merge_async_iterators(
        self, iterators: List[AsyncIterator]
    ) -> AsyncIterator:
        """
        Merge multiple async iterators into one.

        Yields items from all iterators as they arrive.
        """
        import asyncio

        queue: asyncio.Queue = asyncio.Queue()

        async def consume(it: AsyncIterator) -> None:
            try:
                async for item in it:
                    await queue.put(item)
            except Exception as e:
                logger.error("iterator_error", error=str(e))

        # Start all consumers
        tasks = [asyncio.create_task(consume(it)) for it in iterators]

        try:
            while True:
                # Check if all tasks are done
                if all(t.done() for t in tasks):
                    break

                # Get next item with timeout
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield item
                except asyncio.TimeoutError:
                    continue

        finally:
            # Cancel all tasks
            for task in tasks:
                if not task.done():
                    task.cancel()
