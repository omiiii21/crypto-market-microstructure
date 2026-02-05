"""
Integration test fixtures and configuration.

This module provides fixtures for integration testing including:
- Redis client fixtures (using test Redis or mock)
- PostgreSQL client fixtures (using test database or mock)
- Sample data fixtures for OrderBookSnapshot, SpreadMetrics, Alert
- pytest markers for @pytest.mark.integration

Note:
    Integration tests require running Redis and PostgreSQL instances.
    Use docker-compose up -d redis timescaledb to start infrastructure.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator, Generator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.config.models import (
    PostgresConnectionConfig,
    PostgresStorageConfig,
    RedisConnectionConfig,
    RedisStorageConfig,
)
from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertResult,
    AlertSeverity,
    AlertThreshold,
)
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.metrics import (
    AggregatedMetrics,
    BasisMetrics,
    DepthMetrics,
    ImbalanceMetrics,
    SpreadMetrics,
)
from src.models.orderbook import OrderBookSnapshot, PriceLevel


# =============================================================================
# PYTEST MARKERS
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
    )
    config.addinivalue_line(
        "markers",
        "requires_redis: marks tests that require a running Redis instance",
    )
    config.addinivalue_line(
        "markers",
        "requires_postgres: marks tests that require a running PostgreSQL instance",
    )


# =============================================================================
# CONFIGURATION FIXTURES
# =============================================================================


@pytest.fixture
def redis_connection_config() -> RedisConnectionConfig:
    """Redis connection configuration for testing."""
    return RedisConnectionConfig(
        url="redis://localhost:6379",
        db=15,  # Use db 15 for testing to avoid conflicts
        max_connections=5,
        socket_timeout=5,
    )


@pytest.fixture
def redis_storage_config() -> RedisStorageConfig:
    """Redis storage configuration for testing."""
    return RedisStorageConfig(
        current_state_ttl_seconds=60,
        zscore_buffer_ttl_seconds=300,
        alert_dedup_ttl_seconds=30,
    )


@pytest.fixture
def postgres_connection_config() -> PostgresConnectionConfig:
    """PostgreSQL connection configuration for testing."""
    return PostgresConnectionConfig(
        url="postgresql://surveillance:surveillance_dev@localhost:5432/surveillance_test",
        pool_size=2,
        max_overflow=3,
        pool_timeout=10,
    )


@pytest.fixture
def postgres_storage_config() -> PostgresStorageConfig:
    """PostgreSQL storage configuration for testing."""
    return PostgresStorageConfig(
        snapshot_retention_days=1,
        metrics_retention_days=1,
        alerts_retention_days=1,
        compress_after_days=1,
    )


# =============================================================================
# MOCK REDIS CLIENT
# =============================================================================


@pytest.fixture
def mock_redis_client() -> MagicMock:
    """
    Create a mock Redis client for testing without Redis.

    Returns:
        MagicMock: A mock Redis client with common methods mocked.
    """
    mock = MagicMock()
    mock.is_connected = True

    # Mock async methods
    mock.connect = AsyncMock(return_value=None)
    mock.disconnect = AsyncMock(return_value=None)
    mock.ping = AsyncMock(return_value=True)
    mock.set_orderbook = AsyncMock(return_value=None)
    mock.get_orderbook = AsyncMock(return_value=None)
    mock.set_alert = AsyncMock(return_value=None)
    mock.get_alert = AsyncMock(return_value=None)
    mock.get_active_alerts = AsyncMock(return_value=[])
    mock.set_health = AsyncMock(return_value=None)
    mock.get_health = AsyncMock(return_value=None)
    mock.add_zscore_sample = AsyncMock(return_value=None)
    mock.get_zscore_buffer = AsyncMock(return_value=[])
    mock.get_zscore_buffer_length = AsyncMock(return_value=0)
    mock.clear_zscore_buffer = AsyncMock(return_value=None)
    mock.publish_orderbook_update = AsyncMock(return_value=0)
    mock.publish_alert = AsyncMock(return_value=0)
    mock.publish_health_update = AsyncMock(return_value=0)
    mock.flush_db = AsyncMock(return_value=None)

    return mock


@pytest.fixture
def mock_postgres_client() -> MagicMock:
    """
    Create a mock PostgreSQL client for testing without PostgreSQL.

    Returns:
        MagicMock: A mock PostgreSQL client with common methods mocked.
    """
    mock = MagicMock()
    mock.is_connected = True

    # Mock async methods
    mock.connect = AsyncMock(return_value=None)
    mock.disconnect = AsyncMock(return_value=None)
    mock.ping = AsyncMock(return_value=True)
    mock.insert_orderbook_snapshots = AsyncMock(return_value=1)
    mock.query_orderbook_snapshots = AsyncMock(return_value=[])
    mock.insert_spread_metrics = AsyncMock(return_value=1)
    mock.query_spread_metrics = AsyncMock(return_value=[])
    mock.insert_depth_metrics = AsyncMock(return_value=1)
    mock.query_depth_metrics = AsyncMock(return_value=[])
    mock.insert_basis_metrics = AsyncMock(return_value=1)
    mock.query_basis_metrics = AsyncMock(return_value=[])
    mock.insert_alert = AsyncMock(return_value=None)
    mock.update_alert_status = AsyncMock(return_value=None)
    mock.query_alerts = AsyncMock(return_value=[])
    mock.insert_gap_marker = AsyncMock(return_value=None)
    mock.query_gap_markers = AsyncMock(return_value=[])
    mock.get_active_alerts_count = AsyncMock(return_value={})

    return mock


# =============================================================================
# SAMPLE DATA FIXTURES - ORDER BOOK
# =============================================================================


@pytest.fixture
def integration_utc_now() -> datetime:
    """Return current UTC datetime for integration tests."""
    return datetime.now(timezone.utc)


@pytest.fixture
def deep_bid_levels() -> List[PriceLevel]:
    """
    Deep bid levels for integration testing.

    Creates 20 bid levels from $50000 down to $49980 with varying quantities.
    Total notional: approximately $3M.
    """
    return [
        PriceLevel(
            price=Decimal(str(50000 - i)),
            quantity=Decimal(str((i % 5 + 1) * 0.5)),  # 0.5 to 2.5 BTC per level
        )
        for i in range(20)
    ]


@pytest.fixture
def deep_ask_levels() -> List[PriceLevel]:
    """
    Deep ask levels for integration testing.

    Creates 20 ask levels from $50001 up to $50020 with varying quantities.
    Total notional: approximately $3M.
    """
    return [
        PriceLevel(
            price=Decimal(str(50001 + i)),
            quantity=Decimal(str((i % 5 + 1) * 0.5)),  # 0.5 to 2.5 BTC per level
        )
        for i in range(20)
    ]


@pytest.fixture
def sample_perp_snapshot(
    deep_bid_levels: List[PriceLevel],
    deep_ask_levels: List[PriceLevel],
    integration_utc_now: datetime,
) -> OrderBookSnapshot:
    """
    Sample perpetual order book snapshot for integration testing.

    Mid price: $50000.50
    Spread: $1.00 (approx 0.02 bps)
    """
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=integration_utc_now,
        local_timestamp=integration_utc_now,
        sequence_id=100001,
        bids=deep_bid_levels,
        asks=deep_ask_levels,
        depth_levels=20,
    )


@pytest.fixture
def sample_spot_snapshot(
    integration_utc_now: datetime,
) -> OrderBookSnapshot:
    """
    Sample spot order book snapshot for integration testing.

    Mid price: $49995.50 (slightly below perp for positive basis)
    Spread: $1.00
    """
    bids = [
        PriceLevel(price=Decimal("49995"), quantity=Decimal("1.0")),
        PriceLevel(price=Decimal("49994"), quantity=Decimal("2.0")),
        PriceLevel(price=Decimal("49993"), quantity=Decimal("1.5")),
        PriceLevel(price=Decimal("49992"), quantity=Decimal("3.0")),
        PriceLevel(price=Decimal("49991"), quantity=Decimal("2.5")),
    ]
    asks = [
        PriceLevel(price=Decimal("49996"), quantity=Decimal("0.8")),
        PriceLevel(price=Decimal("49997"), quantity=Decimal("1.5")),
        PriceLevel(price=Decimal("49998"), quantity=Decimal("2.0")),
        PriceLevel(price=Decimal("49999"), quantity=Decimal("1.0")),
        PriceLevel(price=Decimal("50000"), quantity=Decimal("3.0")),
    ]
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-SPOT",
        timestamp=integration_utc_now,
        local_timestamp=integration_utc_now,
        sequence_id=200001,
        bids=bids,
        asks=asks,
        depth_levels=20,
    )


@pytest.fixture
def wide_spread_snapshot(
    integration_utc_now: datetime,
) -> OrderBookSnapshot:
    """
    Order book with wide spread for alert testing.

    Spread: $5.00 (approx 1 bps) - wide enough to trigger spread alerts.
    """
    bids = [
        PriceLevel(price=Decimal("49997.50"), quantity=Decimal("1.0")),
        PriceLevel(price=Decimal("49996.50"), quantity=Decimal("2.0")),
        PriceLevel(price=Decimal("49995.50"), quantity=Decimal("1.5")),
    ]
    asks = [
        PriceLevel(price=Decimal("50002.50"), quantity=Decimal("0.8")),
        PriceLevel(price=Decimal("50003.50"), quantity=Decimal("1.5")),
        PriceLevel(price=Decimal("50004.50"), quantity=Decimal("2.0")),
    ]
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=integration_utc_now,
        local_timestamp=integration_utc_now,
        sequence_id=100002,
        bids=bids,
        asks=asks,
        depth_levels=20,
    )


# =============================================================================
# SAMPLE DATA FIXTURES - METRICS
# =============================================================================


@pytest.fixture
def normal_spread_metrics() -> SpreadMetrics:
    """Normal spread metrics (within thresholds)."""
    return SpreadMetrics(
        spread_abs=Decimal("1.00"),
        spread_bps=Decimal("0.2"),
        mid_price=Decimal("50000.50"),
        zscore=Decimal("0.5"),
    )


@pytest.fixture
def high_spread_metrics() -> SpreadMetrics:
    """High spread metrics that should trigger warning alert."""
    return SpreadMetrics(
        spread_abs=Decimal("15.00"),
        spread_bps=Decimal("3.5"),  # > 3.0 bps threshold
        mid_price=Decimal("50000.00"),
        zscore=Decimal("2.5"),  # > 2.0 sigma threshold
    )


@pytest.fixture
def critical_spread_metrics() -> SpreadMetrics:
    """Critical spread metrics that should trigger critical alert."""
    return SpreadMetrics(
        spread_abs=Decimal("25.00"),
        spread_bps=Decimal("5.5"),  # > 5.0 bps threshold
        mid_price=Decimal("50000.00"),
        zscore=Decimal("3.5"),  # > 3.0 sigma threshold
    )


@pytest.fixture
def warmup_spread_metrics() -> SpreadMetrics:
    """Spread metrics with None zscore (warmup period)."""
    return SpreadMetrics(
        spread_abs=Decimal("15.00"),
        spread_bps=Decimal("3.5"),  # > 3.0 bps threshold BUT zscore is None
        mid_price=Decimal("50000.00"),
        zscore=None,  # Warmup - no z-score yet
    )


@pytest.fixture
def sample_depth_metrics_integration() -> DepthMetrics:
    """Sample depth metrics for integration testing."""
    return DepthMetrics(
        depth_5bps_bid=Decimal("250000.00"),
        depth_5bps_ask=Decimal("200000.00"),
        depth_5bps_total=Decimal("450000.00"),
        depth_10bps_bid=Decimal("500000.00"),
        depth_10bps_ask=Decimal("450000.00"),
        depth_10bps_total=Decimal("950000.00"),
        depth_25bps_bid=Decimal("1000000.00"),
        depth_25bps_ask=Decimal("900000.00"),
        depth_25bps_total=Decimal("1900000.00"),
        imbalance=Decimal("0.05"),
    )


@pytest.fixture
def low_depth_metrics() -> DepthMetrics:
    """Low depth metrics that should trigger depth alerts."""
    return DepthMetrics(
        depth_5bps_bid=Decimal("50000.00"),
        depth_5bps_ask=Decimal("40000.00"),
        depth_5bps_total=Decimal("90000.00"),
        depth_10bps_bid=Decimal("150000.00"),  # < $500K warning threshold
        depth_10bps_ask=Decimal("100000.00"),
        depth_10bps_total=Decimal("250000.00"),  # Below $500K threshold
        depth_25bps_bid=Decimal("300000.00"),
        depth_25bps_ask=Decimal("250000.00"),
        depth_25bps_total=Decimal("550000.00"),
        imbalance=Decimal("0.20"),
    )


@pytest.fixture
def sample_basis_metrics_integration() -> BasisMetrics:
    """Sample basis metrics for integration testing."""
    return BasisMetrics(
        basis_abs=Decimal("5.00"),
        basis_bps=Decimal("1.0"),
        perp_mid=Decimal("50005.00"),
        spot_mid=Decimal("50000.00"),
        zscore=Decimal("0.8"),
    )


@pytest.fixture
def high_basis_metrics() -> BasisMetrics:
    """High basis metrics that should trigger basis alert."""
    return BasisMetrics(
        basis_abs=Decimal("50.00"),
        basis_bps=Decimal("12.0"),  # > 10 bps warning threshold
        perp_mid=Decimal("50060.00"),
        spot_mid=Decimal("50000.00"),
        zscore=Decimal("2.5"),  # > 2.0 sigma threshold
    )


@pytest.fixture
def sample_imbalance_metrics() -> ImbalanceMetrics:
    """Sample imbalance metrics for integration testing."""
    return ImbalanceMetrics(
        top_of_book_imbalance=Decimal("0.15"),
        weighted_imbalance_5=Decimal("0.10"),
        weighted_imbalance_10=Decimal("0.08"),
    )


@pytest.fixture
def sample_aggregated_metrics(
    normal_spread_metrics: SpreadMetrics,
    sample_depth_metrics_integration: DepthMetrics,
    sample_basis_metrics_integration: BasisMetrics,
    sample_imbalance_metrics: ImbalanceMetrics,
    integration_utc_now: datetime,
) -> AggregatedMetrics:
    """Complete aggregated metrics for integration testing."""
    return AggregatedMetrics(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=integration_utc_now,
        spread=normal_spread_metrics,
        depth=sample_depth_metrics_integration,
        basis=sample_basis_metrics_integration,
        imbalance=sample_imbalance_metrics,
    )


# =============================================================================
# SAMPLE DATA FIXTURES - ALERTS
# =============================================================================


@pytest.fixture
def spread_warning_definition() -> AlertDefinition:
    """Spread warning alert definition."""
    return AlertDefinition(
        alert_type="spread_warning",
        name="Spread Warning",
        metric_name="spread_bps",
        default_priority=AlertPriority.P2,
        default_severity=AlertSeverity.WARNING,
        condition=AlertCondition.GT,
        requires_zscore=True,
        throttle_seconds=60,
        escalation_seconds=300,
        escalates_to="spread_critical",
    )


@pytest.fixture
def spread_critical_definition() -> AlertDefinition:
    """Spread critical alert definition."""
    return AlertDefinition(
        alert_type="spread_critical",
        name="Spread Critical",
        metric_name="spread_bps",
        default_priority=AlertPriority.P1,
        default_severity=AlertSeverity.CRITICAL,
        condition=AlertCondition.GT,
        requires_zscore=True,
        throttle_seconds=30,
    )


@pytest.fixture
def basis_warning_definition() -> AlertDefinition:
    """Basis warning alert definition with persistence requirement."""
    return AlertDefinition(
        alert_type="basis_warning",
        name="Basis Warning",
        metric_name="basis_bps",
        default_priority=AlertPriority.P2,
        default_severity=AlertSeverity.WARNING,
        condition=AlertCondition.ABS_GT,
        requires_zscore=True,
        persistence_seconds=120,  # Requires 2 minutes persistence
        throttle_seconds=60,
    )


@pytest.fixture
def spread_warning_threshold() -> AlertThreshold:
    """Spread warning threshold configuration."""
    return AlertThreshold(
        threshold=Decimal("3.0"),
        zscore_threshold=Decimal("2.0"),
    )


@pytest.fixture
def spread_critical_threshold() -> AlertThreshold:
    """Spread critical threshold configuration."""
    return AlertThreshold(
        threshold=Decimal("5.0"),
        zscore_threshold=Decimal("3.0"),
    )


@pytest.fixture
def basis_warning_threshold() -> AlertThreshold:
    """Basis warning threshold configuration."""
    return AlertThreshold(
        threshold=Decimal("10.0"),
        zscore_threshold=Decimal("2.0"),
    )


@pytest.fixture
def sample_triggered_alert(integration_utc_now: datetime) -> Alert:
    """Sample triggered alert for testing."""
    return Alert(
        alert_type="spread_warning",
        priority=AlertPriority.P2,
        severity=AlertSeverity.WARNING,
        exchange="binance",
        instrument="BTC-USDT-PERP",
        trigger_metric="spread_bps",
        trigger_value=Decimal("3.5"),
        trigger_threshold=Decimal("3.0"),
        trigger_condition=AlertCondition.GT,
        zscore_value=Decimal("2.3"),
        zscore_threshold=Decimal("2.0"),
        triggered_at=integration_utc_now,
    )


# =============================================================================
# SAMPLE DATA FIXTURES - HEALTH
# =============================================================================


@pytest.fixture
def healthy_status(integration_utc_now: datetime) -> HealthStatus:
    """Healthy connection status."""
    return HealthStatus(
        exchange="binance",
        status=ConnectionStatus.CONNECTED,
        last_message_at=integration_utc_now,
        message_count=12345,
        lag_ms=23,
        reconnect_count=0,
        gaps_last_hour=0,
    )


@pytest.fixture
def degraded_status(integration_utc_now: datetime) -> HealthStatus:
    """Degraded connection status (high lag)."""
    return HealthStatus(
        exchange="binance",
        status=ConnectionStatus.CONNECTED,
        last_message_at=integration_utc_now,
        message_count=12345,
        lag_ms=500,  # High lag
        reconnect_count=3,
        gaps_last_hour=2,
    )


@pytest.fixture
def sample_gap_marker_integration(integration_utc_now: datetime) -> GapMarker:
    """Sample gap marker for integration testing."""
    gap_start = datetime(2025, 1, 26, 12, 0, 0, tzinfo=timezone.utc)
    gap_end = datetime(2025, 1, 26, 12, 0, 45, tzinfo=timezone.utc)
    return GapMarker(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        gap_start=gap_start,
        gap_end=gap_end,
        duration_seconds=Decimal("45.0"),
        reason="websocket_disconnect",
        sequence_id_before=12345678,
        sequence_id_after=12345700,
    )


# =============================================================================
# UTILITY FIXTURES
# =============================================================================


@pytest.fixture
def generate_orderbook_sequence(
    integration_utc_now: datetime,
) -> Generator[OrderBookSnapshot, None, None]:
    """
    Generator that yields a sequence of order book snapshots with incrementing sequence IDs.

    Useful for testing z-score warmup behavior.
    """
    def _generator() -> Generator[OrderBookSnapshot, None, None]:
        base_price = Decimal("50000")
        for i in range(100):
            # Slight price variation
            price_offset = Decimal(str(i % 10 - 5))
            mid = base_price + price_offset

            bids = [
                PriceLevel(price=mid - Decimal("0.5"), quantity=Decimal("1.0")),
                PriceLevel(price=mid - Decimal("1.5"), quantity=Decimal("2.0")),
            ]
            asks = [
                PriceLevel(price=mid + Decimal("0.5"), quantity=Decimal("0.8")),
                PriceLevel(price=mid + Decimal("1.5"), quantity=Decimal("1.5")),
            ]

            yield OrderBookSnapshot(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                timestamp=integration_utc_now,
                local_timestamp=integration_utc_now,
                sequence_id=100000 + i,
                bids=bids,
                asks=asks,
                depth_levels=20,
            )

    return _generator()
