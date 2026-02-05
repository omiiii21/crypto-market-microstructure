"""
Unit tests for dashboard components.

Tests component rendering with mock data to ensure proper display
of metrics, alerts, charts, and health status.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from dash import html

from src.models.alerts import Alert, AlertPriority, AlertSeverity, AlertCondition
from src.models.health import HealthStatus, ConnectionStatus


class TestStateCard:
    """Tests for state_card component."""

    def test_render_zscore_indicator_warmup(self):
        """Test z-score indicator during warmup period."""
        from services.dashboard.components.state_card import render_zscore_indicator

        result = render_zscore_indicator(None, sample_count=15, min_samples=30)

        # Should be a span with warmup class
        assert isinstance(result, html.Span)
        assert "zscore-warmup" in result.className

    def test_render_zscore_indicator_active_normal(self):
        """Test z-score indicator when active with normal value."""
        from services.dashboard.components.state_card import render_zscore_indicator

        result = render_zscore_indicator(Decimal("1.5"), sample_count=30, min_samples=30)

        assert isinstance(result, html.Span)
        assert "zscore-normal" in result.className
        assert "1.5" in result.children

    def test_render_zscore_indicator_active_warning(self):
        """Test z-score indicator when active with warning value."""
        from services.dashboard.components.state_card import render_zscore_indicator

        result = render_zscore_indicator(Decimal("2.5"), sample_count=30, min_samples=30)

        assert isinstance(result, html.Span)
        assert "zscore-warning" in result.className

    def test_render_zscore_indicator_active_critical(self):
        """Test z-score indicator when active with critical value."""
        from services.dashboard.components.state_card import render_zscore_indicator

        result = render_zscore_indicator(Decimal("3.5"), sample_count=30, min_samples=30)

        assert isinstance(result, html.Span)
        assert "zscore-critical" in result.className

    def test_get_status_class_normal(self):
        """Test status class for normal value."""
        from services.dashboard.components.state_card import get_status_class

        result = get_status_class(
            Decimal("2.0"),
            warning_threshold=Decimal("3.0"),
            critical_threshold=Decimal("5.0"),
        )

        assert result == "status-normal"

    def test_get_status_class_warning(self):
        """Test status class for warning value."""
        from services.dashboard.components.state_card import get_status_class

        result = get_status_class(
            Decimal("4.0"),
            warning_threshold=Decimal("3.0"),
            critical_threshold=Decimal("5.0"),
        )

        assert result == "status-warning"

    def test_get_status_class_critical(self):
        """Test status class for critical value."""
        from services.dashboard.components.state_card import get_status_class

        result = get_status_class(
            Decimal("6.0"),
            warning_threshold=Decimal("3.0"),
            critical_threshold=Decimal("5.0"),
        )

        assert result == "status-critical"

    def test_get_status_class_unavailable(self):
        """Test status class for None value."""
        from services.dashboard.components.state_card import get_status_class

        result = get_status_class(None)

        assert result == "status-unavailable"

    def test_get_status_class_lower_worse(self):
        """Test status class when lower values are worse (depth)."""
        from services.dashboard.components.state_card import get_status_class

        # Below critical threshold
        result = get_status_class(
            Decimal("150000"),
            warning_threshold=Decimal("500000"),
            critical_threshold=Decimal("200000"),
            is_lower_worse=True,
        )

        assert result == "status-critical"

    def test_format_metric_value_normal(self):
        """Test metric value formatting."""
        from services.dashboard.components.state_card import format_metric_value

        result = format_metric_value(Decimal("2.5"), precision=2)

        assert result == "2.50"

    def test_format_metric_value_with_prefix_suffix(self):
        """Test metric value formatting with prefix and suffix."""
        from services.dashboard.components.state_card import format_metric_value

        result = format_metric_value(
            Decimal("1500000"),
            precision=2,
            prefix="$",
            suffix="M",
            scale_factor=1e-6,
        )

        assert result == "$1.50M"

    def test_format_metric_value_none(self):
        """Test metric value formatting for None."""
        from services.dashboard.components.state_card import format_metric_value

        result = format_metric_value(None)

        assert result == "--"

    def test_create_current_state_panel(self):
        """Test creating the current state panel."""
        from services.dashboard.components.state_card import create_current_state_panel

        result = create_current_state_panel()

        assert isinstance(result, html.Div)
        assert "panel-container" in result.className


class TestAlertList:
    """Tests for alert_list component."""

    def test_format_duration_seconds(self):
        """Test duration formatting for seconds."""
        from services.dashboard.components.alert_list import format_duration

        triggered_at = datetime.utcnow()
        result = format_duration(triggered_at)

        assert "s" in result

    def test_get_priority_badge_p1(self):
        """Test priority badge for P1."""
        from services.dashboard.components.alert_list import get_priority_badge

        result = get_priority_badge(AlertPriority.P1)

        assert isinstance(result, html.Span)
        assert "badge-p1" in result.className

    def test_get_priority_badge_p2(self):
        """Test priority badge for P2."""
        from services.dashboard.components.alert_list import get_priority_badge

        result = get_priority_badge(AlertPriority.P2)

        assert "badge-p2" in result.className

    def test_get_priority_badge_p3(self):
        """Test priority badge for P3."""
        from services.dashboard.components.alert_list import get_priority_badge

        result = get_priority_badge(AlertPriority.P3)

        assert "badge-p3" in result.className

    def test_render_no_alerts_message(self):
        """Test rendering no alerts message."""
        from services.dashboard.components.alert_list import render_no_alerts_message

        result = render_no_alerts_message()

        assert isinstance(result, html.Div)
        assert "no-data-message" in result.className

    def test_render_alerts_list_empty(self):
        """Test rendering empty alerts list."""
        from services.dashboard.components.alert_list import render_alerts_list

        result = render_alerts_list([])

        assert len(result) == 1
        assert "no-data-message" in result[0].className

    def test_render_alerts_list_with_alerts(self):
        """Test rendering alerts list with alerts."""
        from services.dashboard.components.alert_list import render_alerts_list

        alerts = [
            Alert(
                alert_type="spread_warning",
                priority=AlertPriority.P2,
                severity=AlertSeverity.WARNING,
                exchange="binance",
                instrument="BTC-USDT-PERP",
                trigger_metric="spread_bps",
                trigger_value=Decimal("3.5"),
                trigger_threshold=Decimal("3.0"),
                trigger_condition=AlertCondition.GT,
                triggered_at=datetime.utcnow(),
            ),
        ]

        result = render_alerts_list(alerts)

        assert len(result) == 1
        assert "alert-card" in result[0].className

    def test_get_priority_counts(self):
        """Test counting alerts by priority."""
        from services.dashboard.components.alert_list import get_priority_counts

        alerts = [
            Alert(
                alert_type="spread_warning",
                priority=AlertPriority.P1,
                severity=AlertSeverity.CRITICAL,
                exchange="binance",
                instrument="BTC-USDT-PERP",
                trigger_metric="spread_bps",
                trigger_value=Decimal("5.5"),
                trigger_threshold=Decimal("5.0"),
                trigger_condition=AlertCondition.GT,
                triggered_at=datetime.utcnow(),
            ),
            Alert(
                alert_type="spread_warning",
                priority=AlertPriority.P2,
                severity=AlertSeverity.WARNING,
                exchange="binance",
                instrument="BTC-USDT-PERP",
                trigger_metric="spread_bps",
                trigger_value=Decimal("3.5"),
                trigger_threshold=Decimal("3.0"),
                trigger_condition=AlertCondition.GT,
                triggered_at=datetime.utcnow(),
            ),
        ]

        counts = get_priority_counts(alerts)

        assert counts["P1"] == 1
        assert counts["P2"] == 1
        assert counts["P3"] == 0
        assert counts["total"] == 2


class TestSpreadChart:
    """Tests for spread_chart component."""

    def test_create_empty_spread_chart(self):
        """Test creating empty spread chart."""
        from services.dashboard.components.spread_chart import create_empty_spread_chart

        fig = create_empty_spread_chart()

        assert fig is not None
        assert fig.layout.height == 300

    def test_create_spread_chart_with_data(self):
        """Test creating spread chart with data."""
        from services.dashboard.components.spread_chart import create_spread_chart

        binance_data = [
            {"timestamp": datetime.utcnow(), "spread_bps": 2.0, "zscore": 0.5},
        ]

        fig = create_spread_chart(
            binance_data=binance_data,
            okx_data=[],
            warning_threshold=3.0,
            critical_threshold=5.0,
        )

        assert fig is not None
        # Should have at least the Binance trace
        assert len(fig.data) >= 1


class TestBasisChart:
    """Tests for basis_chart component."""

    def test_create_empty_basis_chart(self):
        """Test creating empty basis chart."""
        from services.dashboard.components.basis_chart import create_empty_basis_chart

        fig = create_empty_basis_chart()

        assert fig is not None
        assert fig.layout.height == 300

    def test_create_basis_chart_with_data(self):
        """Test creating basis chart with data."""
        from services.dashboard.components.basis_chart import create_basis_chart

        binance_data = [
            {"timestamp": datetime.utcnow(), "basis_bps": 5.0, "zscore": 0.5},
        ]

        fig = create_basis_chart(
            binance_data=binance_data,
            okx_data=[],
            warning_threshold=10.0,
            critical_threshold=20.0,
        )

        assert fig is not None


class TestDepthHeatmap:
    """Tests for depth_heatmap component."""

    def test_create_empty_depth_chart(self):
        """Test creating empty depth chart."""
        from services.dashboard.components.depth_heatmap import create_empty_depth_chart

        fig = create_empty_depth_chart()

        assert fig is not None
        assert fig.layout.height == 250

    def test_create_depth_chart_with_data(self):
        """Test creating depth chart with data."""
        from services.dashboard.components.depth_heatmap import create_depth_chart

        fig = create_depth_chart(
            depth_5bps_bid=100000,
            depth_5bps_ask=90000,
            depth_10bps_bid=250000,
            depth_10bps_ask=230000,
            depth_25bps_bid=500000,
            depth_25bps_ask=480000,
        )

        assert fig is not None
        # Should have bid and ask traces
        assert len(fig.data) == 2

    def test_format_usd_millions(self):
        """Test USD formatting in millions."""
        from services.dashboard.components.depth_heatmap import format_usd_millions

        assert format_usd_millions(1_500_000) == "$1.50M"
        assert format_usd_millions(500_000) == "$500.0K"
        assert format_usd_millions(100) == "$100"

    def test_render_imbalance_indicator_positive(self):
        """Test imbalance indicator for positive (bid heavy)."""
        from services.dashboard.components.depth_heatmap import render_imbalance_indicator

        value, direction = render_imbalance_indicator(0.25)

        assert "text-success" in str(value)
        assert "Bid Heavy" in str(direction)

    def test_render_imbalance_indicator_negative(self):
        """Test imbalance indicator for negative (ask heavy)."""
        from services.dashboard.components.depth_heatmap import render_imbalance_indicator

        value, direction = render_imbalance_indicator(-0.25)

        assert "text-danger" in str(value)
        assert "Ask Heavy" in str(direction)

    def test_render_imbalance_indicator_balanced(self):
        """Test imbalance indicator for balanced."""
        from services.dashboard.components.depth_heatmap import render_imbalance_indicator

        value, direction = render_imbalance_indicator(0.05)

        assert "Balanced" in str(direction)


class TestCrossExchange:
    """Tests for cross_exchange component."""

    def test_format_price(self):
        """Test price formatting."""
        from services.dashboard.components.cross_exchange import format_price

        assert format_price(Decimal("50000.50")) == "$50,000.50"
        assert format_price(None) == "$--"

    def test_format_divergence_normal(self):
        """Test divergence formatting for normal value."""
        from services.dashboard.components.cross_exchange import format_divergence

        value, css_class, direction = format_divergence(Decimal("1.5"))

        assert "1.5" in value
        assert css_class == "price-divergence-normal"

    def test_format_divergence_warning(self):
        """Test divergence formatting for warning value."""
        from services.dashboard.components.cross_exchange import format_divergence

        value, css_class, direction = format_divergence(Decimal("4.0"))

        assert css_class == "price-divergence-warning"

    def test_format_divergence_critical(self):
        """Test divergence formatting for critical value."""
        from services.dashboard.components.cross_exchange import format_divergence

        value, css_class, direction = format_divergence(Decimal("6.0"))

        assert css_class == "price-divergence-critical"


class TestHealthPanel:
    """Tests for health_panel component."""

    def test_format_timestamp(self):
        """Test timestamp formatting."""
        from services.dashboard.components.health_panel import format_timestamp

        ts = datetime(2025, 1, 26, 12, 30, 45)
        result = format_timestamp(ts)

        assert result == "12:30:45"

    def test_format_timestamp_none(self):
        """Test timestamp formatting for None."""
        from services.dashboard.components.health_panel import format_timestamp

        result = format_timestamp(None)

        assert result == "--:--:--"

    def test_get_connection_status_class_connected(self):
        """Test status class for connected."""
        from services.dashboard.components.health_panel import get_connection_status_class

        result = get_connection_status_class(ConnectionStatus.CONNECTED)

        assert result == "health-connected"

    def test_get_connection_status_class_disconnected(self):
        """Test status class for disconnected."""
        from services.dashboard.components.health_panel import get_connection_status_class

        result = get_connection_status_class(ConnectionStatus.DISCONNECTED)

        assert result == "health-disconnected"

    def test_render_overall_status_healthy(self):
        """Test overall status when all exchanges are healthy."""
        from services.dashboard.components.health_panel import render_overall_status

        health_dict = {
            "binance": HealthStatus(
                exchange="binance",
                status=ConnectionStatus.CONNECTED,
                message_count=1000,
                lag_ms=50,
                gaps_last_hour=0,
            ),
            "okx": HealthStatus(
                exchange="okx",
                status=ConnectionStatus.CONNECTED,
                message_count=1000,
                lag_ms=50,
                gaps_last_hour=0,
            ),
        }

        text, badge_class = render_overall_status(health_dict)

        assert text == "Healthy"
        assert badge_class == "bg-success"

    def test_render_overall_status_disconnected(self):
        """Test overall status when an exchange is disconnected."""
        from services.dashboard.components.health_panel import render_overall_status

        health_dict = {
            "binance": HealthStatus(
                exchange="binance",
                status=ConnectionStatus.DISCONNECTED,
                message_count=0,
                lag_ms=0,
                gaps_last_hour=0,
            ),
        }

        text, badge_class = render_overall_status(health_dict)

        assert text == "Disconnected"
        assert badge_class == "bg-danger"

    def test_render_database_status_connected(self):
        """Test database status when connected."""
        from services.dashboard.components.health_panel import render_database_status

        result = render_database_status(True, "Redis")

        assert "text-success" in str(result)
        assert "Connected" in str(result)

    def test_render_database_status_disconnected(self):
        """Test database status when disconnected."""
        from services.dashboard.components.health_panel import render_database_status

        result = render_database_status(False, "Redis")

        assert "text-danger" in str(result)
        assert "Disconnected" in str(result)
