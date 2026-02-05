# Binance Exchange Adapter

Complete implementation of the Binance exchange adapter for real-time market data streaming. Implements the `ExchangeAdapter` interface defined in `/src/interfaces/exchange_adapter.py`.

## Architecture

```
src/adapters/binance/
├── __init__.py          # Module exports
├── adapter.py           # Main BinanceAdapter (implements ExchangeAdapter)
├── websocket.py         # WebSocket connection management
├── rest.py              # REST API fallback client
├── normalizer.py        # Data normalization utilities
└── README.md            # This file
```

## Components

### 1. BinanceAdapter (`adapter.py`)

Main adapter implementing the `ExchangeAdapter` interface. Coordinates all components.

**Key Features:**
- Manages separate WebSocket connections for perpetual and spot markets
- Detects sequence gaps using `lastUpdateId`
- Provides REST fallback when WebSocket fails
- Tracks health metrics (connection status, lag, gaps)
- Normalizes all data to unified Pydantic models

**Usage:**
```python
from src.adapters.binance import BinanceAdapter
from src.config.loader import load_config

config = load_config()
exchange_config = config.get_exchange("binance")
instruments = config.get_enabled_instruments()

adapter = BinanceAdapter(exchange_config, instruments)
await adapter.connect()
await adapter.subscribe(["BTC-USDT-PERP", "BTC-USDT-SPOT"])

# Stream order books
async for snapshot in adapter.stream_order_books():
    print(f"{snapshot.instrument}: spread={snapshot.spread_bps} bps")

# Health check
health = await adapter.health_check()
print(f"Status: {health.status}, Lag: {health.lag_ms}ms")
```

### 2. BinanceWebSocketClient (`websocket.py`)

Async WebSocket client for Binance futures and spot endpoints.

**Key Features:**
- Auto-reconnect with exponential backoff and jitter
- Ping/pong heartbeat every 30 seconds (configurable)
- Connection state tracking
- AsyncIterator pattern for message streaming
- Handles connection failures gracefully

**Endpoints:**
- **Perpetual Futures**: `wss://fstream.binance.com/stream`
- **Spot**: `wss://stream.binance.com:9443/ws`

**Stream Format:**
- Order book: `{symbol}@depth20@100ms`
- Ticker: `{symbol}@ticker`
- Mark price: `{symbol}@markPrice` (perpetuals only)

**Usage:**
```python
from src.adapters.binance import BinanceWebSocketClient

client = BinanceWebSocketClient(
    url="wss://fstream.binance.com/stream",
    ping_interval=30,
    max_reconnect_attempts=10
)

await client.connect()

# Stream messages
async for message in client.stream_messages():
    if message.get("e") == "depthUpdate":
        print(f"Order book update: seq={message['u']}")
```

### 3. BinanceRestClient (`rest.py`)

Async REST API client for fallback when WebSocket is unavailable.

**Key Features:**
- Rate limiting (10 requests/second by default)
- Retry logic for transient errors
- Async HTTP with `aiohttp`
- Parses responses to `OrderBookSnapshot` and `TickerSnapshot`

**Endpoints:**
- **Perpetuals**: `https://fapi.binance.com/fapi/v1/depth`
- **Spot**: `https://api.binance.com/api/v3/depth`

**Usage:**
```python
from src.adapters.binance import BinanceRestClient

client = BinanceRestClient(
    base_url="https://fapi.binance.com",
    rate_limit_per_second=10
)

snapshot = await client.get_orderbook("BTCUSDT", limit=20)
print(f"Best bid: {snapshot.best_bid}")

await client.close()
```

### 4. BinanceNormalizer (`normalizer.py`)

Static utility methods for converting Binance JSON to unified Pydantic models.

**Key Features:**
- Converts Binance `depthUpdate` to `OrderBookSnapshot`
- Combines 24hr ticker and mark price to `TickerSnapshot`
- Uses `Decimal` for all financial values
- Validates and sorts price levels
- Filters zero-quantity levels

**Binance Order Book Format:**
```json
{
  "e": "depthUpdate",
  "E": 1234567890,
  "s": "BTCUSDT",
  "U": 157,
  "u": 160,  // lastUpdateId (for gap detection)
  "b": [["50000.00", "1.5"], ...],
  "a": [["50001.00", "1.2"], ...]
}
```

**Usage:**
```python
from src.adapters.binance import BinanceNormalizer

snapshot = BinanceNormalizer.normalize_orderbook(
    raw_message=binance_depth_update,
    instrument="BTC-USDT-PERP",
    instrument_type="perpetual"
)

print(f"Spread: {snapshot.spread_bps} bps")
```

## Gap Detection

Binance uses `lastUpdateId` (field `u`) for sequence tracking. Gaps are detected when:

```
current_lastUpdateId != previous_lastUpdateId + 1
```

**Example:**
```python
# No gap (sequential)
adapter.detect_gap(prev_seq=100, curr_seq=101)  # Returns None

# Gap detected (jumped from 100 to 105)
gap = adapter.detect_gap(prev_seq=100, curr_seq=105)
# Returns GapMarker with:
#   - sequence_id_before: 100
#   - sequence_id_after: 105
#   - sequence_gap_size: 4 (105 - 100 - 1)
```

## Configuration

All configuration is loaded from `/config/exchanges.yaml`:

```yaml
exchanges:
  binance:
    enabled: true

    websocket:
      futures: "wss://fstream.binance.com/stream"
      spot: "wss://stream.binance.com:9443/ws"

    rest:
      futures: "https://fapi.binance.com"
      spot: "https://api.binance.com"

    connection:
      rate_limit_per_second: 10
      reconnect_delay_seconds: 5
      max_reconnect_attempts: 10
      ping_interval_seconds: 30
      ping_timeout_seconds: 10

    streams:
      orderbook_depth: 20
      orderbook_speed: "100ms"
```

Instrument symbols are mapped in `/config/instruments.yaml`:

```yaml
instruments:
  - id: "BTC-USDT-PERP"
    exchange_symbols:
      binance:
        symbol: "BTCUSDT"
        stream: "btcusdt@depth20@100ms"
        ticker_stream: "btcusdt@ticker"
        mark_price_stream: "btcusdt@markPrice"
```

## Error Handling

### WebSocket Errors

**Connection Loss:**
- Auto-reconnect with exponential backoff
- Max delay: 60 seconds
- Max attempts: 10 (configurable)

**Ping Timeout:**
- If pong not received within 10 seconds, connection is marked unhealthy
- Triggers reconnection

**Parse Errors:**
- Invalid JSON messages are logged and skipped
- Does not interrupt the stream

### REST API Errors

**Rate Limiting (429):**
- Throws `RateLimitError`
- Includes `Retry-After` header value

**Network Errors:**
- Throws `ConnectionError`
- Includes error details in log

**Timeout:**
- Default timeout: 10 seconds
- Throws `ConnectionError` with timeout message

### Data Validation Errors

**Invalid Order Book:**
- Crossed book (bid >= ask): Raises `ValueError`
- Missing fields: Raises `ValueError` with field name
- Invalid data types: Raises `ValueError`

**Pydantic Validation:**
- All models use Pydantic v2 validation
- Decimal precision enforced
- Field constraints checked (e.g., price >= 0)

## Performance

### Latency Requirements

- **WebSocket receive to Redis write**: < 50ms
- **Gap detection**: 100% detection rate
- **Auto-reconnect**: Within 5 seconds of disconnect

### Metrics

**Tracked via `HealthStatus`:**
- `message_count`: Total messages received
- `lag_ms`: Processing lag (now - last message time)
- `reconnect_count`: Number of reconnections
- `gaps_last_hour`: Data gaps in last hour

## Testing

Comprehensive unit tests in `/tests/unit/test_binance_adapter.py`.

**Test Coverage:**
- Normalizer: Valid/invalid data, edge cases
- WebSocket: Connection, reconnection, streaming
- REST: Fetching, rate limiting, errors
- Adapter: Full pipeline, gap detection, health check

**Run Tests:**
```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/unit/test_binance_adapter.py -v

# Run with coverage
pytest tests/unit/test_binance_adapter.py --cov=src/adapters/binance --cov-report=html
```

## Exchange-Specific Quirks

### Binance Futures vs Spot

**Differences:**
1. **Endpoints**: Separate WebSocket and REST URLs
2. **Streams**: Perpetuals have `markPrice` stream, spot doesn't
3. **Symbol Format**: Same symbol for both (e.g., "BTCUSDT")
4. **Sequence IDs**: Both use `lastUpdateId`, same semantics

### Combined Streams

Binance uses "combined streams" for multiple subscriptions:
```
wss://fstream.binance.com/stream?streams=stream1/stream2/stream3
```

Messages are wrapped in:
```json
{
  "stream": "btcusdt@depth20@100ms",
  "data": { ... }
}
```

The adapter automatically unwraps these.

### Ping/Pong

- Binance expects clients to respond to ping frames
- `websockets` library handles this automatically
- Additional heartbeat: Send ping every 30s, expect pong within 10s

## Logging

All logging uses `structlog` for structured JSON output.

**Key Log Events:**
- `websocket_connected`: WebSocket connection established
- `websocket_reconnecting`: Attempting reconnection
- `websocket_ping_timeout`: Ping timeout detected
- `rest_rate_limited`: REST API rate limit hit
- `binance_gap_detected`: Sequence gap detected
- `orderbook_normalization_failed`: Data parsing error

**Example Log Entry:**
```json
{
  "event": "binance_gap_detected",
  "timestamp": "2025-01-26T12:00:00Z",
  "level": "warning",
  "instrument": "BTC-USDT-PERP",
  "gap_size": 4,
  "prev_seq": 100,
  "curr_seq": 105
}
```

## Dependencies

Defined in `/pyproject.toml`:

```toml
dependencies = [
    "pydantic>=2.0,<3.0",      # Data validation
    "aiohttp>=3.9.0,<4.0",     # HTTP client
    "websockets>=12.0,<13.0",  # WebSocket client
    "structlog>=24.0.0,<25.0", # Logging
    "pyyaml>=6.0,<7.0",        # Config loading
]
```

## Future Enhancements (Phase 2)

- [ ] Support for Binance Coin-Margined Futures
- [ ] Support for Binance Options
- [ ] Trade stream integration (`@trade` stream)
- [ ] Liquidation stream (`@forceOrder` stream)
- [ ] Funding rate history via REST
- [ ] Historical data backfill

## Documentation Links

- **Binance Futures WebSocket**: https://binance-docs.github.io/apidocs/futures/en/#websocket-market-streams
- **Binance Futures REST**: https://binance-docs.github.io/apidocs/futures/en/#order-book
- **Binance Spot WebSocket**: https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams
- **Binance Spot REST**: https://binance-docs.github.io/apidocs/spot/en/#order-book

