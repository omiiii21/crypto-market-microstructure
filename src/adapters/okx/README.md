# OKX Exchange Adapter

Complete implementation of the `ExchangeAdapter` interface for OKX exchange, providing real-time data streaming for perpetual and spot markets.

## Overview

The OKX adapter integrates with OKX's public WebSocket API and REST endpoints to provide:

- Real-time order book snapshots (5-level depth)
- 24-hour ticker data
- Mark price and funding rate data (perpetuals)
- Automatic reconnection with exponential backoff
- Sequence gap detection using `seqId`
- REST API fallback for data recovery

## Architecture

```
src/adapters/okx/
├── __init__.py          # Module exports
├── adapter.py           # Main OKXAdapter class (implements ExchangeAdapter)
├── websocket.py         # OKXWebSocketClient (connection management)
├── normalizer.py        # OKXNormalizer (data transformation)
├── rest.py              # OKXRestClient (REST fallback)
└── README.md            # This file
```

## Key Components

### 1. OKXAdapter

Main adapter class implementing the `ExchangeAdapter` interface.

**Features:**
- Single WebSocket connection for all instruments
- Per-instrument sequence tracking
- Gap detection and logging
- Health monitoring
- REST fallback

**Example:**
```python
from src.adapters.okx import OKXAdapter
from src.config.loader import load_config

config = load_config()
exchange_config = config.get_exchange("okx")
instruments = config.get_enabled_instruments()

adapter = OKXAdapter(exchange_config, instruments)
await adapter.connect()
await adapter.subscribe(["BTC-USDT-PERP", "BTC-USDT-SPOT"])

async for snapshot in adapter.stream_order_books():
    print(f"{snapshot.instrument}: {snapshot.spread_bps} bps")
```

### 2. OKXWebSocketClient

Manages WebSocket connection to OKX public endpoint.

**OKX-Specific Details:**
- Single endpoint: `wss://ws.okx.com:8443/ws/v5/public`
- JSON subscription format: `{"op": "subscribe", "args": [...]}`
- String-based ping/pong: send `"ping"`, receive `"pong"`
- No separate endpoints for perpetual/spot (unlike Binance)

**Example:**
```python
from src.adapters.okx.websocket import OKXWebSocketClient

client = OKXWebSocketClient(
    url="wss://ws.okx.com:8443/ws/v5/public",
    ping_interval=30,
    max_reconnect_attempts=10
)
await client.connect()

channels = [
    {"channel": "books5", "instId": "BTC-USDT-SWAP"},
    {"channel": "tickers", "instId": "BTC-USDT-SWAP"}
]
await client.subscribe(channels)

async for message in client.stream_messages():
    print(message)
```

### 3. OKXNormalizer

Converts OKX-specific data formats to unified models.

**Transformations:**
- Order book: OKX books5 format → `OrderBookSnapshot`
- Ticker: OKX ticker format → `TickerSnapshot`
- Instrument IDs: `BTC-USDT-SWAP` → `BTC-USDT-PERP`

**Example:**
```python
from src.adapters.okx.normalizer import OKXNormalizer

# Normalize order book
snapshot = OKXNormalizer.normalize_orderbook(
    raw_message=okx_books_message,
    instrument="BTC-USDT-PERP"
)

# Normalize ticker
ticker = OKXNormalizer.normalize_ticker(
    raw_ticker=okx_ticker_data,
    raw_mark_price=okx_mark_price_data,  # None for spot
    instrument="BTC-USDT-PERP"
)

# Instrument ID mapping
okx_id = OKXNormalizer.to_okx_instrument_id("BTC-USDT-PERP")  # "BTC-USDT-SWAP"
our_id = OKXNormalizer.normalize_instrument_id("BTC-USDT-SWAP")  # "BTC-USDT-PERP"
```

### 4. OKXRestClient

Provides REST API fallback and recovery.

**Endpoints:**
- Order book: `GET /api/v5/market/books?instId={instId}&sz=20`
- Ticker: `GET /api/v5/market/ticker?instId={instId}`
- Mark price: `GET /api/v5/public/mark-price?instType=SWAP&instId={instId}`
- Funding rate: `GET /api/v5/public/funding-rate?instId={instId}`

**Example:**
```python
from src.adapters.okx.rest import OKXRestClient

client = OKXRestClient(
    base_url="https://www.okx.com",
    rate_limit_per_second=10
)

snapshot = await client.get_orderbook(
    inst_id="BTC-USDT-SWAP",
    limit=20,
    instrument="BTC-USDT-PERP"
)

ticker = await client.get_ticker(
    inst_id="BTC-USDT-SWAP",
    instrument="BTC-USDT-PERP"
)
```

## OKX-Specific Features

### Instrument ID Mapping

OKX uses different naming conventions:

| Our Format | OKX Format | Type |
|------------|------------|------|
| `BTC-USDT-PERP` | `BTC-USDT-SWAP` | Perpetual |
| `BTC-USDT-SPOT` | `BTC-USDT` | Spot |

The normalizer handles this mapping automatically.

### WebSocket Subscription Format

OKX requires JSON subscription messages:

```json
{
  "op": "subscribe",
  "args": [
    {"channel": "books5", "instId": "BTC-USDT-SWAP"},
    {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
    {"channel": "mark-price", "instId": "BTC-USDT-SWAP"}
  ]
}
```

### Order Book Message Format

```json
{
  "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
  "action": "snapshot",
  "data": [
    {
      "asks": [["50001.0", "1.5", "0", "2"], ...],
      "bids": [["50000.0", "2.0", "0", "3"], ...],
      "ts": "1234567890123",
      "seqId": 123456789,
      "checksum": -123456789
    }
  ]
}
```

**Price Level Format:** `[price, quantity, deprecated, num_orders]`
- We only use the first two elements

### Sequence Tracking

OKX uses `seqId` for gap detection:
- Should increment by 1 for each update
- Gaps indicate missed messages
- Reset sequence tracking after gaps

### Ping/Pong Mechanism

OKX uses string-based ping/pong (not binary WebSocket frames):
- Send: `"ping"` (as text message)
- Receive: `"pong"` (as text message)
- Interval: 25-30 seconds recommended

## Configuration

### exchanges.yaml

```yaml
okx:
  enabled: true

  websocket:
    public: "wss://ws.okx.com:8443/ws/v5/public"

  rest:
    base: "https://www.okx.com"

  connection:
    rate_limit_per_second: 10    # Conservative rate limit
    reconnect_delay_seconds: 5
    max_reconnect_attempts: 10
    ping_interval_seconds: 25    # OKX recommends <30s
    ping_timeout_seconds: 10

  streams:
    orderbook_channel: "books5"  # 5 levels (can use "books" for full)
    ticker_channel: "tickers"    # 24hr ticker
```

### instruments.yaml

```yaml
instruments:
  - id: "BTC-USDT-PERP"
    exchange_symbols:
      okx:
        symbol: "BTC-USDT-SWAP"
        inst_type: "SWAP"

  - id: "BTC-USDT-SPOT"
    exchange_symbols:
      okx:
        symbol: "BTC-USDT"
        inst_type: "SPOT"
```

## Error Handling

### Connection Errors

- **WebSocket disconnect:** Automatic reconnection with exponential backoff
- **Subscription failure:** Logged with structured error
- **Max reconnect exceeded:** Raises `ConnectionError`

### Data Errors

- **Invalid JSON:** Logged and skipped
- **Missing fields:** Raises `ValueError` with details
- **Invalid decimals:** Raises `ValueError`
- **Sequence gaps:** Logged as warning, gap marker created

### REST Errors

- **Rate limit (429):** Raises `RateLimitError`
- **OKX API error (code != "0"):** Raises `ValueError`
- **HTTP errors:** Raises `ConnectionError`
- **Timeout:** Raises `ConnectionError`

## Gap Detection

Sequence gaps are detected using `seqId`:

```python
# Expected sequence: 100, 101, 102, ...
# If we receive: 100 → 105
# Gap size: 4 messages missed

gap = adapter.detect_gap(prev_seq=100, curr_seq=105)
# GapMarker(
#     exchange="okx",
#     instrument="BTC-USDT-PERP",
#     sequence_id_before=100,
#     sequence_id_after=105,
#     reason="sequence_gap"
# )
```

**Actions on Gap:**
1. Log warning with gap size
2. Create `GapMarker` for audit trail
3. Continue processing (don't crash)
4. Track gaps for health monitoring

## Health Monitoring

The adapter provides comprehensive health metrics:

```python
health = await adapter.health_check()

# HealthStatus(
#     exchange="okx",
#     status=ConnectionStatus.CONNECTED,
#     last_message_at=datetime(...),
#     message_count=12345,
#     lag_ms=23,
#     reconnect_count=0,
#     gaps_last_hour=0
# )
```

**Health States:**
- `CONNECTED`: Normal operation
- `DEGRADED`: High lag or recent gaps
- `DISCONNECTED`: Not connected
- `RECONNECTING`: Attempting to reconnect

## Performance

### Latency Requirements

- **Target:** < 50ms from WebSocket receive to Redis write
- **Monitoring:** Track via `lag_ms` in health status
- **Optimization:** Use asyncio for concurrent processing

### Rate Limits

- **WebSocket:** No explicit limit (use 25s ping)
- **REST:** 20 requests per 2 seconds (we use 10/sec)
- **Implementation:** Token bucket rate limiting

## Testing

Run unit tests:

```bash
pytest tests/unit/test_okx_adapter.py -v
```

**Test Coverage:**
- WebSocket connection and reconnection
- Data normalization (valid and invalid)
- Sequence gap detection
- REST API fallback
- Health check
- Error handling
- Instrument ID mapping

## Differences from Binance Adapter

| Aspect | Binance | OKX |
|--------|---------|-----|
| WebSocket Endpoints | Separate (futures/spot) | Single (public) |
| Symbol Format | `BTCUSDT` | `BTC-USDT-SWAP` |
| Price Level Format | `[price, qty]` | `[price, qty, deprecated, orders]` |
| Subscription | URL-based streams | JSON messages |
| Ping/Pong | Binary frames | String messages |
| Sequence Field | `lastUpdateId` (`u`) | `seqId` |
| Timestamp Format | Milliseconds (number) | Milliseconds (string) |

## Known Issues and Quirks

### 1. Checksum Validation

OKX provides checksums but we don't validate them currently. Future enhancement could add validation.

### 2. Timestamp Precision

OKX timestamps are strings (not numbers). The normalizer handles conversion.

### 3. Deprecated Fields

Price levels include a deprecated field (index 2) that we ignore.

### 4. Rate Limiting

OKX rate limits are per IP. Be conservative with REST requests.

### 5. Market Data Delay

OKX WebSocket data may have slight delays during high volatility. Monitor via `lag_ms`.

## Future Enhancements

1. **Checksum Validation:** Verify order book integrity
2. **L2 Deltas:** Support incremental updates (not just snapshots)
3. **More Channels:** Trade data, liquidations, open interest
4. **Advanced Gap Recovery:** Fetch missed data via REST
5. **Multi-region Support:** Connect to closest OKX endpoint

## References

- [OKX WebSocket API](https://www.okx.com/docs-v5/en/#websocket-api-public-channel)
- [OKX Order Book Channel](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel)
- [OKX REST Market Data](https://www.okx.com/docs-v5/en/#rest-api-market-data)

## Support

For issues or questions:
1. Check logs for structured error messages
2. Verify configuration in `config/exchanges.yaml`
3. Review OKX API documentation
4. Check sequence gaps in health monitoring

## License

This module is part of the Crypto Market Microstructure Surveillance System.
Owned by DATA-ENGINEER agent.
