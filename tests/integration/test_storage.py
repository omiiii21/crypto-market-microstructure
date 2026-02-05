"""
Storage integration tests.

This module tests Redis and PostgreSQL storage operations:
- Redis operations: set/get orderbook, metrics, alerts
- PostgreSQL operations: insert/query snapshots, metrics
- Pub/sub message flow
- Storage error handling

Note:
    Tests marked with @pytest.mark.requires_redis or @pytest.mark.requires_postgres
    require running infrastructure. Use docker-compose up -d redis timescaledb.

    Tests without these markers use mocked clients and can run anywhere.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.config.models import (
    PostgresConnectionConfig,
    PostgresStorageConfig,
    RedisConnectionConfig,
    RedisStorageConfig,
)
from src.models.alerts import Alert, AlertCondition, AlertPriority, AlertSeverity
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.metrics import SpreadMetrics
from src.models.orderbook import OrderBookSnapshot, PriceLevel


# =============================================================================
# REDIS STORAGE TESTS - MOCKED
# =============================================================================


class TestRedisStorageMocked:
    """Test Redis storage operations with mocked client."""

    @pytest.mark.asyncio
    async def test_set_and_get_orderbook(
        self,
        mock_redis_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test storing and retrieving order book snapshot.
        """
        # Configure mock to return the snapshot on get
        mock_redis_client.get_orderbook.return_value = sample_perp_snapshot

        # Store snapshot
        await mock_redis_client.set_orderbook(sample_perp_snapshot)
        mock_redis_client.set_orderbook.assert_called_once_with(sample_perp_snapshot)

        # Retrieve snapshot
        retrieved = await mock_redis_client.get_orderbook(
            "binance", "BTC-USDT-PERP"
        )

        assert retrieved is not None
        assert retrieved.exchange == sample_perp_snapshot.exchange
        assert retrieved.instrument == sample_perp_snapshot.instrument
        assert retrieved.sequence_id == sample_perp_snapshot.sequence_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_orderbook_returns_none(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test that getting nonexistent orderbook returns None.
        """
        mock_redis_client.get_orderbook.return_value = None

        result = await mock_redis_client.get_orderbook(
            "nonexistent", "BTC-USDT-PERP"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_alert(
        self,
        mock_redis_client: MagicMock,
        sample_triggered_alert: Alert,
    ) -> None:
        """
        Test storing and retrieving alert.
        """
        mock_redis_client.get_alert.return_value = sample_triggered_alert

        # Store alert
        await mock_redis_client.set_alert(sample_triggered_alert)
        mock_redis_client.set_alert.assert_called_once()

        # Retrieve alert
        retrieved = await mock_redis_client.get_alert(sample_triggered_alert.alert_id)

        assert retrieved is not None
        assert retrieved.alert_id == sample_triggered_alert.alert_id
        assert retrieved.alert_type == sample_triggered_alert.alert_type
        assert retrieved.priority == sample_triggered_alert.priority

    @pytest.mark.asyncio
    async def test_get_active_alerts(
        self,
        mock_redis_client: MagicMock,
        sample_triggered_alert: Alert,
    ) -> None:
        """
        Test retrieving all active alerts.
        """
        mock_redis_client.get_active_alerts.return_value = [sample_triggered_alert]

        alerts = await mock_redis_client.get_active_alerts()

        assert len(alerts) == 1
        assert alerts[0].alert_id == sample_triggered_alert.alert_id

    @pytest.mark.asyncio
    async def test_set_and_get_health(
        self,
        mock_redis_client: MagicMock,
        healthy_status: HealthStatus,
    ) -> None:
        """
        Test storing and retrieving health status.
        """
        mock_redis_client.get_health.return_value = healthy_status

        # Store health
        await mock_redis_client.set_health(healthy_status)
        mock_redis_client.set_health.assert_called_once()

        # Retrieve health
        retrieved = await mock_redis_client.get_health("binance")

        assert retrieved is not None
        assert retrieved.exchange == healthy_status.exchange
        assert retrieved.status == ConnectionStatus.CONNECTED


# =============================================================================
# REDIS Z-SCORE BUFFER TESTS - MOCKED
# =============================================================================


class TestRedisZScoreBufferMocked:
    """Test Redis z-score buffer operations with mocked client."""

    @pytest.mark.asyncio
    async def test_add_zscore_sample(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test adding sample to z-score buffer.
        """
        await mock_redis_client.add_zscore_sample(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
            value=Decimal("2.5"),
            window_size=300,
        )

        mock_redis_client.add_zscore_sample.assert_called_once_with(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
            value=Decimal("2.5"),
            window_size=300,
        )

    @pytest.mark.asyncio
    async def test_get_zscore_buffer(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test retrieving z-score buffer.
        """
        expected_samples = [
            Decimal("2.0"),
            Decimal("2.1"),
            Decimal("2.2"),
            Decimal("2.3"),
        ]
        mock_redis_client.get_zscore_buffer.return_value = expected_samples

        samples = await mock_redis_client.get_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        assert samples == expected_samples
        assert all(isinstance(s, Decimal) for s in samples)

    @pytest.mark.asyncio
    async def test_get_zscore_buffer_length(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test getting z-score buffer length for warmup tracking.
        """
        mock_redis_client.get_zscore_buffer_length.return_value = 25

        length = await mock_redis_client.get_zscore_buffer_length(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        assert length == 25

    @pytest.mark.asyncio
    async def test_clear_zscore_buffer_on_gap(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test clearing z-score buffer when gap is detected.
        """
        await mock_redis_client.clear_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        mock_redis_client.clear_zscore_buffer.assert_called_once()


# =============================================================================
# REDIS PUB/SUB TESTS - MOCKED
# =============================================================================


class TestRedisPubSubMocked:
    """Test Redis pub/sub operations with mocked client."""

    @pytest.mark.asyncio
    async def test_publish_orderbook_update(
        self,
        mock_redis_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test publishing order book update notification.
        """
        mock_redis_client.publish_orderbook_update.return_value = 2

        subscriber_count = await mock_redis_client.publish_orderbook_update(
            sample_perp_snapshot
        )

        assert subscriber_count == 2
        mock_redis_client.publish_orderbook_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_alert(
        self,
        mock_redis_client: MagicMock,
        sample_triggered_alert: Alert,
    ) -> None:
        """
        Test publishing alert notification.
        """
        mock_redis_client.publish_alert.return_value = 1

        subscriber_count = await mock_redis_client.publish_alert(
            sample_triggered_alert
        )

        assert subscriber_count == 1
        mock_redis_client.publish_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_health_update(
        self,
        mock_redis_client: MagicMock,
        healthy_status: HealthStatus,
    ) -> None:
        """
        Test publishing health status update.
        """
        mock_redis_client.publish_health_update.return_value = 1

        subscriber_count = await mock_redis_client.publish_health_update(
            healthy_status
        )

        assert subscriber_count == 1
        mock_redis_client.publish_health_update.assert_called_once()


# =============================================================================
# POSTGRESQL STORAGE TESTS - MOCKED
# =============================================================================


class TestPostgresStorageMocked:
    """Test PostgreSQL storage operations with mocked client."""

    @pytest.mark.asyncio
    async def test_insert_orderbook_snapshots(
        self,
        mock_postgres_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test batch inserting order book snapshots.
        """
        mock_postgres_client.insert_orderbook_snapshots.return_value = 1

        count = await mock_postgres_client.insert_orderbook_snapshots(
            [sample_perp_snapshot]
        )

        assert count == 1
        mock_postgres_client.insert_orderbook_snapshots.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_orderbook_snapshots(
        self,
        mock_postgres_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test querying historical order book snapshots.
        """
        mock_postgres_client.query_orderbook_snapshots.return_value = [
            sample_perp_snapshot
        ]

        start_time = integration_utc_now - timedelta(hours=1)
        end_time = integration_utc_now

        snapshots = await mock_postgres_client.query_orderbook_snapshots(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            start_time=start_time,
            end_time=end_time,
            limit=100,
        )

        assert len(snapshots) == 1
        assert snapshots[0].exchange == "binance"

    @pytest.mark.asyncio
    async def test_insert_spread_metrics(
        self,
        mock_postgres_client: MagicMock,
        normal_spread_metrics: SpreadMetrics,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test inserting spread metrics.
        """
        mock_postgres_client.insert_spread_metrics.return_value = 1

        metrics_batch = [
            ("binance", "BTC-USDT-PERP", integration_utc_now, normal_spread_metrics)
        ]

        count = await mock_postgres_client.insert_spread_metrics(metrics_batch)

        assert count == 1

    @pytest.mark.asyncio
    async def test_query_spread_metrics(
        self,
        mock_postgres_client: MagicMock,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test querying historical spread metrics.
        """
        expected_results = [
            {
                "timestamp": integration_utc_now,
                "spread_bps": Decimal("2.0"),
                "zscore": Decimal("0.5"),
            }
        ]
        mock_postgres_client.query_spread_metrics.return_value = expected_results

        start_time = integration_utc_now - timedelta(hours=1)
        end_time = integration_utc_now

        results = await mock_postgres_client.query_spread_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            start_time=start_time,
            end_time=end_time,
        )

        assert len(results) == 1
        assert results[0]["spread_bps"] == Decimal("2.0")


# =============================================================================
# POSTGRESQL ALERT TESTS - MOCKED
# =============================================================================


class TestPostgresAlertStorageMocked:
    """Test PostgreSQL alert storage operations with mocked client."""

    @pytest.mark.asyncio
    async def test_insert_alert(
        self,
        mock_postgres_client: MagicMock,
        sample_triggered_alert: Alert,
    ) -> None:
        """
        Test inserting alert to database.
        """
        await mock_postgres_client.insert_alert(sample_triggered_alert)

        mock_postgres_client.insert_alert.assert_called_once_with(
            sample_triggered_alert
        )

    @pytest.mark.asyncio
    async def test_update_alert_status(
        self,
        mock_postgres_client: MagicMock,
        sample_triggered_alert: Alert,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test updating alert status (resolve, escalate).
        """
        await mock_postgres_client.update_alert_status(
            alert_id=sample_triggered_alert.alert_id,
            status="resolved",
            resolved_at=integration_utc_now,
            resolution_type="auto",
            resolution_value=Decimal("2.5"),
            duration_seconds=120,
        )

        mock_postgres_client.update_alert_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_alerts_by_priority(
        self,
        mock_postgres_client: MagicMock,
        sample_triggered_alert: Alert,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test querying alerts filtered by priority.
        """
        mock_postgres_client.query_alerts.return_value = [sample_triggered_alert]

        start_time = integration_utc_now - timedelta(hours=1)
        end_time = integration_utc_now

        alerts = await mock_postgres_client.query_alerts(
            start_time=start_time,
            end_time=end_time,
            priority=AlertPriority.P2,
        )

        assert len(alerts) == 1
        assert alerts[0].priority == AlertPriority.P2

    @pytest.mark.asyncio
    async def test_query_active_alerts_count(
        self,
        mock_postgres_client: MagicMock,
    ) -> None:
        """
        Test getting active alert counts by priority.
        """
        mock_postgres_client.get_active_alerts_count.return_value = {
            "P1": 2,
            "P2": 5,
            "P3": 10,
        }

        counts = await mock_postgres_client.get_active_alerts_count()

        assert counts["P1"] == 2
        assert counts["P2"] == 5
        assert counts["P3"] == 10


# =============================================================================
# POSTGRESQL GAP MARKER TESTS - MOCKED
# =============================================================================


class TestPostgresGapMarkersMocked:
    """Test PostgreSQL gap marker operations with mocked client."""

    @pytest.mark.asyncio
    async def test_insert_gap_marker(
        self,
        mock_postgres_client: MagicMock,
        sample_gap_marker_integration: GapMarker,
    ) -> None:
        """
        Test inserting gap marker to database.
        """
        await mock_postgres_client.insert_gap_marker(sample_gap_marker_integration)

        mock_postgres_client.insert_gap_marker.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_gap_markers(
        self,
        mock_postgres_client: MagicMock,
        sample_gap_marker_integration: GapMarker,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test querying gap markers.
        """
        mock_postgres_client.query_gap_markers.return_value = [
            sample_gap_marker_integration
        ]

        start_time = integration_utc_now - timedelta(hours=24)
        end_time = integration_utc_now

        gaps = await mock_postgres_client.query_gap_markers(
            exchange="binance",
            start_time=start_time,
            end_time=end_time,
        )

        assert len(gaps) == 1
        assert gaps[0].exchange == "binance"
        assert gaps[0].reason == "websocket_disconnect"


# =============================================================================
# STORAGE CONNECTION TESTS
# =============================================================================


class TestStorageConnections:
    """Test storage connection handling."""

    @pytest.mark.asyncio
    async def test_redis_connection_and_disconnect(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test Redis connect and disconnect lifecycle.
        """
        await mock_redis_client.connect()
        mock_redis_client.connect.assert_called_once()

        assert mock_redis_client.is_connected is True

        await mock_redis_client.disconnect()
        mock_redis_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_postgres_connection_and_disconnect(
        self,
        mock_postgres_client: MagicMock,
    ) -> None:
        """
        Test PostgreSQL connect and disconnect lifecycle.
        """
        await mock_postgres_client.connect()
        mock_postgres_client.connect.assert_called_once()

        assert mock_postgres_client.is_connected is True

        await mock_postgres_client.disconnect()
        mock_postgres_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_ping(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test Redis health check ping.
        """
        mock_redis_client.ping.return_value = True

        result = await mock_redis_client.ping()

        assert result is True

    @pytest.mark.asyncio
    async def test_postgres_ping(
        self,
        mock_postgres_client: MagicMock,
    ) -> None:
        """
        Test PostgreSQL health check ping.
        """
        mock_postgres_client.ping.return_value = True

        result = await mock_postgres_client.ping()

        assert result is True


# =============================================================================
# STORAGE ERROR HANDLING TESTS
# =============================================================================


class TestStorageErrorHandling:
    """Test storage error handling."""

    @pytest.mark.asyncio
    async def test_redis_operation_when_disconnected(
        self,
        mock_redis_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test that Redis operations fail gracefully when disconnected.
        """
        from src.storage.redis_client import RedisConnectionException

        mock_redis_client.set_orderbook.side_effect = RedisConnectionException(
            "Not connected"
        )

        with pytest.raises(RedisConnectionException):
            await mock_redis_client.set_orderbook(sample_perp_snapshot)

    @pytest.mark.asyncio
    async def test_postgres_operation_when_disconnected(
        self,
        mock_postgres_client: MagicMock,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test that PostgreSQL operations fail gracefully when disconnected.
        """
        from src.storage.postgres_client import PostgresConnectionException

        mock_postgres_client.insert_orderbook_snapshots.side_effect = (
            PostgresConnectionException("Not connected")
        )

        with pytest.raises(PostgresConnectionException):
            await mock_postgres_client.insert_orderbook_snapshots(
                [sample_perp_snapshot]
            )


# =============================================================================
# LIVE REDIS TESTS (REQUIRES INFRASTRUCTURE)
# =============================================================================


@pytest.mark.requires_redis
class TestRedisStorageLive:
    """
    Live Redis storage tests.

    These tests require a running Redis instance.
    Run with: docker-compose up -d redis
    Skip with: pytest -m "not requires_redis"
    """

    @pytest_asyncio.fixture
    async def live_redis_client(
        self,
        redis_connection_config: RedisConnectionConfig,
        redis_storage_config: RedisStorageConfig,
    ):
        """Create live Redis client for testing."""
        from src.storage.redis_client import RedisClient

        client = RedisClient(redis_connection_config, redis_storage_config)

        try:
            await client.connect()
            await client.flush_db()  # Clean test database
            yield client
        finally:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_live_orderbook_roundtrip(
        self,
        live_redis_client,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test real order book storage and retrieval.
        """
        # Store
        await live_redis_client.set_orderbook(sample_perp_snapshot)

        # Retrieve
        retrieved = await live_redis_client.get_orderbook(
            "binance", "BTC-USDT-PERP"
        )

        assert retrieved is not None
        assert retrieved.exchange == sample_perp_snapshot.exchange
        assert retrieved.sequence_id == sample_perp_snapshot.sequence_id
        assert len(retrieved.bids) == len(sample_perp_snapshot.bids)

    @pytest.mark.asyncio
    async def test_live_zscore_buffer(
        self,
        live_redis_client,
    ) -> None:
        """
        Test real z-score buffer operations.
        """
        # Add samples
        for i in range(10):
            await live_redis_client.add_zscore_sample(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                metric="spread_bps",
                value=Decimal(str(2.0 + i * 0.1)),
                window_size=300,
            )

        # Check length
        length = await live_redis_client.get_zscore_buffer_length(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )
        assert length == 10

        # Get buffer
        samples = await live_redis_client.get_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )
        assert len(samples) == 10
        assert all(isinstance(s, Decimal) for s in samples)

        # Clear buffer
        await live_redis_client.clear_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        # Verify cleared
        length_after = await live_redis_client.get_zscore_buffer_length(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )
        assert length_after == 0


# =============================================================================
# LIVE POSTGRESQL TESTS (REQUIRES INFRASTRUCTURE)
# =============================================================================


@pytest.mark.requires_postgres
class TestPostgresStorageLive:
    """
    Live PostgreSQL storage tests.

    These tests require a running PostgreSQL/TimescaleDB instance.
    Run with: docker-compose up -d timescaledb
    Skip with: pytest -m "not requires_postgres"
    """

    @pytest_asyncio.fixture
    async def live_postgres_client(
        self,
        postgres_connection_config: PostgresConnectionConfig,
        postgres_storage_config: PostgresStorageConfig,
    ):
        """Create live PostgreSQL client for testing."""
        from src.storage.postgres_client import PostgresClient

        client = PostgresClient(postgres_connection_config, postgres_storage_config)

        try:
            await client.connect()
            yield client
        finally:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_live_orderbook_insert_and_query(
        self,
        live_postgres_client,
        sample_perp_snapshot: OrderBookSnapshot,
        integration_utc_now: datetime,
    ) -> None:
        """
        Test real order book insert and query.
        """
        # Insert
        count = await live_postgres_client.insert_orderbook_snapshots(
            [sample_perp_snapshot]
        )
        assert count == 1

        # Query
        start_time = integration_utc_now - timedelta(minutes=5)
        end_time = integration_utc_now + timedelta(minutes=5)

        snapshots = await live_postgres_client.query_orderbook_snapshots(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            start_time=start_time,
            end_time=end_time,
            limit=10,
        )

        assert len(snapshots) >= 1
