"""
Async PostgreSQL/TimescaleDB client for historical data storage.

This module provides a PostgreSQL client for storing and querying historical
time-series data including order book snapshots, computed metrics, alerts,
and data gap markers. It leverages TimescaleDB features for efficient
time-series operations.

Key Tables:
    - order_book_snapshots: Historical order book data with computed metrics
    - metrics: Time-series of computed metrics with z-scores
    - basis_metrics: Perpetual-spot basis metrics
    - alerts: Alert history with lifecycle tracking
    - data_gaps: Records of missing data periods

Note:
    This module is owned by the ARCHITECT agent.
    All financial values are stored using DECIMAL for precision.

Example:
    >>> from src.config.models import PostgresConnectionConfig
    >>> from src.storage.postgres_client import PostgresClient
    >>>
    >>> config = PostgresConnectionConfig(
    ...     url="postgresql://surveillance:password@localhost:5432/surveillance"
    ... )
    >>> client = PostgresClient(config)
    >>> await client.connect()
    >>>
    >>> # Insert order book snapshots
    >>> await client.insert_orderbook_snapshots(snapshots)
    >>>
    >>> # Query historical data
    >>> metrics = await client.query_spread_metrics(
    ...     exchange="binance",
    ...     instrument="BTC-USDT-PERP",
    ...     start_time=start,
    ...     end_time=end,
    ... )
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple

import structlog

try:
    import asyncpg
    from asyncpg import Connection, Pool, Record
    from asyncpg.exceptions import (
        PostgresError,
        InterfaceError,
        ConnectionDoesNotExistError,
        TooManyConnectionsError,
    )
except ImportError as e:
    raise ImportError(
        "asyncpg is required for PostgresClient. Install with: pip install asyncpg"
    ) from e

from src.config.models import PostgresConnectionConfig, PostgresStorageConfig
from src.models.alerts import Alert, AlertCondition, AlertPriority, AlertSeverity
from src.models.health import GapMarker
from src.models.metrics import BasisMetrics, DepthMetrics, SpreadMetrics
from src.models.orderbook import OrderBookSnapshot, PriceLevel

logger = structlog.get_logger(__name__)


class PostgresClientError(Exception):
    """Base exception for PostgreSQL client errors."""

    pass


class PostgresConnectionException(PostgresClientError):
    """Raised when PostgreSQL connection fails."""

    pass


class PostgresOperationError(PostgresClientError):
    """Raised when a PostgreSQL operation fails."""

    pass


def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
    """
    Convert Decimal to float for database storage.

    Args:
        value: Decimal value to convert.

    Returns:
        Optional[float]: Float value or None.
    """
    if value is None:
        return None
    return float(value)


def _price_levels_to_json(levels: List[PriceLevel]) -> str:
    """
    Convert price levels to JSON string for database storage.

    Args:
        levels: List of PriceLevel objects.

    Returns:
        str: JSON string representation.
    """
    return json.dumps([
        {"price": str(level.price), "quantity": str(level.quantity)}
        for level in levels
    ])


def _json_to_price_levels(json_str: Optional[str]) -> List[PriceLevel]:
    """
    Convert JSON string back to price levels.

    Args:
        json_str: JSON string from database.

    Returns:
        List[PriceLevel]: List of PriceLevel objects.
    """
    if not json_str:
        return []

    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    return [
        PriceLevel(
            price=Decimal(str(item["price"])),
            quantity=Decimal(str(item["quantity"])),
        )
        for item in data
    ]


class PostgresClient:
    """
    Async PostgreSQL/TimescaleDB client for historical surveillance data.

    Provides methods for inserting and querying order book snapshots,
    computed metrics, alerts, and data gap markers. Optimized for
    TimescaleDB time-series operations with efficient batch inserts.

    Attributes:
        config: PostgreSQL connection configuration.
        storage_config: PostgreSQL storage configuration (retention, etc.).
        _pool: Connection pool for efficient connection reuse.
        _connected: Whether the client is connected.

    Example:
        >>> config = PostgresConnectionConfig(url="postgresql://...")
        >>> storage_config = PostgresStorageConfig()
        >>> client = PostgresClient(config, storage_config)
        >>> await client.connect()
        >>> try:
        ...     await client.insert_orderbook_snapshots(snapshots)
        ... finally:
        ...     await client.disconnect()
    """

    # Default batch size for bulk inserts
    DEFAULT_BATCH_SIZE = 100

    # Maximum retries for transient errors
    MAX_RETRIES = 3

    # Retry delay in seconds
    RETRY_DELAY = 0.5

    def __init__(
        self,
        config: PostgresConnectionConfig,
        storage_config: Optional[PostgresStorageConfig] = None,
    ) -> None:
        """
        Initialize the PostgreSQL client.

        Args:
            config: PostgreSQL connection configuration containing URL and pool settings.
            storage_config: Optional storage configuration for retention policies.
                           Defaults to PostgresStorageConfig() if not provided.

        Example:
            >>> config = PostgresConnectionConfig(
            ...     url="postgresql://surveillance:password@localhost:5432/surveillance",
            ...     pool_size=5,
            ...     max_overflow=10,
            ...     pool_timeout=30,
            ... )
            >>> client = PostgresClient(config)
        """
        self.config = config
        self.storage_config = storage_config or PostgresStorageConfig()
        self._pool: Optional[Pool] = None
        self._connected: bool = False

        logger.info(
            "postgres_client_initialized",
            url=self._sanitize_url(config.url),
            pool_size=config.pool_size,
        )

    def _sanitize_url(self, url: str) -> str:
        """Sanitize URL for logging (remove password)."""
        if "@" in url:
            # Remove password from URL
            parts = url.split("@")
            if ":" in parts[0]:
                user_part = parts[0].rsplit(":", 1)[0]
                return f"{user_part}:***@{parts[1]}"
        return url

    @property
    def is_connected(self) -> bool:
        """
        Check if the client is connected to PostgreSQL.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self._connected and self._pool is not None

    async def connect(self) -> None:
        """
        Establish connection pool to PostgreSQL.

        Creates an asyncpg connection pool with the configured settings.
        Uses DSN parsing to extract connection parameters.

        Raises:
            PostgresConnectionException: If connection fails.

        Example:
            >>> client = PostgresClient(config)
            >>> await client.connect()
            >>> assert client.is_connected
        """
        if self._connected:
            logger.warning("postgres_already_connected")
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self.config.url,
                min_size=1,
                max_size=self.config.pool_size + self.config.max_overflow,
                command_timeout=self.config.pool_timeout,
                # Custom type converters for Decimal handling
                init=self._init_connection,
            )

            # Verify connection with a simple query
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            self._connected = True

            logger.info(
                "postgres_connected",
                url=self._sanitize_url(self.config.url),
            )

        except (PostgresError, OSError, asyncio.TimeoutError) as e:
            self._connected = False
            logger.error(
                "postgres_connection_failed",
                url=self._sanitize_url(self.config.url),
                error=str(e),
            )
            raise PostgresConnectionException(
                f"Failed to connect to PostgreSQL: {e}"
            ) from e

    async def _init_connection(self, conn: Connection) -> None:
        """
        Initialize connection with custom type handling.

        Args:
            conn: asyncpg Connection to initialize.
        """
        # Set timezone to UTC for consistent timestamps
        await conn.execute("SET timezone = 'UTC'")

    async def disconnect(self) -> None:
        """
        Close PostgreSQL connection pool and release resources.

        Safe to call multiple times.

        Example:
            >>> await client.disconnect()
            >>> assert not client.is_connected
        """
        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception as e:
                logger.warning("postgres_close_error", error=str(e))
            finally:
                self._pool = None

        self._connected = False
        logger.info("postgres_disconnected")

    async def ping(self) -> bool:
        """
        Check PostgreSQL connection health.

        Returns:
            bool: True if PostgreSQL responds, False otherwise.

        Example:
            >>> if await client.ping():
            ...     print("PostgreSQL is healthy")
        """
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.warning("postgres_ping_failed", error=str(e))
            return False

    @asynccontextmanager
    async def _acquire_connection(self) -> AsyncIterator[Connection]:
        """
        Acquire a connection from the pool with error handling.

        Yields:
            Connection: asyncpg connection from the pool.

        Raises:
            PostgresConnectionException: If not connected or pool exhausted.
        """
        if not self._connected or self._pool is None:
            raise PostgresConnectionException("PostgreSQL client is not connected")

        try:
            async with self._pool.acquire() as conn:
                yield conn
        except TooManyConnectionsError as e:
            logger.error("postgres_pool_exhausted", error=str(e))
            raise PostgresConnectionException(
                f"Connection pool exhausted: {e}"
            ) from e
        except (ConnectionDoesNotExistError, InterfaceError) as e:
            logger.error("postgres_connection_lost", error=str(e))
            self._connected = False
            raise PostgresConnectionException(
                f"Connection lost: {e}"
            ) from e

    async def _execute_with_retry(
        self,
        operation: str,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a database operation with retry logic for transient errors.

        Args:
            operation: Name of the operation for logging.
            func: Async function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            Any: Result of the function call.

        Raises:
            PostgresOperationError: If all retries fail.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return await func(*args, **kwargs)
            except (PostgresError, ConnectionDoesNotExistError, InterfaceError) as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        "postgres_operation_retry",
                        operation=operation,
                        attempt=attempt + 1,
                        max_retries=self.MAX_RETRIES,
                        error=str(e),
                    )
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(
                        "postgres_operation_failed",
                        operation=operation,
                        error=str(e),
                    )

        raise PostgresOperationError(
            f"Operation '{operation}' failed after {self.MAX_RETRIES} attempts: {last_error}"
        )

    # =========================================================================
    # ORDER BOOK SNAPSHOTS
    # =========================================================================

    async def insert_orderbook_snapshots(
        self,
        snapshots: List[OrderBookSnapshot],
        batch_size: Optional[int] = None,
    ) -> int:
        """
        Batch insert order book snapshots to the database.

        Uses executemany for efficient batch inserts. Automatically computes
        depth metrics from the price levels and stores them alongside the raw data.

        Args:
            snapshots: List of OrderBookSnapshot objects to insert.
            batch_size: Optional batch size for inserts. Defaults to DEFAULT_BATCH_SIZE.

        Returns:
            int: Number of rows inserted.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> snapshots = [snapshot1, snapshot2, snapshot3]
            >>> count = await client.insert_orderbook_snapshots(snapshots)
            >>> print(f"Inserted {count} snapshots")
        """
        if not snapshots:
            return 0

        batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        start_time = time.monotonic()
        total_inserted = 0

        async def _insert_batch(batch: List[OrderBookSnapshot]) -> int:
            async with self._acquire_connection() as conn:
                # Prepare data for executemany
                records = []
                for snapshot in batch:
                    # Compute depth metrics for storage
                    depth_5bps_bid = snapshot.depth_at_bps(5, "bid")
                    depth_5bps_ask = snapshot.depth_at_bps(5, "ask")
                    depth_10bps_bid = snapshot.depth_at_bps(10, "bid")
                    depth_10bps_ask = snapshot.depth_at_bps(10, "ask")
                    depth_25bps_bid = snapshot.depth_at_bps(25, "bid")
                    depth_25bps_ask = snapshot.depth_at_bps(25, "ask")

                    # Compute imbalance
                    total_bid = snapshot.total_bid_notional()
                    total_ask = snapshot.total_ask_notional()
                    imbalance = (
                        (total_bid - total_ask) / (total_bid + total_ask)
                        if (total_bid + total_ask) > 0
                        else Decimal("0")
                    )

                    records.append((
                        snapshot.exchange,
                        snapshot.instrument,
                        snapshot.timestamp,
                        snapshot.local_timestamp,
                        snapshot.sequence_id,
                        _decimal_to_float(snapshot.best_bid),
                        _decimal_to_float(snapshot.best_ask),
                        _decimal_to_float(snapshot.mid_price),
                        _decimal_to_float(snapshot.spread),
                        _decimal_to_float(snapshot.spread_bps),
                        _decimal_to_float(depth_5bps_bid),
                        _decimal_to_float(depth_5bps_ask),
                        _decimal_to_float(depth_5bps_bid + depth_5bps_ask),
                        _decimal_to_float(depth_10bps_bid),
                        _decimal_to_float(depth_10bps_ask),
                        _decimal_to_float(depth_10bps_bid + depth_10bps_ask),
                        _decimal_to_float(depth_25bps_bid),
                        _decimal_to_float(depth_25bps_ask),
                        _decimal_to_float(depth_25bps_bid + depth_25bps_ask),
                        _decimal_to_float(imbalance),
                        _price_levels_to_json(snapshot.bids),
                        _price_levels_to_json(snapshot.asks),
                    ))

                await conn.executemany(
                    """
                    INSERT INTO order_book_snapshots (
                        exchange, instrument, timestamp, local_timestamp, sequence_id,
                        best_bid, best_ask, mid_price, spread_abs, spread_bps,
                        depth_5bps_bid, depth_5bps_ask, depth_5bps_total,
                        depth_10bps_bid, depth_10bps_ask, depth_10bps_total,
                        depth_25bps_bid, depth_25bps_ask, depth_25bps_total,
                        imbalance, bids_json, asks_json
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
                    )
                    """,
                    records,
                )
                return len(records)

        # Process in batches
        for i in range(0, len(snapshots), batch_size):
            batch = snapshots[i:i + batch_size]
            count = await self._execute_with_retry(
                "insert_orderbook_snapshots",
                _insert_batch,
                batch,
            )
            total_inserted += count

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "orderbook_snapshots_inserted",
            count=total_inserted,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return total_inserted

    async def query_orderbook_snapshots(
        self,
        exchange: str,
        instrument: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> List[OrderBookSnapshot]:
        """
        Query historical order book snapshots.

        Args:
            exchange: Exchange identifier (e.g., "binance").
            instrument: Instrument identifier (e.g., "BTC-USDT-PERP").
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            limit: Maximum number of results (default: 1000).

        Returns:
            List[OrderBookSnapshot]: List of snapshots, ordered by timestamp descending.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> snapshots = await client.query_orderbook_snapshots(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     start_time=datetime(2025, 1, 1),
            ...     end_time=datetime(2025, 1, 2),
            ...     limit=100,
            ... )
        """
        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT
                        exchange, instrument, timestamp, local_timestamp, sequence_id,
                        bids_json, asks_json
                    FROM order_book_snapshots
                    WHERE exchange = $1
                      AND instrument = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    ORDER BY timestamp DESC
                    LIMIT $5
                    """,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                    limit,
                )

        rows = await self._execute_with_retry(
            "query_orderbook_snapshots",
            _query,
        )

        snapshots = []
        for row in rows:
            try:
                snapshot = OrderBookSnapshot(
                    exchange=row["exchange"],
                    instrument=row["instrument"],
                    timestamp=row["timestamp"],
                    local_timestamp=row["local_timestamp"],
                    sequence_id=row["sequence_id"],
                    bids=_json_to_price_levels(row["bids_json"]),
                    asks=_json_to_price_levels(row["asks_json"]),
                )
                snapshots.append(snapshot)
            except Exception as e:
                logger.warning(
                    "orderbook_snapshot_parse_failed",
                    error=str(e),
                )

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "orderbook_snapshots_queried",
            exchange=exchange,
            instrument=instrument,
            count=len(snapshots),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return snapshots

    # =========================================================================
    # SPREAD METRICS
    # =========================================================================

    async def insert_spread_metrics(
        self,
        metrics: List[Tuple[str, str, datetime, SpreadMetrics]],
        batch_size: Optional[int] = None,
    ) -> int:
        """
        Batch insert spread metrics to the database.

        Args:
            metrics: List of tuples (exchange, instrument, timestamp, SpreadMetrics).
            batch_size: Optional batch size for inserts.

        Returns:
            int: Number of rows inserted.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = [
            ...     ("binance", "BTC-USDT-PERP", datetime.utcnow(), spread_metrics),
            ... ]
            >>> count = await client.insert_spread_metrics(metrics)
        """
        if not metrics:
            return 0

        batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        start_time = time.monotonic()
        total_inserted = 0

        async def _insert_batch(
            batch: List[Tuple[str, str, datetime, SpreadMetrics]]
        ) -> int:
            async with self._acquire_connection() as conn:
                # Insert spread_abs, spread_bps, and mid_price as separate metric rows
                records = []
                for exchange, instrument, timestamp, m in batch:
                    # spread_bps metric
                    records.append((
                        "spread_bps",
                        exchange,
                        instrument,
                        timestamp,
                        _decimal_to_float(m.spread_bps),
                        _decimal_to_float(m.zscore),
                    ))
                    # spread_abs metric
                    records.append((
                        "spread_abs",
                        exchange,
                        instrument,
                        timestamp,
                        _decimal_to_float(m.spread_abs),
                        None,
                    ))
                    # mid_price metric
                    records.append((
                        "mid_price",
                        exchange,
                        instrument,
                        timestamp,
                        _decimal_to_float(m.mid_price),
                        None,
                    ))

                await conn.executemany(
                    """
                    INSERT INTO metrics (metric_name, exchange, instrument, timestamp, value, zscore)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    records,
                )
                return len(batch)

        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i + batch_size]
            count = await self._execute_with_retry(
                "insert_spread_metrics",
                _insert_batch,
                batch,
            )
            total_inserted += count

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "spread_metrics_inserted",
            count=total_inserted,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return total_inserted

    async def query_spread_metrics(
        self,
        exchange: str,
        instrument: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query historical spread metrics.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            limit: Maximum number of results.

        Returns:
            List[Dict[str, Any]]: List of metric dictionaries with timestamp, value, zscore.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_spread_metrics(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     start_time=start,
            ...     end_time=end,
            ... )
        """
        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT timestamp, value, zscore
                    FROM metrics
                    WHERE metric_name = 'spread_bps'
                      AND exchange = $1
                      AND instrument = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    ORDER BY timestamp DESC
                    LIMIT $5
                    """,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                    limit,
                )

        rows = await self._execute_with_retry(
            "query_spread_metrics",
            _query,
        )

        results = [
            {
                "timestamp": row["timestamp"],
                "spread_bps": Decimal(str(row["value"])) if row["value"] else None,
                "zscore": Decimal(str(row["zscore"])) if row["zscore"] else None,
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "spread_metrics_queried",
            exchange=exchange,
            instrument=instrument,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    # =========================================================================
    # DEPTH METRICS
    # =========================================================================

    async def insert_depth_metrics(
        self,
        metrics: List[Tuple[str, str, datetime, DepthMetrics]],
        batch_size: Optional[int] = None,
    ) -> int:
        """
        Batch insert depth metrics to the database.

        Args:
            metrics: List of tuples (exchange, instrument, timestamp, DepthMetrics).
            batch_size: Optional batch size for inserts.

        Returns:
            int: Number of rows inserted.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = [
            ...     ("binance", "BTC-USDT-PERP", datetime.utcnow(), depth_metrics),
            ... ]
            >>> count = await client.insert_depth_metrics(metrics)
        """
        if not metrics:
            return 0

        batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        start_time = time.monotonic()
        total_inserted = 0

        async def _insert_batch(
            batch: List[Tuple[str, str, datetime, DepthMetrics]]
        ) -> int:
            async with self._acquire_connection() as conn:
                records = []
                for exchange, instrument, timestamp, m in batch:
                    # Insert depth at various levels as separate metrics
                    for bps in [5, 10, 25]:
                        for side in ["bid", "ask", "total"]:
                            metric_name = f"depth_{bps}bps_{side}"
                            value = m.depth_at_level(bps, side)
                            records.append((
                                metric_name,
                                exchange,
                                instrument,
                                timestamp,
                                _decimal_to_float(value),
                                None,
                            ))
                    # Imbalance metric
                    records.append((
                        "imbalance",
                        exchange,
                        instrument,
                        timestamp,
                        _decimal_to_float(m.imbalance),
                        None,
                    ))

                await conn.executemany(
                    """
                    INSERT INTO metrics (metric_name, exchange, instrument, timestamp, value, zscore)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    records,
                )
                return len(batch)

        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i + batch_size]
            count = await self._execute_with_retry(
                "insert_depth_metrics",
                _insert_batch,
                batch,
            )
            total_inserted += count

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "depth_metrics_inserted",
            count=total_inserted,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return total_inserted

    async def query_depth_metrics(
        self,
        exchange: str,
        instrument: str,
        start_time: datetime,
        end_time: datetime,
        bps_level: int = 10,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query historical depth metrics at a specific bps level.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            bps_level: Basis points level (5, 10, or 25).
            limit: Maximum number of results.

        Returns:
            List[Dict[str, Any]]: List of metric dictionaries.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_depth_metrics(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     start_time=start,
            ...     end_time=end,
            ...     bps_level=10,
            ... )
        """
        start_query_time = time.monotonic()
        metric_name = f"depth_{bps_level}bps_total"

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT timestamp, value
                    FROM metrics
                    WHERE metric_name = $1
                      AND exchange = $2
                      AND instrument = $3
                      AND timestamp >= $4
                      AND timestamp <= $5
                    ORDER BY timestamp DESC
                    LIMIT $6
                    """,
                    metric_name,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                    limit,
                )

        rows = await self._execute_with_retry(
            "query_depth_metrics",
            _query,
        )

        results = [
            {
                "timestamp": row["timestamp"],
                "depth_total": Decimal(str(row["value"])) if row["value"] else None,
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "depth_metrics_queried",
            exchange=exchange,
            instrument=instrument,
            bps_level=bps_level,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    # =========================================================================
    # BASIS METRICS
    # =========================================================================

    async def insert_basis_metrics(
        self,
        metrics: List[Tuple[str, str, str, datetime, BasisMetrics]],
        batch_size: Optional[int] = None,
    ) -> int:
        """
        Batch insert basis metrics to the database.

        Args:
            metrics: List of tuples (perp_instrument, spot_instrument, exchange,
                     timestamp, BasisMetrics).
            batch_size: Optional batch size for inserts.

        Returns:
            int: Number of rows inserted.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = [
            ...     ("BTC-USDT-PERP", "BTC-USDT-SPOT", "binance",
            ...      datetime.utcnow(), basis_metrics),
            ... ]
            >>> count = await client.insert_basis_metrics(metrics)
        """
        if not metrics:
            return 0

        batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        start_time = time.monotonic()
        total_inserted = 0

        async def _insert_batch(
            batch: List[Tuple[str, str, str, datetime, BasisMetrics]]
        ) -> int:
            async with self._acquire_connection() as conn:
                records = [
                    (
                        perp_inst,
                        spot_inst,
                        exchange,
                        timestamp,
                        _decimal_to_float(m.perp_mid),
                        _decimal_to_float(m.spot_mid),
                        _decimal_to_float(m.basis_abs),
                        _decimal_to_float(m.basis_bps),
                        _decimal_to_float(m.zscore),
                    )
                    for perp_inst, spot_inst, exchange, timestamp, m in batch
                ]

                await conn.executemany(
                    """
                    INSERT INTO basis_metrics (
                        perp_instrument, spot_instrument, exchange, timestamp,
                        perp_mid, spot_mid, basis_abs, basis_bps, zscore
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    records,
                )
                return len(records)

        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i + batch_size]
            count = await self._execute_with_retry(
                "insert_basis_metrics",
                _insert_batch,
                batch,
            )
            total_inserted += count

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "basis_metrics_inserted",
            count=total_inserted,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return total_inserted

    async def query_basis_metrics(
        self,
        perp_instrument: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query historical basis metrics.

        Args:
            perp_instrument: Perpetual instrument identifier.
            exchange: Exchange identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            limit: Maximum number of results.

        Returns:
            List[Dict[str, Any]]: List of basis metric dictionaries.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_basis_metrics(
            ...     perp_instrument="BTC-USDT-PERP",
            ...     exchange="binance",
            ...     start_time=start,
            ...     end_time=end,
            ... )
        """
        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT timestamp, perp_mid, spot_mid, basis_abs, basis_bps, zscore
                    FROM basis_metrics
                    WHERE perp_instrument = $1
                      AND exchange = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    ORDER BY timestamp DESC
                    LIMIT $5
                    """,
                    perp_instrument,
                    exchange,
                    start_time,
                    end_time,
                    limit,
                )

        rows = await self._execute_with_retry(
            "query_basis_metrics",
            _query,
        )

        results = [
            {
                "timestamp": row["timestamp"],
                "perp_mid": Decimal(str(row["perp_mid"])) if row["perp_mid"] else None,
                "spot_mid": Decimal(str(row["spot_mid"])) if row["spot_mid"] else None,
                "basis_abs": Decimal(str(row["basis_abs"])) if row["basis_abs"] else None,
                "basis_bps": Decimal(str(row["basis_bps"])) if row["basis_bps"] else None,
                "zscore": Decimal(str(row["zscore"])) if row["zscore"] else None,
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "basis_metrics_queried",
            perp_instrument=perp_instrument,
            exchange=exchange,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    # =========================================================================
    # ALERTS
    # =========================================================================

    async def insert_alert(self, alert: Alert) -> None:
        """
        Insert a new alert to the database.

        Uses UPSERT to handle duplicate alert_id gracefully (updates existing).

        Args:
            alert: The Alert to insert.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

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
            >>> await client.insert_alert(alert)
        """
        start_time = time.monotonic()

        async def _insert() -> None:
            async with self._acquire_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO alerts (
                        alert_id, alert_type, priority, severity,
                        exchange, instrument,
                        trigger_metric, trigger_value, trigger_threshold, trigger_condition,
                        zscore_value, zscore_threshold,
                        triggered_at, acknowledged_at, resolved_at, duration_seconds,
                        peak_value, peak_at,
                        escalated, escalated_at, original_priority,
                        context, resolution_type, resolution_value
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24
                    )
                    ON CONFLICT (alert_id) DO UPDATE SET
                        priority = EXCLUDED.priority,
                        acknowledged_at = EXCLUDED.acknowledged_at,
                        resolved_at = EXCLUDED.resolved_at,
                        duration_seconds = EXCLUDED.duration_seconds,
                        peak_value = EXCLUDED.peak_value,
                        peak_at = EXCLUDED.peak_at,
                        escalated = EXCLUDED.escalated,
                        escalated_at = EXCLUDED.escalated_at,
                        original_priority = EXCLUDED.original_priority,
                        resolution_type = EXCLUDED.resolution_type,
                        resolution_value = EXCLUDED.resolution_value,
                        updated_at = NOW()
                    """,
                    alert.alert_id,
                    alert.alert_type,
                    alert.priority.value,
                    alert.severity.value,
                    alert.exchange,
                    alert.instrument,
                    alert.trigger_metric,
                    _decimal_to_float(alert.trigger_value),
                    _decimal_to_float(alert.trigger_threshold),
                    alert.trigger_condition.value,
                    _decimal_to_float(alert.zscore_value),
                    _decimal_to_float(alert.zscore_threshold),
                    alert.triggered_at,
                    alert.acknowledged_at,
                    alert.resolved_at,
                    alert.duration_seconds,
                    _decimal_to_float(alert.peak_value),
                    alert.peak_at,
                    alert.escalated,
                    alert.escalated_at,
                    alert.original_priority.value if alert.original_priority else None,
                    json.dumps(alert.context) if alert.context else "{}",
                    alert.resolution_type,
                    _decimal_to_float(alert.resolution_value),
                )

        await self._execute_with_retry("insert_alert", _insert)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "alert_inserted",
            alert_id=alert.alert_id,
            alert_type=alert.alert_type,
            priority=alert.priority.value,
            elapsed_ms=round(elapsed_ms, 2),
        )

    async def update_alert_status(
        self,
        alert_id: str,
        status: str,
        resolved_at: Optional[datetime] = None,
        resolution_type: Optional[str] = None,
        resolution_value: Optional[Decimal] = None,
        duration_seconds: Optional[int] = None,
        peak_value: Optional[Decimal] = None,
        peak_at: Optional[datetime] = None,
        escalated: Optional[bool] = None,
        escalated_at: Optional[datetime] = None,
        new_priority: Optional[AlertPriority] = None,
        original_priority: Optional[AlertPriority] = None,
    ) -> None:
        """
        Update alert status and lifecycle fields.

        Args:
            alert_id: Unique alert identifier.
            status: New status (for logging, actual updates via specific fields).
            resolved_at: When the alert was resolved.
            resolution_type: How it was resolved (auto, manual, timeout).
            resolution_value: Metric value at resolution.
            duration_seconds: How long the alert was active.
            peak_value: Peak metric value during alert.
            peak_at: When peak value occurred.
            escalated: Whether the alert was escalated.
            escalated_at: When escalation occurred.
            new_priority: New priority after escalation.
            original_priority: Priority before escalation.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> await client.update_alert_status(
            ...     alert_id="abc123",
            ...     status="resolved",
            ...     resolved_at=datetime.utcnow(),
            ...     resolution_type="auto",
            ...     resolution_value=Decimal("2.5"),
            ... )
        """
        start_time = time.monotonic()

        async def _update() -> None:
            async with self._acquire_connection() as conn:
                # Build dynamic update
                updates = ["updated_at = NOW()"]
                params: List[Any] = []
                param_count = 0

                if resolved_at is not None:
                    param_count += 1
                    updates.append(f"resolved_at = ${param_count}")
                    params.append(resolved_at)

                if resolution_type is not None:
                    param_count += 1
                    updates.append(f"resolution_type = ${param_count}")
                    params.append(resolution_type)

                if resolution_value is not None:
                    param_count += 1
                    updates.append(f"resolution_value = ${param_count}")
                    params.append(_decimal_to_float(resolution_value))

                if duration_seconds is not None:
                    param_count += 1
                    updates.append(f"duration_seconds = ${param_count}")
                    params.append(duration_seconds)

                if peak_value is not None:
                    param_count += 1
                    updates.append(f"peak_value = ${param_count}")
                    params.append(_decimal_to_float(peak_value))

                if peak_at is not None:
                    param_count += 1
                    updates.append(f"peak_at = ${param_count}")
                    params.append(peak_at)

                if escalated is not None:
                    param_count += 1
                    updates.append(f"escalated = ${param_count}")
                    params.append(escalated)

                if escalated_at is not None:
                    param_count += 1
                    updates.append(f"escalated_at = ${param_count}")
                    params.append(escalated_at)

                if new_priority is not None:
                    param_count += 1
                    updates.append(f"priority = ${param_count}")
                    params.append(new_priority.value)

                if original_priority is not None:
                    param_count += 1
                    updates.append(f"original_priority = ${param_count}")
                    params.append(original_priority.value)

                # Add alert_id as the last parameter
                param_count += 1
                params.append(alert_id)

                query = f"""
                    UPDATE alerts
                    SET {', '.join(updates)}
                    WHERE alert_id = ${param_count}
                """

                await conn.execute(query, *params)

        await self._execute_with_retry("update_alert_status", _update)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "alert_status_updated",
            alert_id=alert_id,
            status=status,
            elapsed_ms=round(elapsed_ms, 2),
        )

    async def query_alerts(
        self,
        start_time: datetime,
        end_time: datetime,
        exchange: Optional[str] = None,
        instrument: Optional[str] = None,
        priority: Optional[AlertPriority] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Alert]:
        """
        Query historical alerts with filters.

        Args:
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            exchange: Optional exchange filter.
            instrument: Optional instrument filter.
            priority: Optional priority filter (P1, P2, P3).
            status: Optional status filter ("active" or "resolved").
            limit: Maximum number of results.

        Returns:
            List[Alert]: List of alerts matching the filters.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> alerts = await client.query_alerts(
            ...     start_time=start,
            ...     end_time=end,
            ...     priority=AlertPriority.P1,
            ...     status="active",
            ... )
        """
        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                # Build dynamic query with filters
                conditions = [
                    "triggered_at >= $1",
                    "triggered_at <= $2",
                ]
                params: List[Any] = [start_time, end_time]
                param_count = 2

                if exchange is not None:
                    param_count += 1
                    conditions.append(f"exchange = ${param_count}")
                    params.append(exchange)

                if instrument is not None:
                    param_count += 1
                    conditions.append(f"instrument = ${param_count}")
                    params.append(instrument)

                if priority is not None:
                    param_count += 1
                    conditions.append(f"priority = ${param_count}")
                    params.append(priority.value)

                if status == "active":
                    conditions.append("resolved_at IS NULL")
                elif status == "resolved":
                    conditions.append("resolved_at IS NOT NULL")

                param_count += 1
                params.append(limit)

                query = f"""
                    SELECT
                        alert_id, alert_type, priority, severity,
                        exchange, instrument,
                        trigger_metric, trigger_value, trigger_threshold, trigger_condition,
                        zscore_value, zscore_threshold,
                        triggered_at, acknowledged_at, resolved_at, duration_seconds,
                        peak_value, peak_at,
                        escalated, escalated_at, original_priority,
                        context, resolution_type, resolution_value
                    FROM alerts
                    WHERE {' AND '.join(conditions)}
                    ORDER BY triggered_at DESC
                    LIMIT ${param_count}
                """

                return await conn.fetch(query, *params)

        rows = await self._execute_with_retry("query_alerts", _query)

        alerts = []
        for row in rows:
            try:
                alert = Alert(
                    alert_id=row["alert_id"],
                    alert_type=row["alert_type"],
                    priority=AlertPriority(row["priority"]),
                    severity=AlertSeverity(row["severity"]),
                    exchange=row["exchange"],
                    instrument=row["instrument"],
                    trigger_metric=row["trigger_metric"],
                    trigger_value=Decimal(str(row["trigger_value"])),
                    trigger_threshold=Decimal(str(row["trigger_threshold"])),
                    trigger_condition=AlertCondition(row["trigger_condition"]),
                    zscore_value=(
                        Decimal(str(row["zscore_value"]))
                        if row["zscore_value"]
                        else None
                    ),
                    zscore_threshold=(
                        Decimal(str(row["zscore_threshold"]))
                        if row["zscore_threshold"]
                        else None
                    ),
                    triggered_at=row["triggered_at"],
                    acknowledged_at=row["acknowledged_at"],
                    resolved_at=row["resolved_at"],
                    duration_seconds=row["duration_seconds"],
                    peak_value=(
                        Decimal(str(row["peak_value"]))
                        if row["peak_value"]
                        else None
                    ),
                    peak_at=row["peak_at"],
                    escalated=row["escalated"],
                    escalated_at=row["escalated_at"],
                    original_priority=(
                        AlertPriority(row["original_priority"])
                        if row["original_priority"]
                        else None
                    ),
                    context=(
                        json.loads(row["context"])
                        if row["context"]
                        else {}
                    ),
                    resolution_type=row["resolution_type"],
                    resolution_value=(
                        Decimal(str(row["resolution_value"]))
                        if row["resolution_value"]
                        else None
                    ),
                )
                alerts.append(alert)
            except Exception as e:
                logger.warning(
                    "alert_parse_failed",
                    alert_id=row.get("alert_id"),
                    error=str(e),
                )

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "alerts_queried",
            count=len(alerts),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return alerts

    # =========================================================================
    # GAP MARKERS
    # =========================================================================

    async def insert_gap_marker(self, gap: GapMarker) -> None:
        """
        Insert a data gap marker to the database.

        Args:
            gap: The GapMarker to insert.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> gap = GapMarker(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     gap_start=datetime(2025, 1, 26, 12, 0, 0),
            ...     gap_end=datetime(2025, 1, 26, 12, 0, 45),
            ...     duration_seconds=Decimal("45.0"),
            ...     reason="websocket_disconnect",
            ...     sequence_id_before=12345678,
            ...     sequence_id_after=12345700,
            ... )
            >>> await client.insert_gap_marker(gap)
        """
        start_time = time.monotonic()

        async def _insert() -> None:
            async with self._acquire_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO data_gaps (
                        exchange, instrument, gap_start, gap_end,
                        duration_seconds, reason,
                        sequence_id_before, sequence_id_after
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    gap.exchange,
                    gap.instrument,
                    gap.gap_start,
                    gap.gap_end,
                    _decimal_to_float(gap.duration_seconds),
                    gap.reason,
                    gap.sequence_id_before,
                    gap.sequence_id_after,
                )

        await self._execute_with_retry("insert_gap_marker", _insert)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "gap_marker_inserted",
            exchange=gap.exchange,
            instrument=gap.instrument,
            duration_seconds=float(gap.duration_seconds),
            reason=gap.reason,
            elapsed_ms=round(elapsed_ms, 2),
        )

    async def query_gap_markers(
        self,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        instrument: Optional[str] = None,
    ) -> List[GapMarker]:
        """
        Query data gap markers.

        Args:
            exchange: Exchange identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            instrument: Optional instrument filter.

        Returns:
            List[GapMarker]: List of gap markers in the time range.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> gaps = await client.query_gap_markers(
            ...     exchange="binance",
            ...     start_time=start,
            ...     end_time=end,
            ... )
        """
        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                if instrument is not None:
                    return await conn.fetch(
                        """
                        SELECT
                            exchange, instrument, gap_start, gap_end,
                            duration_seconds, reason,
                            sequence_id_before, sequence_id_after
                        FROM data_gaps
                        WHERE exchange = $1
                          AND instrument = $2
                          AND gap_start >= $3
                          AND gap_start <= $4
                        ORDER BY gap_start DESC
                        """,
                        exchange,
                        instrument,
                        start_time,
                        end_time,
                    )
                else:
                    return await conn.fetch(
                        """
                        SELECT
                            exchange, instrument, gap_start, gap_end,
                            duration_seconds, reason,
                            sequence_id_before, sequence_id_after
                        FROM data_gaps
                        WHERE exchange = $1
                          AND gap_start >= $2
                          AND gap_start <= $3
                        ORDER BY gap_start DESC
                        """,
                        exchange,
                        start_time,
                        end_time,
                    )

        rows = await self._execute_with_retry("query_gap_markers", _query)

        gaps = []
        for row in rows:
            try:
                gap = GapMarker(
                    exchange=row["exchange"],
                    instrument=row["instrument"],
                    gap_start=row["gap_start"],
                    gap_end=row["gap_end"],
                    duration_seconds=Decimal(str(row["duration_seconds"])),
                    reason=row["reason"],
                    sequence_id_before=row["sequence_id_before"],
                    sequence_id_after=row["sequence_id_after"],
                )
                gaps.append(gap)
            except Exception as e:
                logger.warning(
                    "gap_marker_parse_failed",
                    error=str(e),
                )

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "gap_markers_queried",
            exchange=exchange,
            count=len(gaps),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return gaps

    # =========================================================================
    # AGGREGATIONS FOR DASHBOARD
    # =========================================================================

    async def query_spread_metrics_aggregated(
        self,
        exchange: str,
        instrument: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m",
    ) -> List[Dict[str, Any]]:
        """
        Query aggregated spread metrics using TimescaleDB time_bucket.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            interval: Aggregation interval ("1m", "5m", "1h").

        Returns:
            List[Dict[str, Any]]: List of aggregated metrics with avg, min, max values.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_spread_metrics_aggregated(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     start_time=start,
            ...     end_time=end,
            ...     interval="5m",
            ... )
        """
        # Map interval string to PostgreSQL interval
        interval_map = {
            "1m": "1 minute",
            "5m": "5 minutes",
            "15m": "15 minutes",
            "1h": "1 hour",
            "4h": "4 hours",
            "1d": "1 day",
        }
        pg_interval = interval_map.get(interval, "1 minute")

        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{pg_interval}', timestamp) AS bucket,
                        AVG(value) AS avg_spread_bps,
                        MIN(value) AS min_spread_bps,
                        MAX(value) AS max_spread_bps,
                        AVG(zscore) AS avg_zscore,
                        COUNT(*) AS sample_count
                    FROM metrics
                    WHERE metric_name = 'spread_bps'
                      AND exchange = $1
                      AND instrument = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    GROUP BY bucket
                    ORDER BY bucket DESC
                    """,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                )

        rows = await self._execute_with_retry(
            "query_spread_metrics_aggregated",
            _query,
        )

        results = [
            {
                "timestamp": row["bucket"],
                "avg_spread_bps": (
                    Decimal(str(row["avg_spread_bps"]))
                    if row["avg_spread_bps"]
                    else None
                ),
                "min_spread_bps": (
                    Decimal(str(row["min_spread_bps"]))
                    if row["min_spread_bps"]
                    else None
                ),
                "max_spread_bps": (
                    Decimal(str(row["max_spread_bps"]))
                    if row["max_spread_bps"]
                    else None
                ),
                "avg_zscore": (
                    Decimal(str(row["avg_zscore"]))
                    if row["avg_zscore"]
                    else None
                ),
                "sample_count": row["sample_count"],
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "spread_metrics_aggregated_queried",
            exchange=exchange,
            instrument=instrument,
            interval=interval,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    async def query_basis_metrics_aggregated(
        self,
        perp_instrument: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m",
    ) -> List[Dict[str, Any]]:
        """
        Query aggregated basis metrics using TimescaleDB time_bucket.

        Args:
            perp_instrument: Perpetual instrument identifier.
            exchange: Exchange identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            interval: Aggregation interval ("1m", "5m", "1h").

        Returns:
            List[Dict[str, Any]]: List of aggregated metrics.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_basis_metrics_aggregated(
            ...     perp_instrument="BTC-USDT-PERP",
            ...     exchange="binance",
            ...     start_time=start,
            ...     end_time=end,
            ...     interval="5m",
            ... )
        """
        interval_map = {
            "1m": "1 minute",
            "5m": "5 minutes",
            "15m": "15 minutes",
            "1h": "1 hour",
            "4h": "4 hours",
            "1d": "1 day",
        }
        pg_interval = interval_map.get(interval, "1 minute")

        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{pg_interval}', timestamp) AS bucket,
                        AVG(basis_bps) AS avg_basis_bps,
                        MIN(basis_bps) AS min_basis_bps,
                        MAX(basis_bps) AS max_basis_bps,
                        AVG(zscore) AS avg_zscore,
                        COUNT(*) AS sample_count
                    FROM basis_metrics
                    WHERE perp_instrument = $1
                      AND exchange = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    GROUP BY bucket
                    ORDER BY bucket DESC
                    """,
                    perp_instrument,
                    exchange,
                    start_time,
                    end_time,
                )

        rows = await self._execute_with_retry(
            "query_basis_metrics_aggregated",
            _query,
        )

        results = [
            {
                "timestamp": row["bucket"],
                "avg_basis_bps": (
                    Decimal(str(row["avg_basis_bps"]))
                    if row["avg_basis_bps"]
                    else None
                ),
                "min_basis_bps": (
                    Decimal(str(row["min_basis_bps"]))
                    if row["min_basis_bps"]
                    else None
                ),
                "max_basis_bps": (
                    Decimal(str(row["max_basis_bps"]))
                    if row["max_basis_bps"]
                    else None
                ),
                "avg_zscore": (
                    Decimal(str(row["avg_zscore"]))
                    if row["avg_zscore"]
                    else None
                ),
                "sample_count": row["sample_count"],
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "basis_metrics_aggregated_queried",
            perp_instrument=perp_instrument,
            exchange=exchange,
            interval=interval,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    async def query_depth_metrics_aggregated(
        self,
        exchange: str,
        instrument: str,
        start_time: datetime,
        end_time: datetime,
        bps_level: int = 10,
        interval: str = "1m",
    ) -> List[Dict[str, Any]]:
        """
        Query aggregated depth metrics using TimescaleDB time_bucket.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            start_time: Start of time range (UTC).
            end_time: End of time range (UTC).
            bps_level: Basis points level (5, 10, or 25).
            interval: Aggregation interval ("1m", "5m", "1h").

        Returns:
            List[Dict[str, Any]]: List of aggregated metrics.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.query_depth_metrics_aggregated(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     start_time=start,
            ...     end_time=end,
            ...     bps_level=10,
            ...     interval="5m",
            ... )
        """
        interval_map = {
            "1m": "1 minute",
            "5m": "5 minutes",
            "15m": "15 minutes",
            "1h": "1 hour",
            "4h": "4 hours",
            "1d": "1 day",
        }
        pg_interval = interval_map.get(interval, "1 minute")
        metric_name = f"depth_{bps_level}bps_total"

        start_query_time = time.monotonic()

        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{pg_interval}', timestamp) AS bucket,
                        AVG(value) AS avg_depth,
                        MIN(value) AS min_depth,
                        MAX(value) AS max_depth,
                        COUNT(*) AS sample_count
                    FROM metrics
                    WHERE metric_name = $1
                      AND exchange = $2
                      AND instrument = $3
                      AND timestamp >= $4
                      AND timestamp <= $5
                    GROUP BY bucket
                    ORDER BY bucket DESC
                    """,
                    metric_name,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                )

        rows = await self._execute_with_retry(
            "query_depth_metrics_aggregated",
            _query,
        )

        results = [
            {
                "timestamp": row["bucket"],
                "avg_depth": (
                    Decimal(str(row["avg_depth"]))
                    if row["avg_depth"]
                    else None
                ),
                "min_depth": (
                    Decimal(str(row["min_depth"]))
                    if row["min_depth"]
                    else None
                ),
                "max_depth": (
                    Decimal(str(row["max_depth"]))
                    if row["max_depth"]
                    else None
                ),
                "sample_count": row["sample_count"],
            }
            for row in rows
        ]

        elapsed_ms = (time.monotonic() - start_query_time) * 1000
        logger.debug(
            "depth_metrics_aggregated_queried",
            exchange=exchange,
            instrument=instrument,
            bps_level=bps_level,
            interval=interval,
            count=len(results),
            elapsed_ms=round(elapsed_ms, 2),
        )

        return results

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def get_active_alerts_count(self) -> Dict[str, int]:
        """
        Get count of active alerts by priority.

        Returns:
            Dict[str, int]: Alert counts keyed by priority (P1, P2, P3).

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> counts = await client.get_active_alerts_count()
            >>> print(f"P1 alerts: {counts.get('P1', 0)}")
        """
        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT priority, COUNT(*) as count
                    FROM alerts
                    WHERE resolved_at IS NULL
                    GROUP BY priority
                    """
                )

        rows = await self._execute_with_retry("get_active_alerts_count", _query)

        return {row["priority"]: row["count"] for row in rows}

    async def get_latest_metrics(
        self,
        instrument: str,
        lookback_minutes: int = 5,
    ) -> Dict[str, Any]:
        """
        Get latest metrics summary for an instrument.

        Args:
            instrument: Instrument identifier.
            lookback_minutes: Minutes to look back for statistics.

        Returns:
            Dict[str, Any]: Dictionary with latest values and statistics per metric.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> metrics = await client.get_latest_metrics("BTC-USDT-PERP")
            >>> print(f"Latest spread: {metrics.get('spread_bps', {}).get('latest')}")
        """
        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(
                    """
                    SELECT * FROM get_latest_metrics($1, $2)
                    """,
                    instrument,
                    lookback_minutes,
                )

        try:
            rows = await self._execute_with_retry("get_latest_metrics", _query)

            return {
                row["metric_name"]: {
                    "latest": (
                        Decimal(str(row["latest_value"]))
                        if row["latest_value"]
                        else None
                    ),
                    "zscore": (
                        Decimal(str(row["latest_zscore"]))
                        if row["latest_zscore"]
                        else None
                    ),
                    "avg": (
                        Decimal(str(row["avg_value"]))
                        if row["avg_value"]
                        else None
                    ),
                    "min": (
                        Decimal(str(row["min_value"]))
                        if row["min_value"]
                        else None
                    ),
                    "max": (
                        Decimal(str(row["max_value"]))
                        if row["max_value"]
                        else None
                    ),
                }
                for row in rows
            }
        except PostgresOperationError:
            # Function might not exist in some setups
            logger.warning(
                "get_latest_metrics_function_not_available",
                instrument=instrument,
            )
            return {}

    async def execute_raw(self, query: str, *args: Any) -> List[Record]:
        """
        Execute a raw SQL query.

        Use with caution - prefer specific methods for type safety.

        Args:
            query: SQL query string.
            *args: Query parameters.

        Returns:
            List[Record]: Query results.

        Raises:
            PostgresConnectionException: If not connected.
            PostgresOperationError: If the operation fails.

        Example:
            >>> rows = await client.execute_raw(
            ...     "SELECT COUNT(*) FROM alerts WHERE priority = $1",
            ...     "P1",
            ... )
        """
        async def _query() -> List[Record]:
            async with self._acquire_connection() as conn:
                return await conn.fetch(query, *args)

        return await self._execute_with_retry("execute_raw", _query)
