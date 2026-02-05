"""
Pytest configuration and shared fixtures.

This module provides common fixtures used across all test modules.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List

import pytest

from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertSeverity,
    AlertThreshold,
)
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.metrics import BasisMetrics, DepthMetrics, SpreadMetrics
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot, TradeSide, TradeSnapshot


# ============================================================================
# TIME FIXTURES
# ============================================================================


@pytest.fixture
def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


# ============================================================================
# PRICE LEVEL FIXTURES
# ============================================================================


@pytest.fixture
def sample_bid_levels() -> List[PriceLevel]:
    """Sample bid levels sorted best (highest) to worst."""
    return [
        PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.0")),
        PriceLevel(price=Decimal("49999.00"), quantity=Decimal("2.0")),
        PriceLevel(price=Decimal("49998.00"), quantity=Decimal("1.5")),
        PriceLevel(price=Decimal("49997.00"), quantity=Decimal("3.0")),
        PriceLevel(price=Decimal("49996.00"), quantity=Decimal("2.5")),
    ]


@pytest.fixture
def sample_ask_levels() -> List[PriceLevel]:
    """Sample ask levels sorted best (lowest) to worst."""
    return [
        PriceLevel(price=Decimal("50001.00"), quantity=Decimal("0.8")),
        PriceLevel(price=Decimal("50002.00"), quantity=Decimal("1.5")),
        PriceLevel(price=Decimal("50003.00"), quantity=Decimal("2.0")),
        PriceLevel(price=Decimal("50004.00"), quantity=Decimal("1.0")),
        PriceLevel(price=Decimal("50005.00"), quantity=Decimal("3.0")),
    ]


# ============================================================================
# ORDER BOOK FIXTURES
# ============================================================================


@pytest.fixture
def sample_orderbook(
    sample_bid_levels: List[PriceLevel],
    sample_ask_levels: List[PriceLevel],
    utc_now: datetime,
) -> OrderBookSnapshot:
    """Sample order book snapshot for testing."""
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=utc_now,
        local_timestamp=utc_now,
        sequence_id=12345678,
        bids=sample_bid_levels,
        asks=sample_ask_levels,
        depth_levels=20,
    )


@pytest.fixture
def empty_orderbook(utc_now: datetime) -> OrderBookSnapshot:
    """Empty order book snapshot for edge case testing."""
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=utc_now,
        local_timestamp=utc_now,
        sequence_id=12345678,
        bids=[],
        asks=[],
        depth_levels=20,
    )


# ============================================================================
# TICKER FIXTURES
# ============================================================================


@pytest.fixture
def sample_perp_ticker(utc_now: datetime) -> TickerSnapshot:
    """Sample perpetual ticker snapshot."""
    return TickerSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=utc_now,
        last_price=Decimal("50000.50"),
        mark_price=Decimal("50001.00"),
        index_price=Decimal("49999.50"),
        volume_24h=Decimal("10000.5"),
        volume_24h_usd=Decimal("500025000.00"),
        high_24h=Decimal("51000.00"),
        low_24h=Decimal("49000.00"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=datetime(2025, 1, 26, 8, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_spot_ticker(utc_now: datetime) -> TickerSnapshot:
    """Sample spot ticker snapshot."""
    return TickerSnapshot(
        exchange="binance",
        instrument="BTC-USDT-SPOT",
        timestamp=utc_now,
        last_price=Decimal("49999.50"),
        volume_24h=Decimal("8000.0"),
        volume_24h_usd=Decimal("399960000.00"),
        high_24h=Decimal("50500.00"),
        low_24h=Decimal("49500.00"),
    )


@pytest.fixture
def sample_trade(utc_now: datetime) -> TradeSnapshot:
    """Sample trade snapshot."""
    return TradeSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=utc_now,
        price=Decimal("50000.00"),
        quantity=Decimal("0.5"),
        side=TradeSide.BUY,
        trade_id="123456789",
    )


# ============================================================================
# METRICS FIXTURES
# ============================================================================


@pytest.fixture
def sample_spread_metrics() -> SpreadMetrics:
    """Sample spread metrics."""
    return SpreadMetrics(
        spread_abs=Decimal("1.00"),
        spread_bps=Decimal("0.2"),
        mid_price=Decimal("50000.50"),
        zscore=Decimal("0.5"),
    )


@pytest.fixture
def sample_depth_metrics() -> DepthMetrics:
    """Sample depth metrics."""
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
def sample_basis_metrics() -> BasisMetrics:
    """Sample basis metrics."""
    return BasisMetrics(
        basis_abs=Decimal("5.00"),
        basis_bps=Decimal("1.0"),
        perp_mid=Decimal("50005.00"),
        spot_mid=Decimal("50000.00"),
        zscore=Decimal("0.8"),
    )


# ============================================================================
# ALERT FIXTURES
# ============================================================================


@pytest.fixture
def sample_alert_definition() -> AlertDefinition:
    """Sample alert definition."""
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
def sample_alert_threshold() -> AlertThreshold:
    """Sample alert threshold."""
    return AlertThreshold(
        threshold=Decimal("3.0"),
        zscore_threshold=Decimal("2.0"),
    )


@pytest.fixture
def sample_alert(utc_now: datetime) -> Alert:
    """Sample active alert."""
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
        triggered_at=utc_now,
    )


# ============================================================================
# HEALTH FIXTURES
# ============================================================================


@pytest.fixture
def sample_health_status(utc_now: datetime) -> HealthStatus:
    """Sample health status."""
    return HealthStatus(
        exchange="binance",
        status=ConnectionStatus.CONNECTED,
        last_message_at=utc_now,
        message_count=12345,
        lag_ms=23,
        reconnect_count=0,
        gaps_last_hour=0,
    )


@pytest.fixture
def sample_gap_marker(utc_now: datetime) -> GapMarker:
    """Sample gap marker."""
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
