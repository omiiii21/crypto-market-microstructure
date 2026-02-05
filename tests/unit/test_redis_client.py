"""
Unit tests for the Redis client.

This module tests the RedisClient class functionality including:
- Connection management
- Order book storage and retrieval
- Z-score buffer operations
- Alert storage and indexing
- Health status operations
- Pub/Sub functionality

Note:
    These tests use mocking to avoid requiring a real Redis connection.
    Integration tests with a real Redis instance should be in tests/integration/.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.models import RedisConnectionConfig, RedisStorageConfig
from src.models.alerts import Alert, AlertCondition, AlertPriority, AlertSeverity
from src.models.health import ConnectionStatus, HealthStatus
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.storage.redis_client import (
    RedisClient,
    RedisClientError,
    RedisConnectionException,
    RedisOperationError,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def redis_config() -> RedisConnectionConfig:
    """Sample Redis connection configuration."""
    return RedisConnectionConfig(
        url="redis://localhost:6379",
        db=0,
        max_connections=10,
        socket_timeout=5,
    )


@pytest.fixture
def storage_config() -> RedisStorageConfig:
    """Sample Redis storage configuration."""
    return RedisStorageConfig(
        current_state_ttl_seconds=60,
        zscore_buffer_ttl_seconds=600,
        alert_dedup_ttl_seconds=60,
    )


@pytest.fixture
def redis_client(
    redis_config: RedisConnectionConfig, storage_config: RedisStorageConfig
) -> RedisClient:
    """Create a RedisClient instance without connecting."""
    return RedisClient(redis_config, storage_config)


@pytest.fixture
def sample_orderbook() -> OrderBookSnapshot:
    """Sample order book snapshot for testing."""
    now = datetime.now(timezone.utc)
    return OrderBookSnapshot(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=now,
        local_timestamp=now,
        sequence_id=12345678,
        bids=[
            PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.0")),
            PriceLevel(price=Decimal("49999.00"), quantity=Decimal("2.0")),
        ],
        asks=[
            PriceLevel(price=Decimal("50001.00"), quantity=Decimal("0.8")),
            PriceLevel(price=Decimal("50002.00"), quantity=Decimal("1.5")),
        ],
        depth_levels=20,
    )


@pytest.fixture
def sample_alert() -> Alert:
    """Sample alert for testing."""
    return Alert(
        alert_id="test-alert-123",
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
        triggered_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_health() -> HealthStatus:
    """Sample health status for testing."""
    return HealthStatus(
        exchange="binance",
        status=ConnectionStatus.CONNECTED,
        last_message_at=datetime.now(timezone.utc),
        message_count=12345,
        lag_ms=23,
        reconnect_count=0,
        gaps_last_hour=0,
    )


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


class TestRedisClientInit:
    """Tests for RedisClient initialization."""

    def test_init_with_config(
        self, redis_config: RedisConnectionConfig, storage_config: RedisStorageConfig
    ) -> None:
        """Test client initializes with provided configuration."""
        client = RedisClient(redis_config, storage_config)

        assert client.config == redis_config
        assert client.storage_config == storage_config
        assert not client.is_connected

    def test_init_with_default_storage_config(
        self, redis_config: RedisConnectionConfig
    ) -> None:
        """Test client uses default storage config when not provided."""
        client = RedisClient(redis_config)

        assert client.config == redis_config
        assert client.storage_config is not None
        assert isinstance(client.storage_config, RedisStorageConfig)

    def test_key_constants(self, redis_client: RedisClient) -> None:
        """Test key constants are properly defined."""
        assert redis_client.KEY_ORDERBOOK == "orderbook"
        assert redis_client.KEY_ZSCORE == "zscore"
        assert redis_client.KEY_ALERT == "alert"
        assert redis_client.KEY_ALERTS_ACTIVE == "alerts:active"
        assert redis_client.KEY_HEALTH == "health"

    def test_channel_constants(self, redis_client: RedisClient) -> None:
        """Test pub/sub channel constants are properly defined."""
        assert redis_client.CHANNEL_ORDERBOOK == "updates:orderbook"
        assert redis_client.CHANNEL_ALERTS == "updates:alerts"
        assert redis_client.CHANNEL_HEALTH == "updates:health"


# ============================================================================
# CONNECTION TESTS
# ============================================================================


class TestRedisClientConnection:
    """Tests for Redis connection management."""

    @pytest.mark.asyncio
    async def test_connect_success(self, redis_client: RedisClient) -> None:
        """Test successful connection to Redis."""
        mock_pool = MagicMock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with patch(
            "src.storage.redis_client.ConnectionPool.from_url", return_value=mock_pool
        ):
            with patch(
                "src.storage.redis_client.Redis", return_value=mock_redis
            ):
                await redis_client.connect()

                assert redis_client.is_connected
                mock_redis.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self, redis_client: RedisClient) -> None:
        """Test connecting when already connected logs warning."""
        redis_client._connected = True
        redis_client._client = AsyncMock()

        # Should not raise, just log warning
        await redis_client.connect()
        assert redis_client.is_connected

    @pytest.mark.asyncio
    async def test_connect_failure(self, redis_client: RedisClient) -> None:
        """Test connection failure raises exception."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        mock_pool = MagicMock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RedisConnectionError("Connection refused"))

        with patch(
            "src.storage.redis_client.ConnectionPool.from_url", return_value=mock_pool
        ):
            with patch(
                "src.storage.redis_client.Redis", return_value=mock_redis
            ):
                with pytest.raises(RedisConnectionException) as exc_info:
                    await redis_client.connect()

                assert "Failed to connect" in str(exc_info.value)
                assert not redis_client.is_connected

    @pytest.mark.asyncio
    async def test_disconnect(self, redis_client: RedisClient) -> None:
        """Test disconnection cleans up resources."""
        mock_client = AsyncMock()
        mock_pool = AsyncMock()

        redis_client._client = mock_client
        redis_client._pool = mock_pool
        redis_client._connected = True

        await redis_client.disconnect()

        assert not redis_client.is_connected
        assert redis_client._client is None
        assert redis_client._pool is None
        mock_client.aclose.assert_called_once()
        mock_pool.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_ping_success(self, redis_client: RedisClient) -> None:
        """Test ping returns True when connected."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.ping()

        assert result is True
        mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_ping_not_connected(self, redis_client: RedisClient) -> None:
        """Test ping returns False when not connected."""
        result = await redis_client.ping()
        assert result is False

    @pytest.mark.asyncio
    async def test_ping_error(self, redis_client: RedisClient) -> None:
        """Test ping returns False on error."""
        from redis.exceptions import RedisError

        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=RedisError("Connection lost"))

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.ping()

        assert result is False


# ============================================================================
# ORDER BOOK TESTS
# ============================================================================


class TestRedisClientOrderBook:
    """Tests for order book storage operations."""

    def test_orderbook_key_format(self, redis_client: RedisClient) -> None:
        """Test order book key generation."""
        key = redis_client._orderbook_key("binance", "BTC-USDT-PERP")
        assert key == "orderbook:binance:BTC-USDT-PERP"

    @pytest.mark.asyncio
    async def test_set_orderbook_not_connected(
        self, redis_client: RedisClient, sample_orderbook: OrderBookSnapshot
    ) -> None:
        """Test set_orderbook raises when not connected."""
        with pytest.raises(RedisConnectionException):
            await redis_client.set_orderbook(sample_orderbook)

    @pytest.mark.asyncio
    async def test_set_orderbook_success(
        self, redis_client: RedisClient, sample_orderbook: OrderBookSnapshot
    ) -> None:
        """Test successful order book storage."""
        mock_client = AsyncMock()
        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.set_orderbook(sample_orderbook)

        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        assert "orderbook:binance:BTC-USDT-PERP" in call_args[0]

    @pytest.mark.asyncio
    async def test_get_orderbook_not_found(self, redis_client: RedisClient) -> None:
        """Test get_orderbook returns None when not found."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_orderbook("binance", "BTC-USDT-PERP")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_orderbook_success(
        self, redis_client: RedisClient, sample_orderbook: OrderBookSnapshot
    ) -> None:
        """Test successful order book retrieval."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=sample_orderbook.model_dump_json())

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_orderbook("binance", "BTC-USDT-PERP")

        assert result is not None
        assert result.exchange == sample_orderbook.exchange
        assert result.instrument == sample_orderbook.instrument
        assert result.sequence_id == sample_orderbook.sequence_id


# ============================================================================
# Z-SCORE BUFFER TESTS
# ============================================================================


class TestRedisClientZScore:
    """Tests for z-score buffer operations."""

    def test_zscore_key_format(self, redis_client: RedisClient) -> None:
        """Test z-score key generation."""
        key = redis_client._zscore_key("binance", "BTC-USDT-PERP", "spread_bps")
        assert key == "zscore:binance:BTC-USDT-PERP:spread_bps"

    @pytest.mark.asyncio
    async def test_add_zscore_sample_success(self, redis_client: RedisClient) -> None:
        """Test adding a z-score sample."""
        mock_client = AsyncMock()
        mock_pipeline = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipeline)
        mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
        mock_pipeline.__aexit__ = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.add_zscore_sample(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
            value=Decimal("2.5"),
            window_size=300,
        )

        mock_pipeline.rpush.assert_called_once()
        mock_pipeline.ltrim.assert_called_once()
        mock_pipeline.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_zscore_buffer_success(self, redis_client: RedisClient) -> None:
        """Test retrieving z-score buffer."""
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=["1.5", "2.0", "2.5", "3.0"])

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        assert len(result) == 4
        assert result[0] == Decimal("1.5")
        assert result[-1] == Decimal("3.0")
        assert all(isinstance(v, Decimal) for v in result)

    @pytest.mark.asyncio
    async def test_get_zscore_buffer_with_limit(self, redis_client: RedisClient) -> None:
        """Test retrieving z-score buffer with limit."""
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=["2.5", "3.0"])

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
            limit=2,
        )

        assert len(result) == 2
        mock_client.lrange.assert_called_with(
            "zscore:binance:BTC-USDT-PERP:spread_bps", -2, -1
        )

    @pytest.mark.asyncio
    async def test_clear_zscore_buffer(self, redis_client: RedisClient) -> None:
        """Test clearing z-score buffer."""
        mock_client = AsyncMock()
        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.clear_zscore_buffer(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        mock_client.delete.assert_called_once_with(
            "zscore:binance:BTC-USDT-PERP:spread_bps"
        )

    @pytest.mark.asyncio
    async def test_get_zscore_buffer_length(self, redis_client: RedisClient) -> None:
        """Test getting z-score buffer length."""
        mock_client = AsyncMock()
        mock_client.llen = AsyncMock(return_value=30)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_zscore_buffer_length(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )

        assert result == 30


# ============================================================================
# ALERT TESTS
# ============================================================================


class TestRedisClientAlerts:
    """Tests for alert storage operations."""

    def test_alert_key_format(self, redis_client: RedisClient) -> None:
        """Test alert key generation."""
        key = redis_client._alert_key("test-alert-123")
        assert key == "alert:test-alert-123"

    def test_alerts_by_priority_key_format(self, redis_client: RedisClient) -> None:
        """Test alerts by priority key generation."""
        key = redis_client._alerts_by_priority_key(AlertPriority.P1)
        assert key == "alerts:by_priority:P1"

    def test_alerts_by_instrument_key_format(self, redis_client: RedisClient) -> None:
        """Test alerts by instrument key generation."""
        key = redis_client._alerts_by_instrument_key("BTC-USDT-PERP")
        assert key == "alerts:by_instrument:BTC-USDT-PERP"

    @pytest.mark.asyncio
    async def test_set_alert_success(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test storing an alert."""
        mock_client = AsyncMock()
        mock_pipeline = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipeline)
        mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
        mock_pipeline.__aexit__ = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.set_alert(sample_alert)

        # Verify alert was stored
        mock_pipeline.set.assert_called_once()
        # Verify added to active set
        mock_pipeline.sadd.assert_called()

    @pytest.mark.asyncio
    async def test_get_alert_success(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test retrieving an alert."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=sample_alert.model_dump_json())

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_alert(sample_alert.alert_id)

        assert result is not None
        assert result.alert_id == sample_alert.alert_id
        assert result.alert_type == sample_alert.alert_type

    @pytest.mark.asyncio
    async def test_get_alert_not_found(self, redis_client: RedisClient) -> None:
        """Test get_alert returns None when not found."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_alert("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_active_alerts_success(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test retrieving active alerts."""
        mock_client = AsyncMock()
        mock_client.smembers = AsyncMock(return_value={sample_alert.alert_id})
        mock_client.mget = AsyncMock(return_value=[sample_alert.model_dump_json()])

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_active_alerts()

        assert len(result) == 1
        assert result[0].alert_id == sample_alert.alert_id

    @pytest.mark.asyncio
    async def test_get_active_alerts_empty(self, redis_client: RedisClient) -> None:
        """Test get_active_alerts returns empty list when no alerts."""
        mock_client = AsyncMock()
        mock_client.smembers = AsyncMock(return_value=set())

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_active_alerts()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_alerts_by_priority(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test retrieving alerts by priority."""
        mock_client = AsyncMock()
        mock_client.sinter = AsyncMock(return_value={sample_alert.alert_id})
        mock_client.mget = AsyncMock(return_value=[sample_alert.model_dump_json()])

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_alerts_by_priority(AlertPriority.P2)

        assert len(result) == 1
        assert result[0].priority == AlertPriority.P2

    @pytest.mark.asyncio
    async def test_get_alerts_by_instrument(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test retrieving alerts by instrument."""
        mock_client = AsyncMock()
        mock_client.sinter = AsyncMock(return_value={sample_alert.alert_id})
        mock_client.mget = AsyncMock(return_value=[sample_alert.model_dump_json()])

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_alerts_by_instrument("BTC-USDT-PERP")

        assert len(result) == 1
        assert result[0].instrument == "BTC-USDT-PERP"

    @pytest.mark.asyncio
    async def test_remove_alert(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test removing an alert."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=sample_alert.model_dump_json())
        mock_pipeline = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipeline)
        mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
        mock_pipeline.__aexit__ = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.remove_alert(sample_alert.alert_id)

        mock_pipeline.delete.assert_called_once()
        mock_pipeline.srem.assert_called()


# ============================================================================
# HEALTH STATUS TESTS
# ============================================================================


class TestRedisClientHealth:
    """Tests for health status operations."""

    def test_health_key_format(self, redis_client: RedisClient) -> None:
        """Test health key generation."""
        key = redis_client._health_key("binance")
        assert key == "health:binance"

    @pytest.mark.asyncio
    async def test_set_health_success(
        self, redis_client: RedisClient, sample_health: HealthStatus
    ) -> None:
        """Test storing health status."""
        mock_client = AsyncMock()
        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.set_health(sample_health)

        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        assert "health:binance" in call_args[0]

    @pytest.mark.asyncio
    async def test_get_health_success(
        self, redis_client: RedisClient, sample_health: HealthStatus
    ) -> None:
        """Test retrieving health status."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=sample_health.model_dump_json())

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_health("binance")

        assert result is not None
        assert result.exchange == "binance"
        assert result.status == ConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_get_health_not_found(self, redis_client: RedisClient) -> None:
        """Test get_health returns None when not found."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_health("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_health_success(
        self, redis_client: RedisClient, sample_health: HealthStatus
    ) -> None:
        """Test retrieving all health statuses."""
        mock_client = AsyncMock()

        # Mock scan_iter to return keys
        async def mock_scan_iter(**kwargs: Any) -> Any:
            yield "health:binance"
            yield "health:okx"

        mock_client.scan_iter = mock_scan_iter

        # Create OKX health
        okx_health = HealthStatus(
            exchange="okx",
            status=ConnectionStatus.CONNECTED,
            last_message_at=datetime.now(timezone.utc),
            message_count=5000,
            lag_ms=15,
        )

        mock_client.mget = AsyncMock(
            return_value=[
                sample_health.model_dump_json(),
                okx_health.model_dump_json(),
            ]
        )

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_all_health()

        assert len(result) == 2
        assert "binance" in result
        assert "okx" in result


# ============================================================================
# PUB/SUB TESTS
# ============================================================================


class TestRedisClientPubSub:
    """Tests for pub/sub operations."""

    @pytest.mark.asyncio
    async def test_publish_orderbook_update(
        self, redis_client: RedisClient, sample_orderbook: OrderBookSnapshot
    ) -> None:
        """Test publishing order book update."""
        mock_client = AsyncMock()
        mock_client.publish = AsyncMock(return_value=3)

        redis_client._client = mock_client
        redis_client._connected = True

        count = await redis_client.publish_orderbook_update(sample_orderbook)

        assert count == 3
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "updates:orderbook"

    @pytest.mark.asyncio
    async def test_publish_alert(
        self, redis_client: RedisClient, sample_alert: Alert
    ) -> None:
        """Test publishing alert."""
        mock_client = AsyncMock()
        mock_client.publish = AsyncMock(return_value=2)

        redis_client._client = mock_client
        redis_client._connected = True

        count = await redis_client.publish_alert(sample_alert)

        assert count == 2
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "updates:alerts"

    @pytest.mark.asyncio
    async def test_publish_health_update(
        self, redis_client: RedisClient, sample_health: HealthStatus
    ) -> None:
        """Test publishing health update."""
        mock_client = AsyncMock()
        mock_client.publish = AsyncMock(return_value=1)

        redis_client._client = mock_client
        redis_client._connected = True

        count = await redis_client.publish_health_update(sample_health)

        assert count == 1
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "updates:health"


# ============================================================================
# UTILITY TESTS
# ============================================================================


class TestRedisClientUtility:
    """Tests for utility methods."""

    @pytest.mark.asyncio
    async def test_flush_db(self, redis_client: RedisClient) -> None:
        """Test flushing database."""
        mock_client = AsyncMock()
        redis_client._client = mock_client
        redis_client._connected = True

        await redis_client.flush_db()

        mock_client.flushdb.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_info(self, redis_client: RedisClient) -> None:
        """Test getting Redis info."""
        mock_info = {
            "redis_version": "7.0.0",
            "connected_clients": 5,
            "used_memory": "1000000",
        }
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(return_value=mock_info)

        redis_client._client = mock_client
        redis_client._connected = True

        result = await redis_client.get_info()

        assert result["redis_version"] == "7.0.0"
        assert result["connected_clients"] == 5


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestRedisClientErrors:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_operation_without_connection(
        self, redis_client: RedisClient
    ) -> None:
        """Test operations raise when not connected."""
        with pytest.raises(RedisConnectionException):
            await redis_client.get_orderbook("binance", "BTC-USDT-PERP")

    @pytest.mark.asyncio
    async def test_redis_error_handling(
        self, redis_client: RedisClient, sample_orderbook: OrderBookSnapshot
    ) -> None:
        """Test Redis errors are wrapped properly."""
        from redis.exceptions import RedisError

        mock_client = AsyncMock()
        mock_client.setex = AsyncMock(side_effect=RedisError("Connection lost"))

        redis_client._client = mock_client
        redis_client._connected = True

        with pytest.raises(RedisOperationError) as exc_info:
            await redis_client.set_orderbook(sample_orderbook)

        assert "Failed to store order book" in str(exc_info.value)

    def test_exception_hierarchy(self) -> None:
        """Test exception inheritance."""
        assert issubclass(RedisConnectionException, RedisClientError)
        assert issubclass(RedisOperationError, RedisClientError)
        assert issubclass(RedisClientError, Exception)
