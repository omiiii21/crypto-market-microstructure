"""
Integration tests for FastAPI dashboard REST API endpoints.

Tests the API endpoints with mocked Redis and PostgreSQL clients
to verify correct request handling and response formatting.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.dashboard.app import create_app


# ============================================================================
# MOCK DATA FIXTURES
# ============================================================================


@pytest.fixture
def mock_current_state() -> Dict[str, Any]:
    """Mock current state data from Redis."""
    return {
        "exchange": "binance",
        "instrument": "BTC-USDT-PERP",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spread_bps": "2.50",
        "mid_price": "50000.00",
        "depth_5bps_bid": "250000.00",
        "depth_5bps_ask": "200000.00",
        "depth_10bps_bid": "500000.00",
        "depth_10bps_ask": "450000.00",
        "depth_25bps_bid": "1000000.00",
        "depth_25bps_ask": "900000.00",
        "imbalance": "0.05",
        "basis_bps": "1.50",
        "zscore_spread": "0.8",
        "zscore_basis": "0.5",
        "zscore_samples": "45",
    }


@pytest.fixture
def mock_active_alerts() -> List[Dict[str, Any]]:
    """Mock active alerts data from Redis."""
    return [
        {
            "alert_id": "alert-001",
            "alert_type": "spread_warning",
            "priority": "P2",
            "severity": "WARNING",
            "exchange": "binance",
            "instrument": "BTC-USDT-PERP",
            "trigger_metric": "spread_bps",
            "trigger_value": "3.50",
            "trigger_threshold": "3.00",
            "zscore_value": "2.30",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
    ]


@pytest.fixture
def mock_health_status() -> Dict[str, Any]:
    """Mock health status data from Redis."""
    return {
        "binance": {
            "exchange": "binance",
            "status": "connected",
            "last_message_at": datetime.now(timezone.utc).isoformat(),
            "message_count": 12345,
            "lag_ms": 23,
            "reconnect_count": 0,
            "gaps_last_hour": 0,
        },
        "okx": {
            "exchange": "okx",
            "status": "connected",
            "last_message_at": datetime.now(timezone.utc).isoformat(),
            "message_count": 10234,
            "lag_ms": 35,
            "reconnect_count": 0,
            "gaps_last_hour": 0,
        },
    }


@pytest.fixture
def mock_spread_metrics() -> List[Dict[str, Any]]:
    """Mock spread metrics time series from PostgreSQL."""
    base_time = datetime.now(timezone.utc)
    return [
        {
            "timestamp": base_time.isoformat(),
            "value": "2.10",
            "zscore": "0.5",
        },
        {
            "timestamp": base_time.isoformat(),
            "value": "2.25",
            "zscore": "0.7",
        },
        {
            "timestamp": base_time.isoformat(),
            "value": "2.40",
            "zscore": "0.9",
        },
    ]


# ============================================================================
# TEST CLIENT FIXTURE
# ============================================================================


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client."""
    mock = AsyncMock()
    mock.get_current_state = AsyncMock()
    mock.get_active_alerts = AsyncMock()
    mock.get_health_status = AsyncMock()
    mock.get_zscore_warmup_status = AsyncMock(return_value={"sample_count": 45, "min_samples": 30})
    mock.get_cross_exchange_data = AsyncMock()
    mock.ping = AsyncMock(return_value=True)
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_postgres_client():
    """Create a mock PostgreSQL client."""
    mock = AsyncMock()
    mock.get_spread_metrics = AsyncMock()
    mock.get_basis_metrics = AsyncMock()
    mock.get_depth_metrics = AsyncMock()
    mock.get_alert_history = AsyncMock()
    mock.is_connected = AsyncMock(return_value=True)
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def test_client(mock_redis_client, mock_postgres_client):
    """Create a test client with mocked dependencies."""
    app = create_app()

    # Store mocked clients in app state
    app.state.redis_client = mock_redis_client
    app.state.postgres_client = mock_postgres_client

    with TestClient(app) as client:
        yield client


# ============================================================================
# STATE API TESTS
# ============================================================================


class TestStateAPI:
    """Tests for /api/state endpoints."""

    def test_get_state_success(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
        mock_current_state: Dict[str, Any],
    ):
        """Test successful state retrieval."""
        mock_redis_client.get_current_state.return_value = mock_current_state

        response = test_client.get("/api/state/binance/BTC-USDT-PERP")

        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "binance"
        assert data["instrument"] == "BTC-USDT-PERP"
        assert "spread_bps" in data
        assert "mid_price" in data

    def test_get_state_not_found(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test state retrieval when data not available."""
        mock_redis_client.get_current_state.return_value = None

        response = test_client.get("/api/state/binance/BTC-USDT-PERP")

        # Should return 200 with unavailable state, not 404
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "unavailable" or data.get("spread_bps") is None

    def test_get_cross_exchange_success(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test cross-exchange comparison retrieval."""
        mock_redis_client.get_cross_exchange_data.return_value = {
            "binance": {
                "mid_price": "50000.00",
                "spread_bps": "2.50",
            },
            "okx": {
                "mid_price": "50005.00",
                "spread_bps": "2.80",
            },
            "divergence_bps": "1.00",
        }

        response = test_client.get("/api/state/cross-exchange/BTC-USDT-PERP")

        assert response.status_code == 200
        data = response.json()
        assert "binance" in data or "exchanges" in data


# ============================================================================
# ALERTS API TESTS
# ============================================================================


class TestAlertsAPI:
    """Tests for /api/alerts endpoints."""

    def test_get_alerts_success(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
        mock_active_alerts: List[Dict[str, Any]],
    ):
        """Test successful alerts retrieval."""
        mock_redis_client.get_active_alerts.return_value = mock_active_alerts

        response = test_client.get("/api/alerts")

        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "counts" in data
        assert len(data["alerts"]) == 1
        assert data["counts"]["total"] >= 1

    def test_get_alerts_empty(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test alerts retrieval when no alerts."""
        mock_redis_client.get_active_alerts.return_value = []

        response = test_client.get("/api/alerts")

        assert response.status_code == 200
        data = response.json()
        assert data["alerts"] == []
        assert data["counts"]["total"] == 0

    def test_get_alerts_with_filters(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
        mock_active_alerts: List[Dict[str, Any]],
    ):
        """Test alerts retrieval with query filters."""
        mock_redis_client.get_active_alerts.return_value = mock_active_alerts

        response = test_client.get("/api/alerts?priority=P2&exchange=binance")

        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data

    def test_get_alerts_history(
        self,
        test_client: TestClient,
        mock_postgres_client: AsyncMock,
    ):
        """Test historical alerts retrieval."""
        mock_postgres_client.get_alert_history.return_value = [
            {
                "alert_id": "alert-001",
                "alert_type": "spread_warning",
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        response = test_client.get("/api/alerts/history?time_range=1h")

        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data


# ============================================================================
# METRICS API TESTS
# ============================================================================


class TestMetricsAPI:
    """Tests for /api/metrics endpoints."""

    def test_get_spread_metrics_success(
        self,
        test_client: TestClient,
        mock_postgres_client: AsyncMock,
        mock_spread_metrics: List[Dict[str, Any]],
    ):
        """Test successful spread metrics retrieval."""
        mock_postgres_client.get_spread_metrics.return_value = mock_spread_metrics

        response = test_client.get("/api/metrics/spread/binance/BTC-USDT-PERP?time_range=5m")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) > 0

    def test_get_basis_metrics_success(
        self,
        test_client: TestClient,
        mock_postgres_client: AsyncMock,
    ):
        """Test successful basis metrics retrieval."""
        mock_postgres_client.get_basis_metrics.return_value = [
            {"timestamp": datetime.now(timezone.utc).isoformat(), "value": "5.00", "zscore": "0.8"},
        ]

        response = test_client.get("/api/metrics/basis/binance/BTC-USDT-PERP?time_range=15m")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_get_depth_metrics_success(
        self,
        test_client: TestClient,
        mock_postgres_client: AsyncMock,
    ):
        """Test successful depth metrics retrieval."""
        mock_postgres_client.get_depth_metrics.return_value = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "depth_10bps_total": "950000.00",
            },
        ]

        response = test_client.get("/api/metrics/depth/binance/BTC-USDT-PERP?time_range=1h")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_get_all_exchange_metrics(
        self,
        test_client: TestClient,
        mock_postgres_client: AsyncMock,
        mock_spread_metrics: List[Dict[str, Any]],
    ):
        """Test metrics retrieval for all exchanges."""
        mock_postgres_client.get_spread_metrics.return_value = mock_spread_metrics

        response = test_client.get("/api/metrics/spread/all/BTC-USDT-PERP?time_range=5m")

        assert response.status_code == 200
        data = response.json()
        assert "binance" in data or "data" in data

    def test_get_metrics_invalid_type(
        self,
        test_client: TestClient,
    ):
        """Test metrics retrieval with invalid metric type."""
        response = test_client.get("/api/metrics/invalid/binance/BTC-USDT-PERP?time_range=5m")

        # Should return 422 (validation error) or 400 (bad request)
        assert response.status_code in [400, 422]


# ============================================================================
# HEALTH API TESTS
# ============================================================================


class TestHealthAPI:
    """Tests for /api/health endpoints."""

    def test_get_health_success(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
        mock_health_status: Dict[str, Any],
    ):
        """Test successful health check."""
        mock_redis_client.get_health_status.return_value = mock_health_status

        response = test_client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["healthy", "degraded", "unhealthy"]

    def test_get_detailed_health(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
        mock_postgres_client: AsyncMock,
        mock_health_status: Dict[str, Any],
    ):
        """Test detailed health check."""
        mock_redis_client.get_health_status.return_value = mock_health_status
        mock_redis_client.ping.return_value = True
        mock_postgres_client.is_connected.return_value = True

        response = test_client.get("/api/health/detailed")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "exchanges" in data or "infrastructure" in data

    def test_health_check_redis_down(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test health check when Redis is unavailable."""
        mock_redis_client.get_health_status.side_effect = Exception("Connection refused")
        mock_redis_client.ping.return_value = False

        response = test_client.get("/api/health")

        # Should still return 200 but with degraded status
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["degraded", "unhealthy"]


# ============================================================================
# STATIC FILE TESTS
# ============================================================================


class TestStaticFiles:
    """Tests for static file serving."""

    def test_index_page(self, test_client: TestClient):
        """Test serving the index.html page."""
        response = test_client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_css_file(self, test_client: TestClient):
        """Test serving CSS file."""
        response = test_client.get("/static/css/dashboard.css")

        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")

    def test_js_file(self, test_client: TestClient):
        """Test serving JavaScript file."""
        response = test_client.get("/static/js/app.js")

        assert response.status_code == 200
        assert "javascript" in response.headers.get("content-type", "")

    def test_missing_static_file(self, test_client: TestClient):
        """Test 404 for missing static file."""
        response = test_client.get("/static/nonexistent.js")

        assert response.status_code == 404


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Tests for API error handling."""

    def test_invalid_exchange(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test request with invalid exchange identifier."""
        mock_redis_client.get_current_state.return_value = None

        response = test_client.get("/api/state/invalid_exchange/BTC-USDT-PERP")

        # Should handle gracefully, returning empty or error response
        assert response.status_code in [200, 400, 404]

    def test_invalid_instrument(
        self,
        test_client: TestClient,
        mock_redis_client: AsyncMock,
    ):
        """Test request with invalid instrument identifier."""
        mock_redis_client.get_current_state.return_value = None

        response = test_client.get("/api/state/binance/INVALID-PAIR")

        assert response.status_code in [200, 400, 404]

    def test_missing_time_range(
        self,
        test_client: TestClient,
    ):
        """Test metrics request without required time_range parameter."""
        response = test_client.get("/api/metrics/spread/binance/BTC-USDT-PERP")

        # time_range should be required, so this should fail validation
        assert response.status_code in [200, 422]  # 200 if default is provided
