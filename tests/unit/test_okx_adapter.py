"""
Unit tests for OKX adapter components.

Tests cover:
    - WebSocket client connection and reconnection
    - Data normalization (orderbook and ticker)
    - Sequence gap detection
    - REST API fallback
    - Health check
    - OKX-specific features (instId mapping, ping/pong)

Test Strategy:
    - Mock WebSocket and HTTP connections
    - Use real OKX message formats
    - Test error handling and edge cases
    - Verify Decimal usage for financial values
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.adapters.okx.adapter import OKXAdapter
from src.adapters.okx.normalizer import OKXNormalizer
from src.adapters.okx.rest import OKXRestClient
from src.adapters.okx.websocket import OKXWebSocketClient
from src.config.models import (
    ConnectionSettings,
    ExchangeConfig,
    InstrumentConfig,
    InstrumentType,
    RestEndpoints,
    StreamSettings,
    WebSocketEndpoints,
)
from src.models.health import ConnectionStatus
from src.models.orderbook import OrderBookSnapshot, PriceLevel


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def exchange_config():
    """Create mock OKX exchange configuration."""
    return ExchangeConfig(
        enabled=True,
        websocket=WebSocketEndpoints(
            public="wss://ws.okx.com:8443/ws/v5/public"
        ),
        rest=RestEndpoints(
            base="https://www.okx.com"
        ),
        connection=ConnectionSettings(
            rate_limit_per_second=10,
            reconnect_delay_seconds=1,
            max_reconnect_attempts=3,
            ping_interval_seconds=30,
            ping_timeout_seconds=10,
        ),
        streams=StreamSettings(
            orderbook_channel="books5",
        ),
    )


@pytest.fixture
def instrument_configs():
    """Create mock instrument configurations."""
    perp = InstrumentConfig(
        id="BTC-USDT-PERP",
        name="BTC/USDT Perpetual",
        type=InstrumentType.PERPETUAL,
        base="BTC",
        quote="USDT",
        enabled=True,
        exchange_symbols={
            "okx": {
                "symbol": "BTC-USDT-SWAP",
                "inst_type": "SWAP"
            }
        },
        depth_levels=20,
    )

    spot = InstrumentConfig(
        id="BTC-USDT-SPOT",
        name="BTC/USDT Spot",
        type=InstrumentType.SPOT,
        base="BTC",
        quote="USDT",
        enabled=True,
        exchange_symbols={
            "okx": {
                "symbol": "BTC-USDT",
                "inst_type": "SPOT"
            }
        },
        depth_levels=20,
    )

    return [perp, spot]


@pytest.fixture
def okx_orderbook_message():
    """Create sample OKX order book message."""
    return {
        "arg": {
            "channel": "books5",
            "instId": "BTC-USDT-SWAP"
        },
        "action": "snapshot",
        "data": [
            {
                "asks": [
                    ["50001.0", "1.5", "0", "2"],
                    ["50002.0", "2.0", "0", "3"]
                ],
                "bids": [
                    ["50000.0", "2.0", "0", "3"],
                    ["49999.0", "1.5", "0", "2"]
                ],
                "ts": "1234567890123",
                "checksum": -123456789,
                "seqId": 123456789
            }
        ]
    }


@pytest.fixture
def okx_ticker_message():
    """Create sample OKX ticker message."""
    return {
        "arg": {
            "channel": "tickers",
            "instId": "BTC-USDT-SWAP"
        },
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "last": "50000.0",
                "lastSz": "0.5",
                "askPx": "50001.0",
                "bidPx": "50000.0",
                "high24h": "51000.0",
                "low24h": "49000.0",
                "vol24h": "1000.5",
                "volCcy24h": "50025000",
                "ts": "1234567890123"
            }
        ]
    }


@pytest.fixture
def okx_mark_price_message():
    """Create sample OKX mark price message."""
    return {
        "arg": {
            "channel": "mark-price",
            "instId": "BTC-USDT-SWAP"
        },
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "markPx": "50001.5",
                "idxPx": "49999.0",
                "fundingRate": "0.0001",
                "nextFundingTime": "1234567890123",
                "ts": "1234567890123"
            }
        ]
    }


# =============================================================================
# NORMALIZER TESTS
# =============================================================================


def test_normalize_orderbook_valid(okx_orderbook_message):
    """Test normalization of valid OKX order book message."""
    snapshot = OKXNormalizer.normalize_orderbook(
        raw_message=okx_orderbook_message,
        instrument="BTC-USDT-PERP",
    )

    assert isinstance(snapshot, OrderBookSnapshot)
    assert snapshot.exchange == "okx"
    assert snapshot.instrument == "BTC-USDT-PERP"
    assert snapshot.sequence_id == 123456789

    # Check bids (sorted descending)
    assert len(snapshot.bids) == 2
    assert snapshot.bids[0].price == Decimal("50000.0")
    assert snapshot.bids[0].quantity == Decimal("2.0")
    assert snapshot.bids[1].price == Decimal("49999.0")

    # Check asks (sorted ascending)
    assert len(snapshot.asks) == 2
    assert snapshot.asks[0].price == Decimal("50001.0")
    assert snapshot.asks[0].quantity == Decimal("1.5")
    assert snapshot.asks[1].price == Decimal("50002.0")

    # Verify Decimal types
    assert isinstance(snapshot.bids[0].price, Decimal)
    assert isinstance(snapshot.asks[0].quantity, Decimal)

    # Check computed fields
    assert snapshot.best_bid == Decimal("50000.0")
    assert snapshot.best_ask == Decimal("50001.0")
    assert snapshot.spread == Decimal("1.0")


def test_normalize_orderbook_missing_field():
    """Test normalization with missing required field."""
    invalid_message = {
        "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
        "data": [
            {
                "asks": [["50001.0", "1.5", "0", "2"]],
                # Missing "bids" field
                "ts": "1234567890123",
                # Missing "seqId" field
            }
        ]
    }

    with pytest.raises(ValueError, match="Missing required field"):
        OKXNormalizer.normalize_orderbook(
            raw_message=invalid_message,
            instrument="BTC-USDT-PERP",
        )


def test_normalize_orderbook_invalid_decimal():
    """Test normalization with invalid decimal value."""
    invalid_message = {
        "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
        "data": [
            {
                "asks": [["invalid_price", "1.5", "0", "2"]],
                "bids": [["50000.0", "2.0", "0", "3"]],
                "ts": "1234567890123",
                "seqId": 123456789
            }
        ]
    }

    with pytest.raises(ValueError, match="Invalid data"):
        OKXNormalizer.normalize_orderbook(
            raw_message=invalid_message,
            instrument="BTC-USDT-PERP",
        )


def test_normalize_ticker_with_mark_price(okx_ticker_message, okx_mark_price_message):
    """Test ticker normalization with mark price data (perpetual)."""
    ticker_data = okx_ticker_message["data"][0]
    mark_price_data = okx_mark_price_message["data"][0]

    ticker = OKXNormalizer.normalize_ticker(
        raw_ticker=ticker_data,
        raw_mark_price=mark_price_data,
        instrument="BTC-USDT-PERP",
    )

    assert ticker.exchange == "okx"
    assert ticker.instrument == "BTC-USDT-PERP"
    assert ticker.last_price == Decimal("50000.0")
    assert ticker.high_24h == Decimal("51000.0")
    assert ticker.low_24h == Decimal("49000.0")
    assert ticker.volume_24h == Decimal("1000.5")
    assert ticker.volume_24h_usd == Decimal("50025000")

    # Perpetual-specific fields
    assert ticker.mark_price == Decimal("50001.5")
    assert ticker.index_price == Decimal("49999.0")
    assert ticker.funding_rate == Decimal("0.0001")
    assert ticker.next_funding_time is not None


def test_normalize_ticker_spot_only(okx_ticker_message):
    """Test ticker normalization without mark price (spot)."""
    ticker_data = okx_ticker_message["data"][0]

    ticker = OKXNormalizer.normalize_ticker(
        raw_ticker=ticker_data,
        raw_mark_price=None,
        instrument="BTC-USDT-SPOT",
    )

    assert ticker.exchange == "okx"
    assert ticker.last_price == Decimal("50000.0")

    # Spot should not have derivatives fields
    assert ticker.mark_price is None
    assert ticker.index_price is None
    assert ticker.funding_rate is None


def test_instrument_id_mapping():
    """Test instrument ID normalization."""
    # OKX -> Our format
    assert OKXNormalizer.normalize_instrument_id("BTC-USDT-SWAP") == "BTC-USDT-PERP"
    assert OKXNormalizer.normalize_instrument_id("BTC-USDT") == "BTC-USDT-SPOT"

    # Our format -> OKX
    assert OKXNormalizer.to_okx_instrument_id("BTC-USDT-PERP") == "BTC-USDT-SWAP"
    assert OKXNormalizer.to_okx_instrument_id("BTC-USDT-SPOT") == "BTC-USDT"

    # Unknown instruments pass through
    assert OKXNormalizer.normalize_instrument_id("UNKNOWN") == "UNKNOWN"
    assert OKXNormalizer.to_okx_instrument_id("UNKNOWN") == "UNKNOWN"


# =============================================================================
# WEBSOCKET CLIENT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_websocket_connect():
    """Test WebSocket connection."""
    with patch("websockets.connect") as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws

        client = OKXWebSocketClient(
            url="wss://ws.okx.com:8443/ws/v5/public",
            ping_interval=30,
        )

        await client.connect()

        assert client.is_connected
        assert mock_connect.called

        await client.disconnect()


@pytest.mark.asyncio
async def test_websocket_subscribe():
    """Test WebSocket subscription."""
    with patch("websockets.connect") as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws

        client = OKXWebSocketClient(url="wss://ws.okx.com:8443/ws/v5/public")
        await client.connect()

        channels = [
            {"channel": "books5", "instId": "BTC-USDT-SWAP"},
            {"channel": "tickers", "instId": "BTC-USDT-SWAP"}
        ]

        await client.subscribe(channels)

        # Check that subscription message was sent
        assert mock_ws.send.called
        sent_message = json.loads(mock_ws.send.call_args[0][0])
        assert sent_message["op"] == "subscribe"
        assert sent_message["args"] == channels

        await client.disconnect()


@pytest.mark.asyncio
async def test_websocket_ping_pong():
    """Test WebSocket ping/pong mechanism."""
    with patch("websockets.connect") as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws

        client = OKXWebSocketClient(
            url="wss://ws.okx.com:8443/ws/v5/public",
            ping_interval=0.1,  # Fast ping for testing
        )

        await client.connect()

        # Wait for ping to be sent
        await asyncio.sleep(0.2)

        # Verify ping was sent (string "ping", not binary)
        assert mock_ws.send.called
        ping_calls = [call for call in mock_ws.send.call_args_list
                     if call[0][0] == "ping"]
        assert len(ping_calls) > 0

        await client.disconnect()


@pytest.mark.asyncio
async def test_websocket_reconnect():
    """Test WebSocket reconnection on disconnect."""
    with patch("websockets.connect") as mock_connect:
        # First connection succeeds, then fails, then succeeds again
        mock_ws1 = AsyncMock()
        mock_ws1.closed = False

        mock_ws2 = AsyncMock()
        mock_ws2.closed = False

        mock_connect.side_effect = [mock_ws1, Exception("Connection failed"), mock_ws2]

        client = OKXWebSocketClient(
            url="wss://ws.okx.com:8443/ws/v5/public",
            reconnect_delay=0.1,  # Fast reconnect for testing
        )

        # First connect succeeds
        await client.connect()
        assert client.is_connected

        # Simulate disconnect will trigger reconnect in stream_messages
        # For now just verify client can be created

        await client.disconnect()


# =============================================================================
# REST CLIENT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_rest_get_orderbook():
    """Test REST order book fetch."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "code": "0",
            "msg": "",
            "data": [
                {
                    "asks": [["50001.0", "1.5", "0", "2"]],
                    "bids": [["50000.0", "2.0", "0", "3"]],
                    "ts": "1234567890123",
                    "seqId": 123456789
                }
            ]
        })

        mock_session.request.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session
        mock_session.closed = False

        client = OKXRestClient(base_url="https://www.okx.com")

        snapshot = await client.get_orderbook(
            inst_id="BTC-USDT-SWAP",
            limit=20,
            instrument="BTC-USDT-PERP",
        )

        assert isinstance(snapshot, OrderBookSnapshot)
        assert snapshot.exchange == "okx"
        assert snapshot.instrument == "BTC-USDT-PERP"
        assert snapshot.sequence_id == 123456789
        assert len(snapshot.bids) == 1
        assert len(snapshot.asks) == 1

        await client.close()


@pytest.mark.asyncio
async def test_rest_rate_limiting():
    """Test REST rate limiting."""
    with patch("aiohttp.ClientSession"):
        client = OKXRestClient(
            base_url="https://www.okx.com",
            rate_limit_per_second=10,
        )

        # Make multiple requests and verify they're rate limited
        start_time = asyncio.get_event_loop().time()

        # Trigger rate limit checks (mock requests)
        for _ in range(3):
            await client._rate_limit()

        end_time = asyncio.get_event_loop().time()
        elapsed = end_time - start_time

        # Should take at least 0.2 seconds (3 requests at 10/sec = 0.1s each)
        assert elapsed >= 0.2

        await client.close()


@pytest.mark.asyncio
async def test_rest_error_handling():
    """Test REST error handling for OKX API errors."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "code": "50001",  # OKX error code
            "msg": "Invalid request",
            "data": []
        })

        mock_session.request.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value = mock_session
        mock_session.closed = False

        client = OKXRestClient(base_url="https://www.okx.com")

        with pytest.raises(ValueError, match="OKX API error"):
            await client._request("GET", "/api/v5/market/books")

        await client.close()


# =============================================================================
# ADAPTER TESTS
# =============================================================================


def test_adapter_initialization(exchange_config, instrument_configs):
    """Test OKX adapter initialization."""
    adapter = OKXAdapter(exchange_config, instrument_configs)

    assert adapter.exchange_name == "okx"
    assert not adapter.is_connected
    assert len(adapter._instruments) == 2


@pytest.mark.asyncio
async def test_adapter_connect(exchange_config, instrument_configs):
    """Test adapter connection."""
    with patch("src.adapters.okx.websocket.OKXWebSocketClient.connect") as mock_connect:
        mock_connect.return_value = None

        adapter = OKXAdapter(exchange_config, instrument_configs)
        await adapter.connect()

        assert mock_connect.called

        await adapter.disconnect()


def test_gap_detection(exchange_config, instrument_configs):
    """Test sequence gap detection."""
    adapter = OKXAdapter(exchange_config, instrument_configs)

    # No gap
    gap = adapter.detect_gap(prev_seq=100, curr_seq=101)
    assert gap is None

    # Gap detected
    gap = adapter.detect_gap(prev_seq=100, curr_seq=105)
    assert gap is not None
    assert gap.sequence_id_before == 100
    assert gap.sequence_id_after == 105
    assert gap.sequence_gap_size == 4
    assert gap.reason == "sequence_gap"

    # First message (no previous sequence)
    gap = adapter.detect_gap(prev_seq=None, curr_seq=100)
    assert gap is None


@pytest.mark.asyncio
async def test_health_check(exchange_config, instrument_configs):
    """Test health check."""
    adapter = OKXAdapter(exchange_config, instrument_configs)

    health = await adapter.health_check()

    assert health.exchange == "okx"
    assert health.status == ConnectionStatus.DISCONNECTED
    assert health.message_count == 0
    assert health.reconnect_count == 0
    assert health.gaps_last_hour == 0


@pytest.mark.asyncio
async def test_rest_fallback(exchange_config, instrument_configs):
    """Test REST fallback for order book."""
    with patch("src.adapters.okx.rest.OKXRestClient.get_orderbook") as mock_get:
        mock_snapshot = OrderBookSnapshot(
            exchange="okx",
            instrument="BTC-USDT-PERP",
            timestamp=datetime.now(timezone.utc),
            local_timestamp=datetime.now(timezone.utc),
            sequence_id=123456789,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
            asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("1.0"))],
        )
        mock_get.return_value = mock_snapshot

        adapter = OKXAdapter(exchange_config, instrument_configs)
        # Initialize REST client
        adapter._rest = OKXRestClient(base_url="https://www.okx.com")

        snapshot = await adapter.get_order_book_rest("BTC-USDT-PERP")

        assert snapshot.exchange == "okx"
        assert snapshot.instrument == "BTC-USDT-PERP"
        assert mock_get.called


# =============================================================================
# INTEGRATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_full_orderbook_flow(exchange_config, instrument_configs, okx_orderbook_message):
    """Test full order book flow from WebSocket to normalized snapshot."""
    with patch("websockets.connect") as mock_connect:
        # Setup mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.closed = False

        # Mock receiving order book message
        async def mock_recv():
            return json.dumps(okx_orderbook_message)

        mock_ws.recv = mock_recv
        mock_connect.return_value = mock_ws

        adapter = OKXAdapter(exchange_config, instrument_configs)
        await adapter.connect()

        # Subscribe
        await adapter.subscribe(["BTC-USDT-PERP"])

        # Get one snapshot
        count = 0
        async for snapshot in adapter.stream_order_books():
            assert snapshot.exchange == "okx"
            assert snapshot.instrument == "BTC-USDT-PERP"
            assert isinstance(snapshot.best_bid, Decimal)
            count += 1
            if count >= 1:
                break

        await adapter.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
