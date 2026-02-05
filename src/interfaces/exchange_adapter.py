"""
Abstract base class for exchange adapters.

This module defines the ExchangeAdapter interface that all exchange-specific
implementations (Binance, OKX) must follow to ensure consistent behavior
across different exchange integrations.

The adapter pattern allows the system to:
- Add new exchanges without modifying core logic
- Normalize data into unified schemas (OrderBookSnapshot, TickerSnapshot)
- Handle exchange-specific connection and protocol details
- Detect and report data gaps via sequence tracking

Example:
    >>> class BinanceAdapter(ExchangeAdapter):
    ...     async def connect(self) -> None:
    ...         # Establish WebSocket connection to Binance
    ...         pass
    ...
    ...     async def stream_order_books(self) -> AsyncIterator[OrderBookSnapshot]:
    ...         async for msg in self._ws_stream():
    ...             yield self._normalize(msg)
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional

from src.models.health import GapMarker, HealthStatus
from src.models.orderbook import OrderBookSnapshot
from src.models.ticker import TickerSnapshot


class ExchangeAdapter(ABC):
    """
    Abstract base class for exchange adapters.

    Defines the contract that all exchange-specific implementations must follow.
    Supports both WebSocket streaming (primary) and REST polling (fallback).

    The adapter is responsible for:
    - Managing WebSocket connections with automatic reconnection
    - Converting exchange-specific data formats to normalized schemas
    - Tracking sequence IDs for gap detection
    - Providing health status for monitoring
    - Falling back to REST API when WebSocket is unavailable

    Attributes:
        exchange_name: Lowercase exchange identifier (e.g., "binance", "okx").
        is_connected: True if WebSocket connection is active and healthy.

    Note:
        All financial values in returned models use Decimal for precision.
        Never use float for prices, quantities, or notional values.
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """
        Return the lowercase exchange identifier.

        This identifier is used in:
        - Redis keys (e.g., "orderbook:binance:BTC-USDT-PERP")
        - Database records (exchange column)
        - Logging and metrics

        Returns:
            str: Lowercase exchange name (e.g., "binance", "okx").

        Example:
            >>> adapter.exchange_name
            'binance'
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if the WebSocket connection is active and healthy.

        A connection is considered healthy if:
        - WebSocket is open
        - Last message received within timeout threshold
        - No pending reconnection

        Returns:
            bool: True if connection is active and can receive data.

        Note:
            This should be a fast, non-blocking check.
        """
        pass

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish WebSocket connection to the exchange.

        This method should:
        1. Create WebSocket connection to the appropriate endpoint
        2. Handle any required authentication or handshake
        3. Start heartbeat/ping mechanism if required by exchange
        4. Initialize internal state for sequence tracking

        The method should be idempotent - calling connect() when already
        connected should be a no-op.

        Raises:
            ConnectionError: If unable to establish connection after
                max_reconnect_attempts retries.
            AuthenticationError: If exchange requires authentication and
                credentials are invalid.
            ValueError: If configuration is invalid (e.g., bad URL).

        Example:
            >>> adapter = BinanceAdapter(config)
            >>> await adapter.connect()
            >>> assert adapter.is_connected
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Gracefully close WebSocket connection and clean up resources.

        This method should:
        1. Cancel any pending subscriptions
        2. Stop heartbeat/ping tasks
        3. Close WebSocket connection
        4. Clean up any internal state

        This method MUST be safe to call:
        - Multiple times (idempotent)
        - When not connected (no-op)
        - Should not raise exceptions

        Example:
            >>> await adapter.disconnect()
            >>> assert not adapter.is_connected
        """
        pass

    @abstractmethod
    async def subscribe(self, instruments: List[str]) -> None:
        """
        Subscribe to market data feeds for specified instruments.

        Sends subscription messages to the exchange for order book and
        ticker data streams for each instrument.

        Args:
            instruments: List of normalized instrument identifiers.
                Format: "BTC-USDT-PERP", "BTC-USDT-SPOT"
                The adapter must map these to exchange-specific symbols.

        Raises:
            ValueError: If instrument format is invalid or not supported.
            ConnectionError: If not connected to exchange.
            RuntimeError: If subscription fails (e.g., rate limited).

        Example:
            >>> await adapter.subscribe(["BTC-USDT-PERP", "BTC-USDT-SPOT"])
        """
        pass

    @abstractmethod
    async def stream_order_books(self) -> AsyncIterator[OrderBookSnapshot]:
        """
        Stream order book snapshots from WebSocket feed.

        Yields normalized OrderBookSnapshot objects as they arrive from
        the exchange. This is the primary data source for market surveillance.

        The adapter is responsible for:
        - Converting exchange-specific format to OrderBookSnapshot
        - Validating and sorting price levels
        - Tracking sequence IDs for gap detection
        - Handling reconnection transparently

        Yields:
            OrderBookSnapshot: Normalized order book with:
                - exchange: Exchange identifier
                - instrument: Normalized instrument ID
                - timestamp: Exchange timestamp (UTC)
                - local_timestamp: When we received it (UTC)
                - sequence_id: For gap detection
                - bids: Sorted best (highest) to worst
                - asks: Sorted best (lowest) to worst

        Raises:
            ConnectionError: If connection is lost and cannot recover
                after max_reconnect_attempts.

        Example:
            >>> async for snapshot in adapter.stream_order_books():
            ...     print(f"{snapshot.instrument}: spread={snapshot.spread_bps} bps")

        Note:
            This is an infinite async iterator that should only terminate
            on unrecoverable errors. Normal reconnections should be handled
            internally without interrupting the stream.
        """
        pass

    @abstractmethod
    async def stream_tickers(self) -> AsyncIterator[TickerSnapshot]:
        """
        Stream ticker snapshots from WebSocket feed.

        Yields normalized TickerSnapshot objects containing current prices,
        24h statistics, and derivatives-specific data (mark price, funding).

        Yields:
            TickerSnapshot: Normalized ticker with:
                - exchange: Exchange identifier
                - instrument: Normalized instrument ID
                - timestamp: Exchange timestamp (UTC)
                - last_price: Last traded price
                - mark_price: Mark price (perpetuals only)
                - index_price: Index price (perpetuals only)
                - volume_24h: 24h volume in base currency
                - funding_rate: Current funding rate (perpetuals only)

        Raises:
            ConnectionError: If connection is lost and cannot recover.

        Example:
            >>> async for ticker in adapter.stream_tickers():
            ...     print(f"{ticker.instrument}: mark={ticker.mark_price}")
        """
        pass

    @abstractmethod
    async def get_order_book_rest(self, instrument: str) -> OrderBookSnapshot:
        """
        Fetch order book snapshot via REST API.

        This is a fallback/recovery mechanism used for:
        - Initial snapshot before starting WebSocket stream
        - Gap recovery when sequence gaps are detected
        - Polling fallback when WebSocket is unavailable

        Args:
            instrument: Normalized instrument identifier (e.g., "BTC-USDT-PERP").

        Returns:
            OrderBookSnapshot: Current order book state from REST API.

        Raises:
            ValueError: If instrument is not found or not supported.
            ConnectionError: If REST request fails due to network issues.
            RateLimitError: If exchange rate limit is exceeded.

        Example:
            >>> snapshot = await adapter.get_order_book_rest("BTC-USDT-PERP")
            >>> print(f"Best bid: {snapshot.best_bid}")

        Note:
            REST requests are subject to rate limits. The adapter should
            implement appropriate rate limiting to avoid being blocked.
        """
        pass

    @abstractmethod
    async def get_ticker_rest(self, instrument: str) -> TickerSnapshot:
        """
        Fetch ticker snapshot via REST API.

        Fallback mechanism for getting current ticker data.

        Args:
            instrument: Normalized instrument identifier (e.g., "BTC-USDT-PERP").

        Returns:
            TickerSnapshot: Current ticker state from REST API.

        Raises:
            ValueError: If instrument is not found or not supported.
            ConnectionError: If REST request fails due to network issues.
            RateLimitError: If exchange rate limit is exceeded.

        Example:
            >>> ticker = await adapter.get_ticker_rest("BTC-USDT-PERP")
            >>> print(f"Funding rate: {ticker.funding_rate}")
        """
        pass

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """
        Perform health check and return current status.

        Checks the health of the adapter including:
        - WebSocket connection status
        - Time since last message (stale feed detection)
        - Message processing lag
        - Reconnection count
        - Gap count in last hour

        Returns:
            HealthStatus: Current health metrics including:
                - exchange: Exchange identifier
                - status: ConnectionStatus enum
                - last_message_at: Timestamp of last message
                - message_count: Total messages received
                - lag_ms: Current processing lag
                - reconnect_count: Reconnections in session
                - gaps_last_hour: Data gaps in last hour

        Example:
            >>> health = await adapter.health_check()
            >>> if not health.is_healthy:
            ...     logger.warning(f"Unhealthy: lag={health.lag_ms}ms")

        Note:
            This should be a lightweight operation suitable for
            frequent polling (e.g., every 1 second).
        """
        pass

    @abstractmethod
    def detect_gap(
        self, prev_seq: Optional[int], curr_seq: int
    ) -> Optional[GapMarker]:
        """
        Detect sequence gaps in order book updates.

        Exchange-specific implementations should define gap detection logic
        based on their sequence number semantics:
        - Binance: lastUpdateId (should increment by 1)
        - OKX: seqId (should increment by 1)

        Args:
            prev_seq: Previous sequence number from last update.
                None if this is the first message after connection.
            curr_seq: Current sequence number from this update.

        Returns:
            Optional[GapMarker]: Gap information if a gap was detected:
                - exchange: Exchange identifier
                - instrument: Affected instrument
                - gap_start: Timestamp of last good data
                - gap_end: Timestamp when data resumed
                - duration_seconds: Gap duration
                - reason: "sequence_gap"
                - sequence_id_before: prev_seq
                - sequence_id_after: curr_seq
            Returns None if no gap detected (normal case).

        Example:
            >>> gap = adapter.detect_gap(prev_seq=100, curr_seq=105)
            >>> if gap:
            ...     logger.warning(f"Gap detected: {gap.sequence_gap_size} messages missed")

        Note:
            Gap detection is critical for maintaining data integrity.
            When a gap is detected, z-score buffers should be reset
            and the gap should be recorded in the database.
        """
        pass

    def __repr__(self) -> str:
        """Return string representation of adapter."""
        return f"{self.__class__.__name__}(exchange={self.exchange_name}, connected={self.is_connected})"
