"""
Unit tests for dashboard data fetchers.

Tests data fetching functions with mocked Redis and PostgreSQL clients.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.alerts import Alert, AlertPriority, AlertSeverity, AlertCondition
from src.models.health import HealthStatus, ConnectionStatus


@pytest.fixture
def mock_orderbook_snapshot():
    """Create a mock OrderBookSnapshot for testing."""
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=datetime.utcnow(),
        local_timestamp=datetime.utcnow(),
        sequence_id=12345678,
        bids=[
            PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0")),
            PriceLevel(price=Decimal("49999"), quantity=Decimal("2.0")),
            PriceLevel(price=Decimal("49998"), quantity=Decimal("1.5")),
        ],
        asks=[
            PriceLevel(price=Decimal("50001"), quantity=Decimal("0.5")),
            PriceLevel(price=Decimal("50002"), quantity=Decimal("1.0")),
            PriceLevel(price=Decimal("50003"), quantity=Decimal("2.0")),
        ],
    )


@pytest.fixture
def mock_alert():
    """Create a mock Alert for testing."""
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
        triggered_at=datetime.utcnow(),
    )


@pytest.fixture
def mock_health_status():
    """Create a mock HealthStatus for testing."""
    return HealthStatus(
        exchange="binance",
        status=ConnectionStatus.CONNECTED,
        last_message_at=datetime.utcnow(),
        message_count=12345,
        lag_ms=23,
        reconnect_count=0,
        gaps_last_hour=0,
    )


class TestGetCurrentState:
    """Tests for get_current_state function."""

    @pytest.mark.asyncio
    async def test_get_current_state_success(self, mock_orderbook_snapshot):
        """Test successful current state retrieval."""
        from services.dashboard.data.fetchers import get_current_state

        mock_redis = AsyncMock()
        mock_redis.get_orderbook.return_value = mock_orderbook_snapshot

        result = await get_current_state(mock_redis, "binance", "BTC-USDT-PERP")

        assert result is not None
        assert result["exchange"] == "binance"
        assert result["instrument"] == "BTC-USDT-PERP"
        assert result["mid_price"] is not None
        assert result["spread_bps"] is not None

    @pytest.mark.asyncio
    async def test_get_current_state_no_client(self):
        """Test current state retrieval with no client."""
        from services.dashboard.data.fetchers import get_current_state

        result = await get_current_state(None, "binance", "BTC-USDT-PERP")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_state_not_found(self):
        """Test current state retrieval when not found."""
        from services.dashboard.data.fetchers import get_current_state

        mock_redis = AsyncMock()
        mock_redis.get_orderbook.return_value = None

        result = await get_current_state(mock_redis, "binance", "BTC-USDT-PERP")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_state_exchange_all(self, mock_orderbook_snapshot):
        """Test current state retrieval with 'all' exchange defaults to binance."""
        from services.dashboard.data.fetchers import get_current_state

        mock_redis = AsyncMock()
        mock_redis.get_orderbook.return_value = mock_orderbook_snapshot

        result = await get_current_state(mock_redis, "all", "BTC-USDT-PERP")

        # Should call with binance
        mock_redis.get_orderbook.assert_called_with("binance", "BTC-USDT-PERP")


class TestGetZscoreWarmupStatus:
    """Tests for get_zscore_warmup_status function."""

    @pytest.mark.asyncio
    async def test_get_zscore_warmup_status_warming_up(self):
        """Test warmup status when still warming up."""
        from services.dashboard.data.fetchers import get_zscore_warmup_status

        mock_redis = AsyncMock()
        mock_redis.get_zscore_buffer_length.return_value = 15

        result = await get_zscore_warmup_status(
            mock_redis, "binance", "BTC-USDT-PERP", "spread_bps"
        )

        assert result["is_warmed_up"] is False
        assert result["sample_count"] == 15
        assert result["min_samples"] == 30

    @pytest.mark.asyncio
    async def test_get_zscore_warmup_status_warmed_up(self):
        """Test warmup status when warmed up."""
        from services.dashboard.data.fetchers import get_zscore_warmup_status

        mock_redis = AsyncMock()
        mock_redis.get_zscore_buffer_length.return_value = 30

        result = await get_zscore_warmup_status(
            mock_redis, "binance", "BTC-USDT-PERP", "spread_bps"
        )

        assert result["is_warmed_up"] is True
        assert result["sample_count"] == 30

    @pytest.mark.asyncio
    async def test_get_zscore_warmup_status_no_client(self):
        """Test warmup status with no client."""
        from services.dashboard.data.fetchers import get_zscore_warmup_status

        result = await get_zscore_warmup_status(
            None, "binance", "BTC-USDT-PERP", "spread_bps"
        )

        assert result["is_warmed_up"] is False
        assert result["sample_count"] == 0


class TestGetActiveAlerts:
    """Tests for get_active_alerts function."""

    @pytest.mark.asyncio
    async def test_get_active_alerts_success(self, mock_alert):
        """Test successful active alerts retrieval."""
        from services.dashboard.data.fetchers import get_active_alerts

        mock_redis = AsyncMock()
        mock_redis.get_active_alerts.return_value = [mock_alert]

        result = await get_active_alerts(mock_redis)

        assert len(result) == 1
        assert result[0].alert_type == "spread_warning"

    @pytest.mark.asyncio
    async def test_get_active_alerts_no_client(self):
        """Test active alerts retrieval with no client."""
        from services.dashboard.data.fetchers import get_active_alerts

        result = await get_active_alerts(None)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_active_alerts_empty(self):
        """Test active alerts retrieval when none found."""
        from services.dashboard.data.fetchers import get_active_alerts

        mock_redis = AsyncMock()
        mock_redis.get_active_alerts.return_value = []

        result = await get_active_alerts(mock_redis)

        assert result == []


class TestGetSpreadHistory:
    """Tests for get_spread_history function."""

    @pytest.mark.asyncio
    async def test_get_spread_history_success(self):
        """Test successful spread history retrieval."""
        from services.dashboard.data.fetchers import get_spread_history

        mock_postgres = AsyncMock()
        mock_postgres.query_spread_metrics_aggregated.return_value = [
            {
                "timestamp": datetime.utcnow(),
                "avg_spread_bps": Decimal("2.5"),
                "avg_zscore": Decimal("0.5"),
            }
        ]

        result = await get_spread_history(
            mock_postgres, "binance", "BTC-USDT-PERP", "1h"
        )

        assert "binance" in result
        assert len(result["binance"]) == 1
        assert result["binance"][0]["spread_bps"] == Decimal("2.5")

    @pytest.mark.asyncio
    async def test_get_spread_history_no_client(self):
        """Test spread history retrieval with no client."""
        from services.dashboard.data.fetchers import get_spread_history

        result = await get_spread_history(None, "binance", "BTC-USDT-PERP", "1h")

        assert result == {"binance": [], "okx": []}

    @pytest.mark.asyncio
    async def test_get_spread_history_all_exchanges(self):
        """Test spread history retrieval for all exchanges."""
        from services.dashboard.data.fetchers import get_spread_history

        mock_postgres = AsyncMock()
        mock_postgres.query_spread_metrics_aggregated.return_value = []

        result = await get_spread_history(mock_postgres, "all", "BTC-USDT-PERP", "1h")

        # Should call for both exchanges
        assert mock_postgres.query_spread_metrics_aggregated.call_count == 2


class TestGetBasisHistory:
    """Tests for get_basis_history function."""

    @pytest.mark.asyncio
    async def test_get_basis_history_success(self):
        """Test successful basis history retrieval."""
        from services.dashboard.data.fetchers import get_basis_history

        mock_postgres = AsyncMock()
        mock_postgres.query_basis_metrics_aggregated.return_value = [
            {
                "timestamp": datetime.utcnow(),
                "avg_basis_bps": Decimal("5.0"),
                "avg_zscore": Decimal("0.5"),
            }
        ]

        result = await get_basis_history(
            mock_postgres, "binance", "BTC-USDT-PERP", "1h"
        )

        assert "binance" in result
        assert len(result["binance"]) == 1

    @pytest.mark.asyncio
    async def test_get_basis_history_no_client(self):
        """Test basis history retrieval with no client."""
        from services.dashboard.data.fetchers import get_basis_history

        result = await get_basis_history(None, "binance", "BTC-USDT-PERP", "1h")

        assert result == {"binance": [], "okx": []}


class TestGetDepthCurrent:
    """Tests for get_depth_current function."""

    @pytest.mark.asyncio
    async def test_get_depth_current_success(self, mock_orderbook_snapshot):
        """Test successful current depth retrieval."""
        from services.dashboard.data.fetchers import get_depth_current

        mock_redis = AsyncMock()
        mock_redis.get_orderbook.return_value = mock_orderbook_snapshot

        result = await get_depth_current(mock_redis, "binance", "BTC-USDT-PERP")

        assert result is not None
        assert "depth_5bps_bid" in result
        assert "depth_10bps_bid" in result
        assert "depth_25bps_bid" in result
        assert "imbalance" in result

    @pytest.mark.asyncio
    async def test_get_depth_current_no_client(self):
        """Test current depth retrieval with no client."""
        from services.dashboard.data.fetchers import get_depth_current

        result = await get_depth_current(None, "binance", "BTC-USDT-PERP")

        assert result is None


class TestGetHealthStatus:
    """Tests for get_health_status function."""

    @pytest.mark.asyncio
    async def test_get_health_status_success(self, mock_health_status):
        """Test successful health status retrieval."""
        from services.dashboard.data.fetchers import get_health_status

        mock_redis = AsyncMock()
        mock_redis.get_all_health.return_value = {"binance": mock_health_status}

        result = await get_health_status(mock_redis)

        assert "binance" in result
        assert result["binance"].status == ConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_get_health_status_no_client(self):
        """Test health status retrieval with no client."""
        from services.dashboard.data.fetchers import get_health_status

        result = await get_health_status(None)

        assert result == {}


class TestGetCrossExchangeData:
    """Tests for get_cross_exchange_data function."""

    @pytest.mark.asyncio
    async def test_get_cross_exchange_data_success(self, mock_orderbook_snapshot):
        """Test successful cross-exchange data retrieval."""
        from services.dashboard.data.fetchers import get_cross_exchange_data

        mock_redis = AsyncMock()
        mock_redis.get_orderbook.return_value = mock_orderbook_snapshot

        result = await get_cross_exchange_data(mock_redis, "BTC-USDT-PERP")

        assert "binance" in result
        assert "okx" in result
        # Both should have the same snapshot (mocked)
        assert result["binance"]["mid_price"] is not None

    @pytest.mark.asyncio
    async def test_get_cross_exchange_data_no_client(self):
        """Test cross-exchange data retrieval with no client."""
        from services.dashboard.data.fetchers import get_cross_exchange_data

        result = await get_cross_exchange_data(None, "BTC-USDT-PERP")

        assert result == {}


class TestHelperFunctions:
    """Tests for helper functions in fetchers module."""

    def test_parse_time_range_5m(self):
        """Test parsing 5m time range."""
        from services.dashboard.data.fetchers import _parse_time_range

        end_time = datetime(2025, 1, 26, 12, 0, 0)
        start_time = _parse_time_range("5m", end_time)

        expected = datetime(2025, 1, 26, 11, 55, 0)
        assert start_time == expected

    def test_parse_time_range_1h(self):
        """Test parsing 1h time range."""
        from services.dashboard.data.fetchers import _parse_time_range

        end_time = datetime(2025, 1, 26, 12, 0, 0)
        start_time = _parse_time_range("1h", end_time)

        expected = datetime(2025, 1, 26, 11, 0, 0)
        assert start_time == expected

    def test_parse_time_range_24h(self):
        """Test parsing 24h time range."""
        from services.dashboard.data.fetchers import _parse_time_range

        end_time = datetime(2025, 1, 26, 12, 0, 0)
        start_time = _parse_time_range("24h", end_time)

        expected = datetime(2025, 1, 25, 12, 0, 0)
        assert start_time == expected

    def test_get_aggregation_interval_5m(self):
        """Test aggregation interval for 5m."""
        from services.dashboard.data.fetchers import _get_aggregation_interval

        assert _get_aggregation_interval("5m") == "1m"

    def test_get_aggregation_interval_4h(self):
        """Test aggregation interval for 4h."""
        from services.dashboard.data.fetchers import _get_aggregation_interval

        assert _get_aggregation_interval("4h") == "5m"

    def test_get_aggregation_interval_24h(self):
        """Test aggregation interval for 24h."""
        from services.dashboard.data.fetchers import _get_aggregation_interval

        assert _get_aggregation_interval("24h") == "15m"
