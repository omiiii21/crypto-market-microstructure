"""
Unit tests for Pydantic data models.

Tests validation, computed properties, and edge cases for all models.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

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
from src.models.metrics import BasisMetrics, DepthMetrics, SpreadMetrics
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.ticker import TickerSnapshot, TradeSide, TradeSnapshot


class TestPriceLevel:
    """Tests for PriceLevel model."""

    def test_create_price_level(self) -> None:
        """Test creating a price level."""
        level = PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.5"))
        assert level.price == Decimal("50000.00")
        assert level.quantity == Decimal("1.5")

    def test_notional_property(self) -> None:
        """Test notional calculation."""
        level = PriceLevel(price=Decimal("50000.00"), quantity=Decimal("2.0"))
        assert level.notional == Decimal("100000.00")

    def test_zero_quantity(self) -> None:
        """Test price level with zero quantity."""
        level = PriceLevel(price=Decimal("50000.00"), quantity=Decimal("0"))
        assert level.notional == Decimal("0")

    def test_negative_price_rejected(self) -> None:
        """Test that negative prices are rejected."""
        with pytest.raises(ValueError):
            PriceLevel(price=Decimal("-1.00"), quantity=Decimal("1.0"))


class TestOrderBookSnapshot:
    """Tests for OrderBookSnapshot model."""

    def test_create_orderbook(
        self, sample_bid_levels: list, sample_ask_levels: list, utc_now: datetime
    ) -> None:
        """Test creating an order book snapshot."""
        ob = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=utc_now,
            local_timestamp=utc_now,
            sequence_id=12345678,
            bids=sample_bid_levels,
            asks=sample_ask_levels,
        )
        assert ob.exchange == "binance"
        assert ob.instrument == "BTC-USDT-PERP"
        assert len(ob.bids) == 5
        assert len(ob.asks) == 5

    def test_best_bid_ask(self, sample_orderbook: OrderBookSnapshot) -> None:
        """Test best bid/ask properties."""
        assert sample_orderbook.best_bid == Decimal("50000.00")
        assert sample_orderbook.best_ask == Decimal("50001.00")

    def test_mid_price(self, sample_orderbook: OrderBookSnapshot) -> None:
        """Test mid price calculation."""
        expected = (Decimal("50000.00") + Decimal("50001.00")) / Decimal("2")
        assert sample_orderbook.mid_price == expected

    def test_spread(self, sample_orderbook: OrderBookSnapshot) -> None:
        """Test spread calculation."""
        assert sample_orderbook.spread == Decimal("1.00")

    def test_spread_bps(self, sample_orderbook: OrderBookSnapshot) -> None:
        """Test spread in basis points."""
        # spread = 1.00, mid = 50000.50
        # spread_bps = 1.00 / 50000.50 * 10000 = ~0.2 bps
        spread_bps = sample_orderbook.spread_bps
        assert spread_bps is not None
        assert spread_bps < Decimal("1.0")  # Very tight spread

    def test_empty_orderbook(self, empty_orderbook: OrderBookSnapshot) -> None:
        """Test empty order book properties."""
        assert empty_orderbook.best_bid is None
        assert empty_orderbook.best_ask is None
        assert empty_orderbook.mid_price is None
        assert empty_orderbook.spread is None
        assert empty_orderbook.is_valid is False

    def test_crossed_book_rejected(self, utc_now: datetime) -> None:
        """Test that crossed order books are rejected."""
        with pytest.raises(ValueError, match="Crossed order book"):
            OrderBookSnapshot(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                timestamp=utc_now,
                local_timestamp=utc_now,
                sequence_id=12345678,
                bids=[PriceLevel(price=Decimal("50001.00"), quantity=Decimal("1.0"))],
                asks=[PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.0"))],
            )

    def test_unsorted_bids_rejected(self, utc_now: datetime) -> None:
        """Test that unsorted bids are rejected."""
        with pytest.raises(ValueError, match="Bids must be sorted"):
            OrderBookSnapshot(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                timestamp=utc_now,
                local_timestamp=utc_now,
                sequence_id=12345678,
                bids=[
                    PriceLevel(price=Decimal("49999.00"), quantity=Decimal("1.0")),
                    PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.0")),
                ],
                asks=[PriceLevel(price=Decimal("50001.00"), quantity=Decimal("1.0"))],
            )


class TestTickerSnapshot:
    """Tests for TickerSnapshot model."""

    def test_create_perp_ticker(self, sample_perp_ticker: TickerSnapshot) -> None:
        """Test creating a perpetual ticker."""
        assert sample_perp_ticker.is_perpetual is True
        assert sample_perp_ticker.mark_price is not None
        assert sample_perp_ticker.funding_rate is not None

    def test_create_spot_ticker(self, sample_spot_ticker: TickerSnapshot) -> None:
        """Test creating a spot ticker."""
        assert sample_spot_ticker.is_perpetual is False
        assert sample_spot_ticker.mark_price is None
        assert sample_spot_ticker.funding_rate is None

    def test_mark_index_deviation(self, sample_perp_ticker: TickerSnapshot) -> None:
        """Test mark-index deviation calculation."""
        deviation = sample_perp_ticker.mark_index_deviation_bps
        assert deviation is not None
        # mark = 50001.00, index = 49999.50
        # deviation = (50001 - 49999.5) / 49999.5 * 10000 = ~0.3 bps
        assert deviation > Decimal("0")

    def test_annualized_funding(self, sample_perp_ticker: TickerSnapshot) -> None:
        """Test annualized funding rate calculation."""
        ann_funding = sample_perp_ticker.funding_rate_annualized
        assert ann_funding is not None
        # 0.0001 * 3 * 365 = 0.1095 = 10.95%
        assert ann_funding == Decimal("0.0001") * Decimal("3") * Decimal("365")

    def test_invalid_24h_range_rejected(self, utc_now: datetime) -> None:
        """Test that invalid 24h range is rejected."""
        with pytest.raises(ValueError, match="24h high"):
            TickerSnapshot(
                exchange="binance",
                instrument="BTC-USDT-SPOT",
                timestamp=utc_now,
                last_price=Decimal("50000.00"),
                volume_24h=Decimal("1000.0"),
                volume_24h_usd=Decimal("50000000.00"),
                high_24h=Decimal("49000.00"),  # Lower than low
                low_24h=Decimal("50000.00"),
            )


class TestTradeSnapshot:
    """Tests for TradeSnapshot model."""

    def test_create_trade(self, sample_trade: TradeSnapshot) -> None:
        """Test creating a trade snapshot."""
        assert sample_trade.price == Decimal("50000.00")
        assert sample_trade.quantity == Decimal("0.5")
        assert sample_trade.side == TradeSide.BUY

    def test_notional(self, sample_trade: TradeSnapshot) -> None:
        """Test trade notional calculation."""
        assert sample_trade.notional == Decimal("25000.00")

    def test_is_buy_sell(self, sample_trade: TradeSnapshot) -> None:
        """Test buy/sell properties."""
        assert sample_trade.is_buy is True
        assert sample_trade.is_sell is False


class TestSpreadMetrics:
    """Tests for SpreadMetrics model."""

    def test_create_spread_metrics(self, sample_spread_metrics: SpreadMetrics) -> None:
        """Test creating spread metrics."""
        assert sample_spread_metrics.spread_bps == Decimal("0.2")
        assert sample_spread_metrics.zscore == Decimal("0.5")

    def test_zscore_available(self, sample_spread_metrics: SpreadMetrics) -> None:
        """Test z-score availability check."""
        assert sample_spread_metrics.is_zscore_available is True

    def test_zscore_unavailable(self) -> None:
        """Test z-score unavailable during warmup."""
        metrics = SpreadMetrics(
            spread_abs=Decimal("1.00"),
            spread_bps=Decimal("0.2"),
            mid_price=Decimal("50000.50"),
            zscore=None,  # Warmup
        )
        assert metrics.is_zscore_available is False


class TestBasisMetrics:
    """Tests for BasisMetrics model."""

    def test_create_basis_metrics(self, sample_basis_metrics: BasisMetrics) -> None:
        """Test creating basis metrics."""
        assert sample_basis_metrics.basis_abs == Decimal("5.00")
        assert sample_basis_metrics.basis_bps == Decimal("1.0")

    def test_is_premium(self, sample_basis_metrics: BasisMetrics) -> None:
        """Test premium detection."""
        assert sample_basis_metrics.is_premium is True
        assert sample_basis_metrics.is_discount is False

    def test_is_discount(self) -> None:
        """Test discount detection."""
        metrics = BasisMetrics(
            basis_abs=Decimal("-5.00"),
            basis_bps=Decimal("-1.0"),
            perp_mid=Decimal("49995.00"),
            spot_mid=Decimal("50000.00"),
        )
        assert metrics.is_discount is True
        assert metrics.is_premium is False


class TestAlertCondition:
    """Tests for AlertCondition evaluation."""

    def test_gt_condition(self) -> None:
        """Test greater than condition."""
        assert AlertCondition.GT.evaluate(Decimal("5.0"), Decimal("3.0")) is True
        assert AlertCondition.GT.evaluate(Decimal("3.0"), Decimal("5.0")) is False
        assert AlertCondition.GT.evaluate(Decimal("3.0"), Decimal("3.0")) is False

    def test_lt_condition(self) -> None:
        """Test less than condition."""
        assert AlertCondition.LT.evaluate(Decimal("2.0"), Decimal("3.0")) is True
        assert AlertCondition.LT.evaluate(Decimal("5.0"), Decimal("3.0")) is False

    def test_abs_gt_condition(self) -> None:
        """Test absolute value greater than condition."""
        assert AlertCondition.ABS_GT.evaluate(Decimal("-5.0"), Decimal("3.0")) is True
        assert AlertCondition.ABS_GT.evaluate(Decimal("5.0"), Decimal("3.0")) is True
        assert AlertCondition.ABS_GT.evaluate(Decimal("-2.0"), Decimal("3.0")) is False


class TestAlert:
    """Tests for Alert model."""

    def test_create_alert(self, sample_alert: Alert) -> None:
        """Test creating an alert."""
        assert sample_alert.alert_type == "spread_warning"
        assert sample_alert.priority == AlertPriority.P2
        assert sample_alert.is_active is True

    def test_acknowledge_alert(self, sample_alert: Alert, utc_now: datetime) -> None:
        """Test acknowledging an alert."""
        acked = sample_alert.acknowledge(utc_now)
        assert acked.is_acknowledged is True
        assert acked.acknowledged_at == utc_now

    def test_resolve_alert(self, sample_alert: Alert, utc_now: datetime) -> None:
        """Test resolving an alert."""
        resolved = sample_alert.resolve(
            resolution_type="auto",
            resolution_value=Decimal("2.5"),
            timestamp=utc_now,
        )
        assert resolved.is_active is False
        assert resolved.resolution_type == "auto"
        assert resolved.resolution_value == Decimal("2.5")

    def test_escalate_alert(self, sample_alert: Alert, utc_now: datetime) -> None:
        """Test escalating an alert."""
        escalated = sample_alert.escalate(AlertPriority.P1, utc_now)
        assert escalated.is_escalated is True
        assert escalated.priority == AlertPriority.P1
        assert escalated.original_priority == AlertPriority.P2


class TestHealthStatus:
    """Tests for HealthStatus model."""

    def test_create_health_status(self, sample_health_status: HealthStatus) -> None:
        """Test creating health status."""
        assert sample_health_status.exchange == "binance"
        assert sample_health_status.status == ConnectionStatus.CONNECTED
        assert sample_health_status.is_healthy is True

    def test_degraded_detection(self, utc_now: datetime) -> None:
        """Test degraded status detection."""
        health = HealthStatus(
            exchange="binance",
            status=ConnectionStatus.CONNECTED,
            last_message_at=utc_now,
            lag_ms=1500,  # High lag
            gaps_last_hour=10,  # Many gaps
        )
        assert health.is_healthy is False
        assert health.is_degraded is True


class TestGapMarker:
    """Tests for GapMarker model."""

    def test_create_gap_marker(self, sample_gap_marker: GapMarker) -> None:
        """Test creating a gap marker."""
        assert sample_gap_marker.duration_seconds == Decimal("45.0")
        assert sample_gap_marker.reason == "websocket_disconnect"

    def test_is_significant(self, sample_gap_marker: GapMarker) -> None:
        """Test significant gap detection."""
        assert sample_gap_marker.is_significant is True

    def test_sequence_gap_size(self, sample_gap_marker: GapMarker) -> None:
        """Test sequence gap size calculation."""
        # 12345700 - 12345678 - 1 = 21
        assert sample_gap_marker.sequence_gap_size == 21

    def test_invalid_gap_rejected(self) -> None:
        """Test that invalid gap (end before start) is rejected."""
        with pytest.raises(ValueError, match="gap_end"):
            GapMarker(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                gap_start=datetime(2025, 1, 26, 12, 0, 45, tzinfo=timezone.utc),
                gap_end=datetime(2025, 1, 26, 12, 0, 0, tzinfo=timezone.utc),
                duration_seconds=Decimal("-45.0"),
                reason="test",
            )
