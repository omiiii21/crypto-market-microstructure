"""
Unit tests for Binance adapter.

Tests all components:
    - BinanceNormalizer: Data normalization
    - BinanceWebSocketClient: Connection management
    - BinanceRestClient: REST API fallback
    - BinanceAdapter: Complete adapter logic

Uses mocked connections to avoid actual network calls.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.adapters.binance.adapter import BinanceAdapter
from src.adapters.binance.normalizer import BinanceNormalizer
from src.adapters.binance.rest import BinanceRestClient
from src.adapters.binance.websocket import BinanceWebSocketClient
from src.config.models import (
    ConnectionSettings,
    ExchangeConfig,
    ExchangeSymbolConfig,
    InstrumentConfig,
    InstrumentType,
    RestEndpoints,
    StreamSettings,
    WebSocketEndpoints,
)
from src.models.health import ConnectionStatus, GapMarker
from src.models.orderbook import OrderBookSnapshot, PriceLevel


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def binance_orderbook_message():
    """Sample Binance order book message."""
    return {
        "e": "depthUpdate",
        "E": 1234567890123,
        "s": "BTCUSDT",
        "U": 157,
        "u": 160,
        "b": [
            ["50000.00", "1.5"],
            ["49999.00", "2.0"],
            ["49998.00", "1.0"],
        ],
        "a": [
            ["50001.00", "1.2"],
            ["50002.00", "0.8"],
            ["50003.00", "2.5"],
        ],
    }


@pytest.fixture
def binance_ticker_message():
    """Sample Binance 24hr ticker message."""
    return {
        "e": "24hrTicker",
        "E": 1234567890123,
        "s": "BTCUSDT",
        "c": "50000.00",
        "h": "51000.00",
        "l": "49000.00",
        "v": "1000.5",
        "q": "50025000.00",
    }


@pytest.fixture
def binance_mark_price_message():
    """Sample Binance mark price message."""
    return {
        "e": "markPriceUpdate",
        "E": 1234567890123,
        "s": "BTCUSDT",
        "p": "50001.50",
        "i": "49999.00",
        "r": "0.0001",
        "T": 1234567900000,
    }


@pytest.fixture
def exchange_config():
    """Sample exchange configuration."""
    return ExchangeConfig(
        enabled=True,
        websocket=WebSocketEndpoints(
            futures="wss://fstream.binance.com/stream",
            spot="wss://stream.binance.com:9443/ws",
        ),
        rest=RestEndpoints(
            futures="https://fapi.binance.com",
            spot="https://api.binance.com",
        ),
        connection=ConnectionSettings(
            rate_limit_per_second=10,
            reconnect_delay_seconds=5,
            max_reconnect_attempts=10,
            ping_interval_seconds=30,
            ping_timeout_seconds=10,
        ),
        streams=StreamSettings(
            orderbook_depth=20,
            orderbook_speed="100ms",
        ),
    )


@pytest.fixture
def instrument_configs():
    """Sample instrument configurations."""
    return [
        InstrumentConfig(
            id="BTC-USDT-PERP",
            name="BTC/USDT Perpetual",
            type=InstrumentType.PERPETUAL,
            base="BTC",
            quote="USDT",
            enabled=True,
            exchange_symbols={
                "binance": ExchangeSymbolConfig(
                    symbol="BTCUSDT",
                    stream="btcusdt@depth20@100ms",
                    ticker_stream="btcusdt@ticker",
                    mark_price_stream="btcusdt@markPrice",
                )
            },
            depth_levels=20,
        ),
        InstrumentConfig(
            id="BTC-USDT-SPOT",
            name="BTC/USDT Spot",
            type=InstrumentType.SPOT,
            base="BTC",
            quote="USDT",
            enabled=True,
            exchange_symbols={
                "binance": ExchangeSymbolConfig(
                    symbol="BTCUSDT",
                    stream="btcusdt@depth20@100ms",
                    ticker_stream="btcusdt@ticker",
                )
            },
            depth_levels=20,
        ),
    ]


# =============================================================================
# NORMALIZER TESTS
# =============================================================================


def test_normalize_orderbook_valid(binance_orderbook_message):
    """Test order book normalization with valid data."""
    snapshot = BinanceNormalizer.normalize_orderbook(
        raw_message=binance_orderbook_message,
        instrument="BTC-USDT-PERP",
        instrument_type="perpetual",
    )

    assert snapshot.exchange == "binance"
    assert snapshot.instrument == "BTC-USDT-PERP"
    assert snapshot.sequence_id == 160
    assert len(snapshot.bids) == 3
    assert len(snapshot.asks) == 3

    # Check best bid/ask
    assert snapshot.best_bid == Decimal("50000.00")
    assert snapshot.best_ask == Decimal("50001.00")

    # Check sorting
    assert snapshot.bids[0].price > snapshot.bids[1].price  # Descending
    assert snapshot.asks[0].price < snapshot.asks[1].price  # Ascending

    # Check spread
    assert snapshot.spread == Decimal("1.00")


def test_normalize_orderbook_missing_field():
    """Test normalization fails gracefully with missing field."""
    invalid_message = {"e": "depthUpdate", "E": 123}

    with pytest.raises(ValueError, match="Missing required field"):
        BinanceNormalizer.normalize_orderbook(
            raw_message=invalid_message,
            instrument="BTC-USDT-PERP",
            instrument_type="perpetual",
        )


def test_normalize_orderbook_zero_quantities(binance_orderbook_message):
    """Test that zero quantity levels are filtered out."""
    binance_orderbook_message["b"].append(["49997.00", "0.0"])
    binance_orderbook_message["a"].append(["50004.00", "0.0"])

    snapshot = BinanceNormalizer.normalize_orderbook(
        raw_message=binance_orderbook_message,
        instrument="BTC-USDT-PERP",
        instrument_type="perpetual",
    )

    # Zero quantity levels should be filtered
    assert all(level.quantity > 0 for level in snapshot.bids)
    assert all(level.quantity > 0 for level in snapshot.asks)


def test_normalize_ticker_with_mark_price(
    binance_ticker_message, binance_mark_price_message
):
    """Test ticker normalization with mark price data."""
    ticker = BinanceNormalizer.normalize_ticker(
        raw_24hr_ticker=binance_ticker_message,
        raw_mark_price=binance_mark_price_message,
        instrument="BTC-USDT-PERP",
    )

    assert ticker.exchange == "binance"
    assert ticker.instrument == "BTC-USDT-PERP"
    assert ticker.last_price == Decimal("50000.00")
    assert ticker.mark_price == Decimal("50001.50")
    assert ticker.index_price == Decimal("49999.00")
    assert ticker.funding_rate == Decimal("0.0001")
    assert ticker.is_perpetual


def test_normalize_ticker_spot_only(binance_ticker_message):
    """Test ticker normalization for spot (no mark price)."""
    ticker = BinanceNormalizer.normalize_ticker(
        raw_24hr_ticker=binance_ticker_message,
        raw_mark_price=None,
        instrument="BTC-USDT-SPOT",
    )

    assert ticker.exchange == "binance"
    assert ticker.instrument == "BTC-USDT-SPOT"
    assert ticker.last_price == Decimal("50000.00")
    assert ticker.mark_price is None
    assert ticker.index_price is None
    assert ticker.funding_rate is None
    assert not ticker.is_perpetual


# =============================================================================
# WEBSOCKET CLIENT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_websocket_connect():
    """Test WebSocket connection."""
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws

        client = BinanceWebSocketClient(
            url="wss://fstream.binance.com/stream",
            ping_interval=30,
            max_reconnect_attempts=10,
        )

        await client.connect()

        assert client.is_connected
        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_disconnect():
    """Test WebSocket disconnection."""
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws

        client = BinanceWebSocketClient(url="wss://fstream.binance.com/stream")
        await client.connect()
        await client.disconnect()

        assert not client.is_connected
        mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_stream_messages():
    """Test streaming messages from WebSocket."""
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Mock receiving messages
        messages = [
            '{"e":"depthUpdate","u":100}',
            '{"e":"depthUpdate","u":101}',
        ]
        mock_ws.recv.side_effect = messages + [asyncio.CancelledError()]
        mock_connect.return_value = mock_ws

        client = BinanceWebSocketClient(url="wss://fstream.binance.com/stream")
        await client.connect()

        received = []
        try:
            async for message in client.stream_messages():
                received.append(message)
                if len(received) >= 2:
                    break
        except asyncio.CancelledError:
            pass

        assert len(received) == 2
        assert received[0]["u"] == 100
        assert received[1]["u"] == 101


# =============================================================================
# REST CLIENT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_rest_get_orderbook():
    """Test fetching order book via REST."""
    mock_response = {
        "lastUpdateId": 12345,
        "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
        "asks": [["50001.00", "1.2"], ["50002.00", "0.8"]],
    }

    with patch("aiohttp.ClientSession.request") as mock_request:
        mock_request.return_value.__aenter__.return_value.status = 200
        mock_request.return_value.__aenter__.return_value.json = AsyncMock(
            return_value=mock_response
        )

        client = BinanceRestClient(
            base_url="https://fapi.binance.com", rate_limit_per_second=10
        )

        snapshot = await client.get_orderbook(
            symbol="BTCUSDT", limit=20, instrument="BTC-USDT-PERP"
        )

        assert snapshot.exchange == "binance"
        assert snapshot.instrument == "BTC-USDT-PERP"
        assert snapshot.sequence_id == 12345
        assert len(snapshot.bids) == 2
        assert len(snapshot.asks) == 2
        assert snapshot.best_bid == Decimal("50000.00")
        assert snapshot.best_ask == Decimal("50001.00")

        await client.close()


@pytest.mark.asyncio
async def test_rest_rate_limiting():
    """Test rate limiting enforcement."""
    client = BinanceRestClient(
        base_url="https://fapi.binance.com", rate_limit_per_second=10
    )

    # Mock the request to succeed immediately
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "lastUpdateId": 123,
            "bids": [],
            "asks": [],
        }

        # Make multiple requests
        start = asyncio.get_event_loop().time()
        for _ in range(3):
            await client.get_orderbook("BTCUSDT", instrument="BTC-USDT-PERP")
        end = asyncio.get_event_loop().time()

        # Should take at least 0.2 seconds (3 requests * 0.1s interval)
        duration = end - start
        assert duration >= 0.15  # Allow some tolerance

    await client.close()


# =============================================================================
# ADAPTER TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_adapter_connect(exchange_config, instrument_configs):
    """Test adapter connection."""
    with patch(
        "src.adapters.binance.adapter.BinanceWebSocketClient"
    ) as mock_ws_class:
        mock_ws = AsyncMock()
        mock_ws.is_connected = True
        mock_ws_class.return_value = mock_ws

        adapter = BinanceAdapter(exchange_config, instrument_configs)
        await adapter.connect()

        assert adapter.is_connected
        assert adapter.exchange_name == "binance"


@pytest.mark.asyncio
async def test_adapter_gap_detection(exchange_config, instrument_configs):
    """Test sequence gap detection."""
    adapter = BinanceAdapter(exchange_config, instrument_configs)

    # No gap - first message
    gap = adapter.detect_gap(prev_seq=None, curr_seq=100)
    assert gap is None

    # No gap - sequential
    gap = adapter.detect_gap(prev_seq=100, curr_seq=101)
    assert gap is None

    # Gap detected
    gap = adapter.detect_gap(prev_seq=100, curr_seq=105)
    assert gap is not None
    assert gap.sequence_id_before == 100
    assert gap.sequence_id_after == 105
    assert gap.sequence_gap_size == 4


@pytest.mark.asyncio
async def test_adapter_health_check(exchange_config, instrument_configs):
    """Test health check."""
    with patch(
        "src.adapters.binance.adapter.BinanceWebSocketClient"
    ) as mock_ws_class:
        mock_ws = AsyncMock()
        mock_ws.is_connected = True
        mock_ws.last_message_at = datetime.now(timezone.utc)
        mock_ws.reconnect_count = 0
        mock_ws_class.return_value = mock_ws

        adapter = BinanceAdapter(exchange_config, instrument_configs)
        await adapter.connect()

        health = await adapter.health_check()

        assert health.exchange == "binance"
        assert health.status == ConnectionStatus.CONNECTED
        assert health.message_count >= 0
        assert health.reconnect_count == 0


@pytest.mark.asyncio
async def test_adapter_get_orderbook_rest(exchange_config, instrument_configs):
    """Test fetching order book via REST through adapter."""
    with patch(
        "src.adapters.binance.adapter.BinanceRestClient"
    ) as mock_rest_class:
        mock_rest = AsyncMock()
        mock_snapshot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime.now(timezone.utc),
            local_timestamp=datetime.now(timezone.utc),
            sequence_id=12345,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
            asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("0.5"))],
        )
        mock_rest.get_orderbook.return_value = mock_snapshot
        mock_rest_class.return_value = mock_rest

        adapter = BinanceAdapter(exchange_config, instrument_configs)
        await adapter.connect()

        snapshot = await adapter.get_order_book_rest("BTC-USDT-PERP")

        assert snapshot.exchange == "binance"
        assert snapshot.instrument == "BTC-USDT-PERP"
        assert snapshot.sequence_id == 12345


@pytest.mark.asyncio
async def test_adapter_subscribe(exchange_config, instrument_configs):
    """Test subscribing to instruments."""
    with patch(
        "src.adapters.binance.adapter.BinanceWebSocketClient"
    ) as mock_ws_class:
        mock_ws = AsyncMock()
        mock_ws.is_connected = True
        mock_ws.url = "wss://fstream.binance.com/stream"
        mock_ws_class.return_value = mock_ws

        adapter = BinanceAdapter(exchange_config, instrument_configs)
        await adapter.connect()
        await adapter.subscribe(["BTC-USDT-PERP"])

        # Should have reconnected with combined stream URL
        assert "btcusdt@depth20@100ms" in mock_ws.url.lower()


# =============================================================================
# EDGE CASE TESTS
# =============================================================================


def test_normalize_orderbook_crossed_book():
    """Test that crossed book raises validation error."""
    crossed_message = {
        "e": "depthUpdate",
        "E": 1234567890123,
        "s": "BTCUSDT",
        "u": 100,
        "b": [["50002.00", "1.0"]],  # Bid higher than ask
        "a": [["50001.00", "1.0"]],
    }

    with pytest.raises(ValueError, match="Crossed order book"):
        BinanceNormalizer.normalize_orderbook(
            raw_message=crossed_message,
            instrument="BTC-USDT-PERP",
            instrument_type="perpetual",
        )


def test_normalize_orderbook_empty_sides():
    """Test normalization with empty bids or asks."""
    empty_message = {
        "e": "depthUpdate",
        "E": 1234567890123,
        "s": "BTCUSDT",
        "u": 100,
        "b": [],
        "a": [["50001.00", "1.0"]],
    }

    snapshot = BinanceNormalizer.normalize_orderbook(
        raw_message=empty_message,
        instrument="BTC-USDT-PERP",
        instrument_type="perpetual",
    )

    assert len(snapshot.bids) == 0
    assert len(snapshot.asks) == 1
    assert snapshot.best_bid is None
    assert snapshot.best_ask == Decimal("50001.00")


@pytest.mark.asyncio
async def test_adapter_invalid_instrument(exchange_config, instrument_configs):
    """Test that requesting unknown instrument raises error."""
    adapter = BinanceAdapter(exchange_config, instrument_configs)

    with pytest.raises(ValueError, match="Instrument not found"):
        await adapter.get_order_book_rest("ETH-USDT-PERP")


# =============================================================================
# INTEGRATION TEST
# =============================================================================


@pytest.mark.asyncio
async def test_full_pipeline_integration(
    exchange_config, instrument_configs, binance_orderbook_message
):
    """Test full pipeline from WebSocket message to OrderBookSnapshot."""
    with patch(
        "src.adapters.binance.adapter.BinanceWebSocketClient"
    ) as mock_ws_class:
        # Setup mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.is_connected = True
        mock_ws.last_message_at = datetime.now(timezone.utc)
        mock_ws.reconnect_count = 0

        # Mock streaming messages
        async def mock_stream():
            yield binance_orderbook_message
            yield binance_orderbook_message

        mock_ws.stream_messages.return_value = mock_stream()
        mock_ws_class.return_value = mock_ws

        # Create adapter
        adapter = BinanceAdapter(exchange_config, instrument_configs)
        await adapter.connect()
        await adapter.subscribe(["BTC-USDT-PERP"])

        # Stream order books
        count = 0
        async for snapshot in adapter.stream_order_books():
            assert snapshot.exchange == "binance"
            assert snapshot.instrument == "BTC-USDT-PERP"
            assert snapshot.best_bid == Decimal("50000.00")
            count += 1
            if count >= 2:
                break

        assert count == 2
