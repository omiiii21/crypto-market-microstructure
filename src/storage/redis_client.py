"""
Async Redis client for real-time state management.

This module provides a Redis client for storing and retrieving real-time data
including order book snapshots, z-score buffers, active alerts, and system health.
It also supports pub/sub for real-time dashboard updates.

Key Patterns:
    - Order books: `orderbook:{exchange}:{instrument}` (hash with TTL)
    - Z-score buffers: `zscore:{exchange}:{instrument}:{metric}` (list with LTRIM)
    - Alerts: `alert:{alert_id}` (hash), `alerts:active` (set),
              `alerts:by_priority:{priority}` (set), `alerts:by_instrument:{instrument}` (set)
    - Health: `health:{exchange}` (hash)
    - Pub/Sub channels: `updates:orderbook`, `updates:alerts`, `updates:health`

Note:
    This module is owned by the ARCHITECT agent.
    All financial values are serialized as strings to preserve Decimal precision.

Example:
    >>> from src.config.models import RedisConnectionConfig
    >>> from src.storage.redis_client import RedisClient
    >>>
    >>> config = RedisConnectionConfig(url="redis://localhost:6379")
    >>> client = RedisClient(config)
    >>> await client.connect()
    >>>
    >>> # Store an order book snapshot
    >>> await client.set_orderbook(snapshot)
    >>>
    >>> # Retrieve it
    >>> snapshot = await client.get_orderbook("binance", "BTC-USDT-PERP")
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional

import structlog
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from src.config.models import RedisConnectionConfig, RedisStorageConfig
from src.models.alerts import Alert, AlertPriority
from src.models.health import HealthStatus
from src.models.orderbook import OrderBookSnapshot

logger = structlog.get_logger(__name__)


class RedisClientError(Exception):
    """Base exception for Redis client errors."""

    pass


class RedisConnectionException(RedisClientError):
    """Raised when Redis connection fails."""

    pass


class RedisOperationError(RedisClientError):
    """Raised when a Redis operation fails."""

    pass


def _decimal_serializer(obj: Any) -> str:
    """
    JSON serializer that handles Decimal values.

    Args:
        obj: Object to serialize.

    Returns:
        str: String representation for JSON.

    Raises:
        TypeError: If object type is not serializable.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class RedisClient:
    """
    Async Redis client for real-time surveillance system state.

    Provides methods for storing and retrieving order book snapshots,
    z-score rolling buffers, active alerts, and system health status.
    Also supports pub/sub for real-time updates.

    Attributes:
        config: Redis connection configuration.
        storage_config: Redis storage configuration (TTLs, etc.).
        _pool: Connection pool for efficient connection reuse.
        _client: Redis client instance.
        _connected: Whether the client is connected.

    Example:
        >>> config = RedisConnectionConfig(url="redis://localhost:6379")
        >>> storage_config = RedisStorageConfig()
        >>> client = RedisClient(config, storage_config)
        >>> await client.connect()
        >>> try:
        ...     await client.set_orderbook(snapshot)
        ... finally:
        ...     await client.disconnect()
    """

    # Key prefixes
    KEY_ORDERBOOK = "orderbook"
    KEY_ZSCORE = "zscore"
    KEY_ALERT = "alert"
    KEY_ALERTS_ACTIVE = "alerts:active"
    KEY_ALERTS_BY_PRIORITY = "alerts:by_priority"
    KEY_ALERTS_BY_INSTRUMENT = "alerts:by_instrument"
    KEY_HEALTH = "health"

    # Pub/sub channels
    CHANNEL_ORDERBOOK = "updates:orderbook"
    CHANNEL_ALERTS = "updates:alerts"
    CHANNEL_HEALTH = "updates:health"

    def __init__(
        self,
        config: RedisConnectionConfig,
        storage_config: Optional[RedisStorageConfig] = None,
    ) -> None:
        """
        Initialize the Redis client.

        Args:
            config: Redis connection configuration containing URL, db, and pool settings.
            storage_config: Optional storage configuration for TTLs. Defaults to
                           RedisStorageConfig() if not provided.

        Example:
            >>> config = RedisConnectionConfig(
            ...     url="redis://localhost:6379",
            ...     db=0,
            ...     max_connections=10,
            ...     socket_timeout=5,
            ... )
            >>> client = RedisClient(config)
        """
        self.config = config
        self.storage_config = storage_config or RedisStorageConfig()
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None  # type: ignore[type-arg]
        self._connected: bool = False

        logger.info(
            "redis_client_initialized",
            url=config.url,
            db=config.db,
            max_connections=config.max_connections,
        )

    @property
    def is_connected(self) -> bool:
        """
        Check if the client is connected to Redis.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self._connected and self._client is not None

    async def connect(self) -> None:
        """
        Establish connection to Redis.

        Creates a connection pool and initializes the Redis client.
        Uses hiredis parser for improved performance.

        Raises:
            RedisConnectionException: If connection fails.

        Example:
            >>> client = RedisClient(config)
            >>> await client.connect()
            >>> assert client.is_connected
        """
        if self._connected:
            logger.warning("redis_already_connected")
            return

        try:
            self._pool = ConnectionPool.from_url(
                self.config.url,
                db=self.config.db,
                max_connections=self.config.max_connections,
                socket_timeout=self.config.socket_timeout,
                socket_connect_timeout=self.config.socket_timeout,
                decode_responses=True,
            )
            self._client = Redis(connection_pool=self._pool)

            # Verify connection with ping
            await self._client.ping()
            self._connected = True

            logger.info(
                "redis_connected",
                url=self.config.url,
                db=self.config.db,
            )

        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            self._connected = False
            logger.error(
                "redis_connection_failed",
                url=self.config.url,
                error=str(e),
            )
            raise RedisConnectionException(
                f"Failed to connect to Redis at {self.config.url}: {e}"
            ) from e

    async def disconnect(self) -> None:
        """
        Close Redis connection and release resources.

        Closes the client connection and connection pool.
        Safe to call multiple times.

        Example:
            >>> await client.disconnect()
            >>> assert not client.is_connected
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except RedisError as e:
                logger.warning("redis_close_error", error=str(e))
            finally:
                self._client = None

        if self._pool is not None:
            try:
                await self._pool.aclose()
            except Exception as e:
                logger.warning("redis_pool_close_error", error=str(e))
            finally:
                self._pool = None

        self._connected = False
        logger.info("redis_disconnected")

    async def ping(self) -> bool:
        """
        Check Redis connection health.

        Returns:
            bool: True if Redis responds to PING, False otherwise.

        Example:
            >>> if await client.ping():
            ...     print("Redis is healthy")
        """
        if not self._client:
            return False

        try:
            await self._client.ping()
            return True
        except RedisError as e:
            logger.warning("redis_ping_failed", error=str(e))
            return False

    def _require_connection(self) -> Redis:  # type: ignore[type-arg]
        """
        Ensure client is connected and return the Redis instance.

        Returns:
            Redis: The Redis client instance.

        Raises:
            RedisConnectionException: If not connected.
        """
        if not self._connected or self._client is None:
            raise RedisConnectionException("Redis client is not connected")
        return self._client

    # =========================================================================
    # ORDER BOOK STATE
    # =========================================================================

    def _orderbook_key(self, exchange: str, instrument: str) -> str:
        """
        Generate Redis key for order book storage.

        Args:
            exchange: Exchange identifier (e.g., "binance").
            instrument: Instrument identifier (e.g., "BTC-USDT-PERP").

        Returns:
            str: Redis key in format `orderbook:{exchange}:{instrument}`.
        """
        return f"{self.KEY_ORDERBOOK}:{exchange}:{instrument}"

    async def set_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        """
        Store an order book snapshot in Redis.

        Serializes the snapshot using Pydantic's model_dump_json() and stores
        it as a string with TTL from configuration.

        Args:
            snapshot: The OrderBookSnapshot to store.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> snapshot = OrderBookSnapshot(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     timestamp=datetime.utcnow(),
            ...     local_timestamp=datetime.utcnow(),
            ...     sequence_id=12345678,
            ...     bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
            ...     asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("0.5"))],
            ... )
            >>> await client.set_orderbook(snapshot)
        """
        client = self._require_connection()
        key = self._orderbook_key(snapshot.exchange, snapshot.instrument)

        try:
            serialized = snapshot.model_dump_json()
            ttl = self.storage_config.current_state_ttl_seconds

            await client.setex(key, ttl, serialized)

            logger.debug(
                "orderbook_stored",
                exchange=snapshot.exchange,
                instrument=snapshot.instrument,
                sequence_id=snapshot.sequence_id,
                ttl=ttl,
            )

        except RedisError as e:
            logger.error(
                "orderbook_store_failed",
                exchange=snapshot.exchange,
                instrument=snapshot.instrument,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to store order book for {snapshot.exchange}:{snapshot.instrument}: {e}"
            ) from e

    async def get_orderbook(
        self, exchange: str, instrument: str
    ) -> Optional[OrderBookSnapshot]:
        """
        Retrieve an order book snapshot from Redis.

        Args:
            exchange: Exchange identifier (e.g., "binance").
            instrument: Instrument identifier (e.g., "BTC-USDT-PERP").

        Returns:
            Optional[OrderBookSnapshot]: The stored snapshot, or None if not found.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> snapshot = await client.get_orderbook("binance", "BTC-USDT-PERP")
            >>> if snapshot:
            ...     print(f"Best bid: {snapshot.best_bid}")
        """
        client = self._require_connection()
        key = self._orderbook_key(exchange, instrument)

        try:
            data = await client.get(key)

            if data is None:
                logger.debug(
                    "orderbook_not_found",
                    exchange=exchange,
                    instrument=instrument,
                )
                return None

            snapshot = OrderBookSnapshot.model_validate_json(data)

            logger.debug(
                "orderbook_retrieved",
                exchange=exchange,
                instrument=instrument,
                sequence_id=snapshot.sequence_id,
            )

            return snapshot

        except RedisError as e:
            logger.error(
                "orderbook_retrieve_failed",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve order book for {exchange}:{instrument}: {e}"
            ) from e

    # =========================================================================
    # Z-SCORE ROLLING BUFFERS
    # =========================================================================

    def _zscore_key(self, exchange: str, instrument: str, metric: str) -> str:
        """
        Generate Redis key for z-score buffer storage.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name (e.g., "spread_bps").

        Returns:
            str: Redis key in format `zscore:{exchange}:{instrument}:{metric}`.
        """
        return f"{self.KEY_ZSCORE}:{exchange}:{instrument}:{metric}"

    async def add_zscore_sample(
        self,
        exchange: str,
        instrument: str,
        metric: str,
        value: Decimal,
        window_size: int = 300,
    ) -> None:
        """
        Add a sample to the z-score rolling buffer.

        Appends the value to a Redis list and trims to maintain window size.
        Values are stored as JSON-serialized strings to preserve Decimal precision.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name (e.g., "spread_bps").
            value: The Decimal value to add.
            window_size: Maximum number of samples to retain (default: 300).

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> await client.add_zscore_sample(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     metric="spread_bps",
            ...     value=Decimal("2.5"),
            ...     window_size=300,
            ... )
        """
        client = self._require_connection()
        key = self._zscore_key(exchange, instrument, metric)

        try:
            # Serialize Decimal as string to preserve precision
            serialized_value = str(value)

            # Use pipeline for atomic operations
            async with client.pipeline(transaction=True) as pipe:
                # Append value (RPUSH adds to end)
                pipe.rpush(key, serialized_value)
                # Trim to window size (keep last `window_size` elements)
                pipe.ltrim(key, -window_size, -1)
                # Set TTL to prevent orphaned keys
                pipe.expire(key, self.storage_config.zscore_buffer_ttl_seconds)
                await pipe.execute()

            logger.debug(
                "zscore_sample_added",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                value=str(value),
            )

        except RedisError as e:
            logger.error(
                "zscore_sample_add_failed",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to add z-score sample for {exchange}:{instrument}:{metric}: {e}"
            ) from e

    async def get_zscore_buffer(
        self,
        exchange: str,
        instrument: str,
        metric: str,
        limit: Optional[int] = None,
    ) -> List[Decimal]:
        """
        Retrieve the z-score rolling buffer.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name.
            limit: Optional maximum number of samples to return (most recent).
                   If None, returns all samples in the buffer.

        Returns:
            List[Decimal]: List of Decimal values, oldest to newest.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> samples = await client.get_zscore_buffer(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     metric="spread_bps",
            ...     limit=30,
            ... )
            >>> print(f"Sample count: {len(samples)}")
        """
        client = self._require_connection()
        key = self._zscore_key(exchange, instrument, metric)

        try:
            if limit is not None:
                # Get last `limit` elements
                data = await client.lrange(key, -limit, -1)
            else:
                # Get all elements
                data = await client.lrange(key, 0, -1)

            # Convert strings back to Decimal
            samples = [Decimal(v) for v in data]

            logger.debug(
                "zscore_buffer_retrieved",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                sample_count=len(samples),
            )

            return samples

        except RedisError as e:
            logger.error(
                "zscore_buffer_retrieve_failed",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve z-score buffer for {exchange}:{instrument}:{metric}: {e}"
            ) from e

    async def clear_zscore_buffer(
        self,
        exchange: str,
        instrument: str,
        metric: str,
    ) -> None:
        """
        Clear the z-score rolling buffer.

        Used when data gaps are detected to reset the statistical baseline.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> await client.clear_zscore_buffer(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     metric="spread_bps",
            ... )
        """
        client = self._require_connection()
        key = self._zscore_key(exchange, instrument, metric)

        try:
            await client.delete(key)

            logger.info(
                "zscore_buffer_cleared",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
            )

        except RedisError as e:
            logger.error(
                "zscore_buffer_clear_failed",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to clear z-score buffer for {exchange}:{instrument}:{metric}: {e}"
            ) from e

    async def get_zscore_buffer_length(
        self,
        exchange: str,
        instrument: str,
        metric: str,
    ) -> int:
        """
        Get the current length of a z-score buffer.

        Useful for checking warmup progress without retrieving all samples.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name.

        Returns:
            int: Number of samples in the buffer.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> count = await client.get_zscore_buffer_length(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     metric="spread_bps",
            ... )
            >>> print(f"Samples collected: {count}")
        """
        client = self._require_connection()
        key = self._zscore_key(exchange, instrument, metric)

        try:
            length = await client.llen(key)
            return int(length)

        except RedisError as e:
            logger.error(
                "zscore_buffer_length_failed",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to get z-score buffer length for {exchange}:{instrument}:{metric}: {e}"
            ) from e

    # =========================================================================
    # ACTIVE ALERTS
    # =========================================================================

    def _alert_key(self, alert_id: str) -> str:
        """Generate Redis key for an alert."""
        return f"{self.KEY_ALERT}:{alert_id}"

    def _alerts_by_priority_key(self, priority: AlertPriority) -> str:
        """Generate Redis key for alerts by priority index."""
        return f"{self.KEY_ALERTS_BY_PRIORITY}:{priority.value}"

    def _alerts_by_instrument_key(self, instrument: str) -> str:
        """Generate Redis key for alerts by instrument index."""
        return f"{self.KEY_ALERTS_BY_INSTRUMENT}:{instrument}"

    async def set_alert(self, alert: Alert) -> None:
        """
        Store an alert in Redis with index maintenance.

        Stores the alert as a JSON string and adds it to the appropriate
        index sets (active, by_priority, by_instrument).

        Args:
            alert: The Alert to store.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> alert = Alert(
            ...     alert_type="spread_warning",
            ...     priority=AlertPriority.P2,
            ...     severity=AlertSeverity.WARNING,
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     trigger_metric="spread_bps",
            ...     trigger_value=Decimal("3.5"),
            ...     trigger_threshold=Decimal("3.0"),
            ...     trigger_condition=AlertCondition.GT,
            ...     triggered_at=datetime.utcnow(),
            ... )
            >>> await client.set_alert(alert)
        """
        client = self._require_connection()
        key = self._alert_key(alert.alert_id)

        try:
            serialized = alert.model_dump_json()

            async with client.pipeline(transaction=True) as pipe:
                # Store the alert
                pipe.set(key, serialized)

                # Add to index sets if alert is active
                if alert.is_active:
                    pipe.sadd(self.KEY_ALERTS_ACTIVE, alert.alert_id)
                    pipe.sadd(
                        self._alerts_by_priority_key(alert.priority),
                        alert.alert_id,
                    )
                    pipe.sadd(
                        self._alerts_by_instrument_key(alert.instrument),
                        alert.alert_id,
                    )
                else:
                    # Remove from index sets if resolved
                    pipe.srem(self.KEY_ALERTS_ACTIVE, alert.alert_id)
                    # Note: We don't remove from priority/instrument sets here
                    # to preserve history; cleanup is done by TTL

                await pipe.execute()

            logger.debug(
                "alert_stored",
                alert_id=alert.alert_id,
                alert_type=alert.alert_type,
                priority=alert.priority.value,
                is_active=alert.is_active,
            )

        except RedisError as e:
            logger.error(
                "alert_store_failed",
                alert_id=alert.alert_id,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to store alert {alert.alert_id}: {e}"
            ) from e

    async def get_alert(self, alert_id: str) -> Optional[Alert]:
        """
        Retrieve an alert by ID.

        Args:
            alert_id: The unique alert identifier.

        Returns:
            Optional[Alert]: The alert if found, None otherwise.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> alert = await client.get_alert("abc123")
            >>> if alert:
            ...     print(f"Alert type: {alert.alert_type}")
        """
        client = self._require_connection()
        key = self._alert_key(alert_id)

        try:
            data = await client.get(key)

            if data is None:
                logger.debug("alert_not_found", alert_id=alert_id)
                return None

            alert = Alert.model_validate_json(data)

            logger.debug(
                "alert_retrieved",
                alert_id=alert_id,
                alert_type=alert.alert_type,
            )

            return alert

        except RedisError as e:
            logger.error(
                "alert_retrieve_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve alert {alert_id}: {e}"
            ) from e

    async def get_active_alerts(self) -> List[Alert]:
        """
        Retrieve all active (non-resolved) alerts.

        Returns:
            List[Alert]: List of active alerts, sorted by triggered_at descending.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> alerts = await client.get_active_alerts()
            >>> for alert in alerts:
            ...     print(f"{alert.priority.value}: {alert.alert_type}")
        """
        client = self._require_connection()

        try:
            # Get all active alert IDs
            alert_ids = await client.smembers(self.KEY_ALERTS_ACTIVE)

            if not alert_ids:
                return []

            # Fetch all alerts in batch
            keys = [self._alert_key(aid) for aid in alert_ids]
            values = await client.mget(keys)

            alerts: List[Alert] = []
            for data in values:
                if data is not None:
                    try:
                        alert = Alert.model_validate_json(data)
                        if alert.is_active:  # Double-check
                            alerts.append(alert)
                    except Exception as e:
                        logger.warning(
                            "alert_parse_failed",
                            error=str(e),
                        )

            # Sort by triggered_at descending (newest first)
            alerts.sort(key=lambda a: a.triggered_at, reverse=True)

            logger.debug(
                "active_alerts_retrieved",
                count=len(alerts),
            )

            return alerts

        except RedisError as e:
            logger.error(
                "active_alerts_retrieve_failed",
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve active alerts: {e}"
            ) from e

    async def get_alerts_by_priority(self, priority: AlertPriority) -> List[Alert]:
        """
        Retrieve all active alerts with a specific priority.

        Args:
            priority: The AlertPriority to filter by.

        Returns:
            List[Alert]: List of alerts with the specified priority.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> p1_alerts = await client.get_alerts_by_priority(AlertPriority.P1)
            >>> print(f"Critical alerts: {len(p1_alerts)}")
        """
        client = self._require_connection()
        priority_key = self._alerts_by_priority_key(priority)

        try:
            # Intersect with active set to only get active alerts
            alert_ids = await client.sinter(self.KEY_ALERTS_ACTIVE, priority_key)

            if not alert_ids:
                return []

            # Fetch all alerts in batch
            keys = [self._alert_key(aid) for aid in alert_ids]
            values = await client.mget(keys)

            alerts: List[Alert] = []
            for data in values:
                if data is not None:
                    try:
                        alert = Alert.model_validate_json(data)
                        if alert.is_active and alert.priority == priority:
                            alerts.append(alert)
                    except Exception as e:
                        logger.warning(
                            "alert_parse_failed",
                            error=str(e),
                        )

            # Sort by triggered_at descending
            alerts.sort(key=lambda a: a.triggered_at, reverse=True)

            logger.debug(
                "alerts_by_priority_retrieved",
                priority=priority.value,
                count=len(alerts),
            )

            return alerts

        except RedisError as e:
            logger.error(
                "alerts_by_priority_retrieve_failed",
                priority=priority.value,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve alerts by priority {priority.value}: {e}"
            ) from e

    async def get_alerts_by_instrument(self, instrument: str) -> List[Alert]:
        """
        Retrieve all active alerts for a specific instrument.

        Args:
            instrument: The instrument identifier to filter by.

        Returns:
            List[Alert]: List of alerts for the specified instrument.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> alerts = await client.get_alerts_by_instrument("BTC-USDT-PERP")
            >>> print(f"Alerts for BTC perp: {len(alerts)}")
        """
        client = self._require_connection()
        instrument_key = self._alerts_by_instrument_key(instrument)

        try:
            # Intersect with active set to only get active alerts
            alert_ids = await client.sinter(self.KEY_ALERTS_ACTIVE, instrument_key)

            if not alert_ids:
                return []

            # Fetch all alerts in batch
            keys = [self._alert_key(aid) for aid in alert_ids]
            values = await client.mget(keys)

            alerts: List[Alert] = []
            for data in values:
                if data is not None:
                    try:
                        alert = Alert.model_validate_json(data)
                        if alert.is_active and alert.instrument == instrument:
                            alerts.append(alert)
                    except Exception as e:
                        logger.warning(
                            "alert_parse_failed",
                            error=str(e),
                        )

            # Sort by triggered_at descending
            alerts.sort(key=lambda a: a.triggered_at, reverse=True)

            logger.debug(
                "alerts_by_instrument_retrieved",
                instrument=instrument,
                count=len(alerts),
            )

            return alerts

        except RedisError as e:
            logger.error(
                "alerts_by_instrument_retrieve_failed",
                instrument=instrument,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve alerts by instrument {instrument}: {e}"
            ) from e

    async def remove_alert(self, alert_id: str) -> None:
        """
        Remove an alert and clean up index entries.

        Removes the alert from storage and all index sets.

        Args:
            alert_id: The unique alert identifier to remove.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> await client.remove_alert("abc123")
        """
        client = self._require_connection()
        key = self._alert_key(alert_id)

        try:
            # First, get the alert to know which indexes to clean
            alert = await self.get_alert(alert_id)

            async with client.pipeline(transaction=True) as pipe:
                # Remove the alert
                pipe.delete(key)

                # Remove from active set
                pipe.srem(self.KEY_ALERTS_ACTIVE, alert_id)

                # Remove from priority and instrument sets if we have the alert
                if alert:
                    pipe.srem(
                        self._alerts_by_priority_key(alert.priority),
                        alert_id,
                    )
                    pipe.srem(
                        self._alerts_by_instrument_key(alert.instrument),
                        alert_id,
                    )

                await pipe.execute()

            logger.info(
                "alert_removed",
                alert_id=alert_id,
            )

        except RedisError as e:
            logger.error(
                "alert_remove_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to remove alert {alert_id}: {e}"
            ) from e

    # =========================================================================
    # SYSTEM HEALTH
    # =========================================================================

    def _health_key(self, exchange: str) -> str:
        """Generate Redis key for health status."""
        return f"{self.KEY_HEALTH}:{exchange}"

    async def set_health(self, health: HealthStatus) -> None:
        """
        Store exchange health status.

        Updates every second by the data ingestion service.

        Args:
            health: The HealthStatus to store.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> health = HealthStatus(
            ...     exchange="binance",
            ...     status=ConnectionStatus.CONNECTED,
            ...     last_message_at=datetime.utcnow(),
            ...     message_count=12345,
            ...     lag_ms=23,
            ...     reconnect_count=0,
            ...     gaps_last_hour=0,
            ... )
            >>> await client.set_health(health)
        """
        client = self._require_connection()
        key = self._health_key(health.exchange)

        try:
            serialized = health.model_dump_json()
            # Health status has short TTL - if not updated, connection is likely dead
            ttl = self.storage_config.current_state_ttl_seconds

            await client.setex(key, ttl, serialized)

            logger.debug(
                "health_stored",
                exchange=health.exchange,
                status=health.status.value,
                lag_ms=health.lag_ms,
            )

        except RedisError as e:
            logger.error(
                "health_store_failed",
                exchange=health.exchange,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to store health for {health.exchange}: {e}"
            ) from e

    async def get_health(self, exchange: str) -> Optional[HealthStatus]:
        """
        Retrieve health status for an exchange.

        Args:
            exchange: Exchange identifier.

        Returns:
            Optional[HealthStatus]: Health status if found, None otherwise.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> health = await client.get_health("binance")
            >>> if health:
            ...     print(f"Status: {health.status.value}")
        """
        client = self._require_connection()
        key = self._health_key(exchange)

        try:
            data = await client.get(key)

            if data is None:
                logger.debug("health_not_found", exchange=exchange)
                return None

            health = HealthStatus.model_validate_json(data)

            logger.debug(
                "health_retrieved",
                exchange=exchange,
                status=health.status.value,
            )

            return health

        except RedisError as e:
            logger.error(
                "health_retrieve_failed",
                exchange=exchange,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve health for {exchange}: {e}"
            ) from e

    async def get_all_health(self) -> Dict[str, HealthStatus]:
        """
        Retrieve health status for all exchanges.

        Scans for all health keys and returns a dictionary.

        Returns:
            Dict[str, HealthStatus]: Health status keyed by exchange name.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> all_health = await client.get_all_health()
            >>> for exchange, health in all_health.items():
            ...     print(f"{exchange}: {health.status.value}")
        """
        client = self._require_connection()

        try:
            # Scan for health keys
            pattern = f"{self.KEY_HEALTH}:*"
            keys: List[str] = []

            # Use scan_iter to handle large keyspaces efficiently
            async for key in client.scan_iter(match=pattern, count=100):
                keys.append(key)

            if not keys:
                return {}

            # Fetch all in batch
            values = await client.mget(keys)

            result: Dict[str, HealthStatus] = {}
            for key, data in zip(keys, values):
                if data is not None:
                    try:
                        health = HealthStatus.model_validate_json(data)
                        result[health.exchange] = health
                    except Exception as e:
                        logger.warning(
                            "health_parse_failed",
                            key=key,
                            error=str(e),
                        )

            logger.debug(
                "all_health_retrieved",
                count=len(result),
            )

            return result

        except RedisError as e:
            logger.error(
                "all_health_retrieve_failed",
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to retrieve all health: {e}"
            ) from e

    # =========================================================================
    # PUB/SUB
    # =========================================================================

    async def publish_orderbook_update(self, snapshot: OrderBookSnapshot) -> int:
        """
        Publish an order book update to subscribers.

        Args:
            snapshot: The OrderBookSnapshot to publish.

        Returns:
            int: Number of subscribers that received the message.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> count = await client.publish_orderbook_update(snapshot)
            >>> print(f"Notified {count} subscribers")
        """
        client = self._require_connection()

        try:
            # Publish a lightweight update (not full snapshot for performance)
            update = {
                "exchange": snapshot.exchange,
                "instrument": snapshot.instrument,
                "timestamp": snapshot.timestamp.isoformat(),
                "sequence_id": snapshot.sequence_id,
                "best_bid": str(snapshot.best_bid) if snapshot.best_bid else None,
                "best_ask": str(snapshot.best_ask) if snapshot.best_ask else None,
                "spread_bps": str(snapshot.spread_bps) if snapshot.spread_bps else None,
            }

            message = json.dumps(update)
            count = await client.publish(self.CHANNEL_ORDERBOOK, message)

            logger.debug(
                "orderbook_update_published",
                exchange=snapshot.exchange,
                instrument=snapshot.instrument,
                subscribers=count,
            )

            return int(count)

        except RedisError as e:
            logger.error(
                "orderbook_publish_failed",
                exchange=snapshot.exchange,
                instrument=snapshot.instrument,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to publish order book update: {e}"
            ) from e

    async def publish_alert(self, alert: Alert) -> int:
        """
        Publish an alert to subscribers.

        Args:
            alert: The Alert to publish.

        Returns:
            int: Number of subscribers that received the message.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> count = await client.publish_alert(alert)
            >>> print(f"Notified {count} subscribers")
        """
        client = self._require_connection()

        try:
            message = alert.model_dump_json()
            count = await client.publish(self.CHANNEL_ALERTS, message)

            logger.debug(
                "alert_published",
                alert_id=alert.alert_id,
                alert_type=alert.alert_type,
                subscribers=count,
            )

            return int(count)

        except RedisError as e:
            logger.error(
                "alert_publish_failed",
                alert_id=alert.alert_id,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to publish alert: {e}"
            ) from e

    async def publish_health_update(self, health: HealthStatus) -> int:
        """
        Publish a health update to subscribers.

        Args:
            health: The HealthStatus to publish.

        Returns:
            int: Number of subscribers that received the message.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.

        Example:
            >>> count = await client.publish_health_update(health)
            >>> print(f"Notified {count} subscribers")
        """
        client = self._require_connection()

        try:
            message = health.model_dump_json()
            count = await client.publish(self.CHANNEL_HEALTH, message)

            logger.debug(
                "health_update_published",
                exchange=health.exchange,
                status=health.status.value,
                subscribers=count,
            )

            return int(count)

        except RedisError as e:
            logger.error(
                "health_publish_failed",
                exchange=health.exchange,
                error=str(e),
            )
            raise RedisOperationError(
                f"Failed to publish health update: {e}"
            ) from e

    @asynccontextmanager
    async def subscribe(
        self, channels: List[str]
    ) -> AsyncIterator[AsyncIterator[Dict[str, Any]]]:
        """
        Subscribe to Redis pub/sub channels.

        Context manager that yields an async iterator of messages.
        Available channels:
            - updates:orderbook - Order book updates
            - updates:alerts - Alert notifications
            - updates:health - Health status updates

        Args:
            channels: List of channel names to subscribe to.

        Yields:
            AsyncIterator[Dict[str, Any]]: Async iterator of parsed messages.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If subscription fails.

        Example:
            >>> async with client.subscribe(["updates:alerts"]) as messages:
            ...     async for message in messages:
            ...         print(f"Received: {message}")
        """
        client = self._require_connection()
        pubsub: PubSub = client.pubsub()

        try:
            await pubsub.subscribe(*channels)

            logger.info(
                "pubsub_subscribed",
                channels=channels,
            )

            async def message_iterator() -> AsyncIterator[Dict[str, Any]]:
                """Iterate over messages from subscribed channels."""
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            yield {
                                "channel": message["channel"],
                                "data": data,
                            }
                        except json.JSONDecodeError as e:
                            logger.warning(
                                "pubsub_message_parse_failed",
                                channel=message["channel"],
                                error=str(e),
                            )

            yield message_iterator()

        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

            logger.info(
                "pubsub_unsubscribed",
                channels=channels,
            )

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def flush_db(self) -> None:
        """
        Flush the current Redis database.

        WARNING: This deletes all data in the database. Use only for testing.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.
        """
        client = self._require_connection()

        try:
            await client.flushdb()
            logger.warning("redis_db_flushed", db=self.config.db)

        except RedisError as e:
            logger.error("redis_flush_failed", error=str(e))
            raise RedisOperationError(f"Failed to flush database: {e}") from e

    async def get_info(self) -> Dict[str, Any]:
        """
        Get Redis server information.

        Returns:
            Dict[str, Any]: Redis INFO command output.

        Raises:
            RedisConnectionException: If not connected.
            RedisOperationError: If the operation fails.
        """
        client = self._require_connection()

        try:
            info = await client.info()
            return dict(info)

        except RedisError as e:
            logger.error("redis_info_failed", error=str(e))
            raise RedisOperationError(f"Failed to get Redis info: {e}") from e
