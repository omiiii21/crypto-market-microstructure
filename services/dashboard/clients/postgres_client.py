"""
Async PostgreSQL client for the dashboard service.

This module provides a lightweight async PostgreSQL client using asyncpg
for querying historical data from TimescaleDB. It does NOT write data - only reads.

Key Tables:
    - metrics: Time-series of computed metrics with z-scores
    - basis_metrics: Perpetual-spot basis metrics
    - alerts: Alert history with lifecycle tracking
    - order_book_snapshots: Historical order book data

Note:
    This module is owned by the VIZ agent.
    All data is READ from PostgreSQL - the dashboard never writes metrics.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog

try:
    import asyncpg
    from asyncpg import Pool, Record
except ImportError:
    asyncpg = None
    Pool = None
    Record = None

logger = structlog.get_logger(__name__)


class DashboardPostgresClient:
    """
    Async PostgreSQL client for dashboard data retrieval.

    Provides methods for querying historical metrics, alerts, and other
    time-series data from TimescaleDB. Uses asyncpg for true async operation.

    Attributes:
        url: PostgreSQL connection URL.
        _pool: Connection pool for efficient connection reuse.
        _connected: Whether the client is connected.

    Example:
        >>> client = DashboardPostgresClient("postgresql://...")
        >>> await client.connect()
        >>> metrics = await client.get_spread_metrics("binance", "BTC-USDT-PERP", "1h")
        >>> await client.disconnect()
    """

    def __init__(self, url: str):
        """
        Initialize the PostgreSQL client.

        Args:
            url: PostgreSQL connection URL.
        """
        if asyncpg is None:
            raise ImportError("asyncpg is required. Install with: pip install asyncpg")

        self.url = url
        self._pool: Optional[Pool] = None
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to PostgreSQL."""
        return self._connected and self._pool is not None

    async def connect(self) -> None:
        """
        Establish connection pool to PostgreSQL.

        Raises:
            Exception: If connection fails.
        """
        if self._connected:
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self.url,
                min_size=1,
                max_size=5,
                command_timeout=30,
            )

            # Verify connection
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            self._connected = True
            logger.info("dashboard_postgres_connected")

        except Exception as e:
            self._connected = False
            logger.error("dashboard_postgres_connection_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Close PostgreSQL connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._connected = False
        logger.info("dashboard_postgres_disconnected")

    async def ping(self) -> bool:
        """Check PostgreSQL connection health."""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # =========================================================================
    # TIME RANGE HELPERS
    # =========================================================================

    def _parse_time_range(self, time_range: str) -> tuple[datetime, datetime]:
        """
        Parse time range string to start and end datetimes.

        Args:
            time_range: Time range string (e.g., "5m", "1h", "24h").

        Returns:
            tuple[datetime, datetime]: Start and end times.
        """
        end_time = datetime.utcnow()
        range_map = {
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "24h": timedelta(hours=24),
        }
        delta = range_map.get(time_range, timedelta(hours=1))
        start_time = end_time - delta
        return start_time, end_time

    def _get_aggregation_interval(self, time_range: str) -> str:
        """
        Get appropriate aggregation interval for a time range.

        Args:
            time_range: Time range string.

        Returns:
            str: PostgreSQL interval string.
        """
        interval_map = {
            "5m": "1 minute",
            "15m": "1 minute",
            "1h": "1 minute",
            "4h": "5 minutes",
            "24h": "15 minutes",
        }
        return interval_map.get(time_range, "1 minute")

    # =========================================================================
    # SPREAD METRICS
    # =========================================================================

    async def get_spread_metrics(
        self,
        exchange: str,
        instrument: str,
        time_range: str = "1h",
    ) -> List[Dict[str, Any]]:
        """
        Get historical spread metrics.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            time_range: Time range string (e.g., "1h", "4h").

        Returns:
            List[Dict[str, Any]]: List of spread data points.
        """
        if not self._pool:
            return []

        try:
            start_time, end_time = self._parse_time_range(time_range)
            interval = self._get_aggregation_interval(time_range)

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{interval}', timestamp) AS bucket,
                        AVG(value) AS avg_spread_bps,
                        MIN(value) AS min_spread_bps,
                        MAX(value) AS max_spread_bps,
                        AVG(zscore) AS avg_zscore
                    FROM metrics
                    WHERE metric_name = 'spread_bps'
                      AND exchange = $1
                      AND instrument = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    GROUP BY bucket
                    ORDER BY bucket ASC
                    """,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                )

            return [
                {
                    "timestamp": row["bucket"].isoformat() if row["bucket"] else None,
                    "spread_bps": str(row["avg_spread_bps"]) if row["avg_spread_bps"] else None,
                    "min_spread_bps": str(row["min_spread_bps"]) if row["min_spread_bps"] else None,
                    "max_spread_bps": str(row["max_spread_bps"]) if row["max_spread_bps"] else None,
                    "zscore": str(row["avg_zscore"]) if row["avg_zscore"] else None,
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(
                "get_spread_metrics_error",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )
            return []

    # =========================================================================
    # BASIS METRICS
    # =========================================================================

    async def get_basis_metrics(
        self,
        exchange: str,
        perp_instrument: str,
        time_range: str = "1h",
    ) -> List[Dict[str, Any]]:
        """
        Get historical basis metrics.

        Args:
            exchange: Exchange identifier.
            perp_instrument: Perpetual instrument identifier.
            time_range: Time range string.

        Returns:
            List[Dict[str, Any]]: List of basis data points.
        """
        if not self._pool:
            return []

        try:
            start_time, end_time = self._parse_time_range(time_range)
            interval = self._get_aggregation_interval(time_range)

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{interval}', timestamp) AS bucket,
                        AVG(basis_bps) AS avg_basis_bps,
                        MIN(basis_bps) AS min_basis_bps,
                        MAX(basis_bps) AS max_basis_bps,
                        AVG(zscore) AS avg_zscore
                    FROM basis_metrics
                    WHERE perp_instrument = $1
                      AND exchange = $2
                      AND timestamp >= $3
                      AND timestamp <= $4
                    GROUP BY bucket
                    ORDER BY bucket ASC
                    """,
                    perp_instrument,
                    exchange,
                    start_time,
                    end_time,
                )

            return [
                {
                    "timestamp": row["bucket"].isoformat() if row["bucket"] else None,
                    "basis_bps": str(row["avg_basis_bps"]) if row["avg_basis_bps"] else None,
                    "min_basis_bps": str(row["min_basis_bps"]) if row["min_basis_bps"] else None,
                    "max_basis_bps": str(row["max_basis_bps"]) if row["max_basis_bps"] else None,
                    "zscore": str(row["avg_zscore"]) if row["avg_zscore"] else None,
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(
                "get_basis_metrics_error",
                exchange=exchange,
                perp_instrument=perp_instrument,
                error=str(e),
            )
            return []

    # =========================================================================
    # DEPTH METRICS
    # =========================================================================

    async def get_depth_metrics(
        self,
        exchange: str,
        instrument: str,
        time_range: str = "1h",
        bps_level: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get historical depth metrics.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            time_range: Time range string.
            bps_level: Basis points level (5, 10, or 25).

        Returns:
            List[Dict[str, Any]]: List of depth data points.
        """
        if not self._pool:
            return []

        try:
            start_time, end_time = self._parse_time_range(time_range)
            interval = self._get_aggregation_interval(time_range)
            metric_name = f"depth_{bps_level}bps_total"

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT
                        time_bucket('{interval}', timestamp) AS bucket,
                        AVG(value) AS avg_depth,
                        MIN(value) AS min_depth,
                        MAX(value) AS max_depth
                    FROM metrics
                    WHERE metric_name = $1
                      AND exchange = $2
                      AND instrument = $3
                      AND timestamp >= $4
                      AND timestamp <= $5
                    GROUP BY bucket
                    ORDER BY bucket ASC
                    """,
                    metric_name,
                    exchange,
                    instrument,
                    start_time,
                    end_time,
                )

            return [
                {
                    "timestamp": row["bucket"].isoformat() if row["bucket"] else None,
                    "depth": str(row["avg_depth"]) if row["avg_depth"] else None,
                    "min_depth": str(row["min_depth"]) if row["min_depth"] else None,
                    "max_depth": str(row["max_depth"]) if row["max_depth"] else None,
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(
                "get_depth_metrics_error",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )
            return []

    # =========================================================================
    # ALERTS
    # =========================================================================

    async def get_alert_history(
        self,
        time_range: str = "24h",
        exchange: Optional[str] = None,
        instrument: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get historical alerts.

        Args:
            time_range: Time range string.
            exchange: Optional exchange filter.
            instrument: Optional instrument filter.
            priority: Optional priority filter (P1, P2, P3).
            status: Optional status filter ("active" or "resolved").
            limit: Maximum number of results.

        Returns:
            List[Dict[str, Any]]: List of alerts.
        """
        if not self._pool:
            return []

        try:
            start_time, end_time = self._parse_time_range(time_range)

            # Build query with optional filters
            conditions = ["triggered_at >= $1", "triggered_at <= $2"]
            params: List[Any] = [start_time, end_time]
            param_idx = 2

            if exchange:
                param_idx += 1
                conditions.append(f"exchange = ${param_idx}")
                params.append(exchange)

            if instrument:
                param_idx += 1
                conditions.append(f"instrument = ${param_idx}")
                params.append(instrument)

            if priority:
                param_idx += 1
                conditions.append(f"priority = ${param_idx}")
                params.append(priority)

            if status == "active":
                conditions.append("resolved_at IS NULL")
            elif status == "resolved":
                conditions.append("resolved_at IS NOT NULL")

            param_idx += 1
            params.append(limit)

            query = f"""
                SELECT
                    alert_id, alert_type, priority, severity,
                    exchange, instrument,
                    trigger_metric, trigger_value, trigger_threshold,
                    zscore_value, zscore_threshold,
                    triggered_at, resolved_at, duration_seconds
                FROM alerts
                WHERE {' AND '.join(conditions)}
                ORDER BY triggered_at DESC
                LIMIT ${param_idx}
            """

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            return [
                {
                    "alert_id": row["alert_id"],
                    "alert_type": row["alert_type"],
                    "priority": row["priority"],
                    "severity": row["severity"],
                    "exchange": row["exchange"],
                    "instrument": row["instrument"],
                    "trigger_metric": row["trigger_metric"],
                    "trigger_value": str(row["trigger_value"]) if row["trigger_value"] else None,
                    "trigger_threshold": str(row["trigger_threshold"]) if row["trigger_threshold"] else None,
                    "zscore_value": str(row["zscore_value"]) if row["zscore_value"] else None,
                    "zscore_threshold": str(row["zscore_threshold"]) if row["zscore_threshold"] else None,
                    "triggered_at": row["triggered_at"].isoformat() if row["triggered_at"] else None,
                    "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                    "duration_seconds": row["duration_seconds"],
                }
                for row in rows
            ]

        except Exception as e:
            logger.error("get_alert_history_error", error=str(e))
            return []

    # =========================================================================
    # GAP MARKERS
    # =========================================================================

    async def get_gap_count(
        self,
        exchange: str,
        time_range: str = "1h",
    ) -> int:
        """
        Get count of data gaps in the time range.

        Args:
            exchange: Exchange identifier.
            time_range: Time range string.

        Returns:
            int: Number of gaps.
        """
        if not self._pool:
            return 0

        try:
            start_time, end_time = self._parse_time_range(time_range)

            async with self._pool.acquire() as conn:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM data_gaps
                    WHERE exchange = $1
                      AND gap_start >= $2
                      AND gap_start <= $3
                    """,
                    exchange,
                    start_time,
                    end_time,
                )

            return count or 0

        except Exception as e:
            logger.error(
                "get_gap_count_error",
                exchange=exchange,
                error=str(e),
            )
            return 0

    # =========================================================================
    # SUMMARY STATS
    # =========================================================================

    async def get_metrics_count(self) -> int:
        """
        Get total count of metrics in the database.

        Returns:
            int: Total number of metrics.
        """
        if not self._pool:
            return 0

        try:
            async with self._pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM metrics")
            return count or 0
        except Exception as e:
            logger.error("get_metrics_count_error", error=str(e))
            return 0

    async def get_latest_timestamp(
        self,
        exchange: str,
        instrument: str,
    ) -> Optional[str]:
        """
        Get the latest timestamp for an exchange/instrument.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.

        Returns:
            Optional[str]: ISO timestamp string or None.
        """
        if not self._pool:
            return None

        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval(
                    """
                    SELECT MAX(timestamp)
                    FROM metrics
                    WHERE exchange = $1
                      AND instrument = $2
                    """,
                    exchange,
                    instrument,
                )
            return result.isoformat() if result else None
        except Exception as e:
            logger.error(
                "get_latest_timestamp_error",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )
            return None
