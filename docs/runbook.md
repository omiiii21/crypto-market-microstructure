# Operations Runbook

Operational procedures and troubleshooting guide for the Crypto Market Microstructure Surveillance System.

## Table of Contents

1. [Service Management](#service-management)
2. [Health Checks](#health-checks)
3. [Monitoring](#monitoring)
4. [Common Issues](#common-issues)
5. [Adding New Instruments](#adding-new-instruments)
6. [Adding New Exchanges](#adding-new-exchanges)
7. [Database Operations](#database-operations)
8. [Emergency Procedures](#emergency-procedures)

---

### Fresh Start (Complete Reset)

Use this procedure when you want to **wipe all data** and start from scratch:
- Services were stopped for an extended period (causing data gaps)
- You want to clear all historical metrics, alerts, and order book data
- Testing from a clean slate

> **WARNING:** This permanently deletes ALL data (metrics, alerts, order book history). This cannot be undone. Z-score warmup will restart, suppressing alerts for ~30 seconds.

**Step 1: Stop services and delete volumes**

```bash
# Stop all services and remove volumes (deletes Redis + PostgreSQL data)
docker-compose down -v
```

**Step 2: Rebuild and start (optional but recommended)**

```bash
# Rebuild images for a completely clean state
docker-compose build

# Start all services
docker-compose up -d
```

**Step 3: Verify fresh state**

Wait ~10 seconds for services to start, then run these checks:

```bash
# 1. Confirm PostgreSQL tables are empty
docker exec surveillance-db psql -U surveillance -d surveillance -c "SELECT count(*) FROM metrics;"
# Expected: 0

# 2. Confirm Redis has no stale keys (will be empty initially)
docker exec surveillance-redis redis-cli keys "*"
# Expected: (empty array) initially, then orderbook:* keys appear after ~5 seconds

# 3. Confirm z-score warmup is restarting
docker exec surveillance-redis redis-cli llen "zscore:binance:BTC-USDT-PERP:spread_bps"
# Expected: 0 initially, then incrementing toward 30 over ~30 seconds

# 4. Confirm no active alerts
docker exec surveillance-redis redis-cli smembers "alerts:active"
# Expected: (empty array)

# 5. Verify all services are healthy
docker-compose ps
# Expected: All 6 services show "Up (healthy)"
```

**Step 4: Verify dashboard**

Open http://localhost:8050 and confirm:
- Z-score displays show "warming up" or sample count < 30
- No stale alerts in the alerts panel
- Charts begin populating after ~30 seconds

---

## Quick Start Verification

After starting services with `docker-compose up -d`, verify the system is working:

```bash
# 1. Check all services are healthy
docker-compose ps
# Expected: All services show "Up (healthy)"

# 2. Verify data is flowing (wait ~30 seconds after startup)
docker-compose exec timescaledb psql -U surveillance -d surveillance -c "SELECT count(*) FROM metrics;"
# Expected: Count should be increasing

# 3. Check exchange subscriptions
docker-compose logs data-ingestion | grep -i "subscribed"
# Expected: "Successfully subscribed to binance BTC-USDT-PERP"
#           "Successfully subscribed to okx BTC-USDT-PERP"

# 4. Open dashboard
open http://localhost:8050  # macOS
# or: start http://localhost:8050  # Windows
```

**System Status Summary:**
- ✅ All 6 services healthy
- ✅ Data flowing: WebSocket → Redis → Metrics → PostgreSQL
- ✅ Exchange subscriptions: Binance + OKX active
- ⚠️ Dashboard UI: AsyncIO event loop conflict (data exists but UI may not populate)

---

## Service Management

### Starting All Services

```bash
# Start all services in detached mode
docker-compose up -d

# Wait for services to be healthy
docker-compose ps

# Expected output: all services should show "healthy"
```

### Starting Infrastructure Only

```bash
# Start only Redis and TimescaleDB (for local development)
docker-compose up -d redis timescaledb
```

### Stopping Services

```bash
# Stop all services gracefully
docker-compose down

# Stop and remove volumes (WARNING: deletes data)
docker-compose down -v
```

### Restarting a Single Service

```bash
# Restart a specific service
docker-compose restart [service-name]

# Examples:
docker-compose restart data-ingestion
docker-compose restart metrics-engine
docker-compose restart anomaly-detector
docker-compose restart dashboard
```

### Viewing Logs

```bash
# Follow logs for all services
docker-compose logs -f

# Follow logs for a specific service
docker-compose logs -f [service-name]

# View last 100 lines
docker-compose logs --tail=100 [service-name]

# View logs with timestamps
docker-compose logs -f -t [service-name]
```

### Scaling Services

```bash
# Scale a service (if needed for load testing)
docker-compose up -d --scale metrics-engine=2

# Note: The current architecture assumes single instances
```

---

## Health Checks

### Check All Services

```bash
# Show service status
docker-compose ps

# Expected output:
# NAME                    STATUS          PORTS
# surveillance-redis      Up (healthy)    0.0.0.0:6379->6379/tcp
# surveillance-db         Up (healthy)    0.0.0.0:5432->5432/tcp
# surveillance-ingestion  Up (healthy)
# surveillance-metrics    Up (healthy)
# surveillance-anomaly    Up (healthy)
# surveillance-dashboard  Up (healthy)    0.0.0.0:8050->8050/tcp
```

### Check Redis

```bash
# Using redis-cli
redis-cli ping
# Expected: PONG

# Check Redis info
redis-cli info server

# Check memory usage
redis-cli info memory
```

### Check PostgreSQL

```bash
# Using psql
psql -h localhost -U surveillance -d surveillance -c "SELECT 1"

# Check active connections
psql -h localhost -U surveillance -d surveillance -c "SELECT count(*) FROM pg_stat_activity"

# Check table sizes
psql -h localhost -U surveillance -d surveillance -c "
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10
"
```

### Check Dashboard

```bash
# Verify dashboard is responding
curl -f http://localhost:8050/ || echo "Dashboard not responding"
```

---

## Monitoring

### Key Metrics to Watch

| Metric | Warning Threshold | Critical Threshold |
|--------|-------------------|-------------------|
| Message rate (per exchange) | < 10 msg/s | < 1 msg/s |
| Processing lag | > 100ms | > 500ms |
| Gap frequency | > 5 gaps/hour | > 20 gaps/hour |
| Active P1 alerts | > 0 | > 5 |
| Redis memory | > 400MB | > 480MB |

### Redis Keys to Monitor

```bash
# List all orderbook keys
redis-cli keys "orderbook:*"

# Check z-score buffer lengths (warmup status)
redis-cli llen "zscore:binance:BTC-USDT-PERP:spread_bps"

# List active alerts
redis-cli smembers "alerts:active"

# Check health status
redis-cli get "health:binance"
redis-cli get "health:okx"
```

### View Real-time Metrics

```bash
# Watch message counts in real-time
watch -n 1 'redis-cli hget "health:binance" message_count'

# Watch active alert count
watch -n 5 'redis-cli scard "alerts:active"'
```

### Check Metric Values

```sql
-- Recent spread metrics
SELECT timestamp, value as spread_bps, zscore
FROM metrics
WHERE metric_name = 'spread_bps'
  AND exchange = 'binance'
  AND instrument = 'BTC-USDT-PERP'
ORDER BY timestamp DESC
LIMIT 10;

-- Alert statistics (last hour)
SELECT priority, count(*)
FROM alerts
WHERE triggered_at > NOW() - INTERVAL '1 hour'
GROUP BY priority;

-- Gap statistics (last 24 hours)
SELECT exchange, count(*), avg(duration_seconds)
FROM data_gaps
WHERE gap_start > NOW() - INTERVAL '24 hours'
GROUP BY exchange;
```

---

## Common Issues

### Issue: Z-Score Alerts Not Firing

**Symptoms:**
- Spread exceeds threshold but no alert is triggered
- Dashboard shows "Z-Score: warming up (X/30)"

**Cause:**
Z-score is in warmup period (less than 30 samples collected).

**Solution:**
1. Wait for warmup to complete (~30 seconds of data)
2. Check z-score buffer length:
   ```bash
   redis-cli llen "zscore:binance:BTC-USDT-PERP:spread_bps"
   ```
3. If buffer is stuck, check data-ingestion logs:
   ```bash
   docker-compose logs -f data-ingestion | grep -i "error\|warning"
   ```

**Expected Behavior:**
The system intentionally skips alerts during warmup to prevent false positives. This is correct behavior per PRD requirements.

---

### Issue: High Message Lag

**Symptoms:**
- Dashboard shows stale data
- Health status shows high lag_ms (> 100ms)
- Alerts fire late

**Cause:**
Processing bottleneck in metrics-engine or data-ingestion.

**Solution:**
1. Check metrics-engine logs:
   ```bash
   docker-compose logs -f metrics-engine | grep -i "slow\|lag\|timeout"
   ```

2. Check Redis memory:
   ```bash
   redis-cli info memory | grep used_memory_human
   ```

3. Check PostgreSQL connection pool:
   ```sql
   SELECT count(*) FROM pg_stat_activity WHERE state = 'active';
   ```

4. Consider restarting the metrics-engine:
   ```bash
   docker-compose restart metrics-engine
   ```

---

### Issue: Data Gaps Detected Frequently

**Symptoms:**
- Frequent gap markers in database
- Z-score buffers resetting
- Alerts getting skipped

**Cause:**
- Network issues
- Exchange rate limiting
- WebSocket disconnections

**Solution:**
1. Check data-ingestion logs for disconnections:
   ```bash
   docker-compose logs data-ingestion | grep -i "disconnect\|reconnect\|gap"
   ```

2. Check exchange connection settings in `config/exchanges.yaml`:
   ```yaml
   connection:
     rate_limit_per_second: 10
     reconnect_delay_seconds: 5
     max_reconnect_attempts: 10
   ```

3. Verify network connectivity:
   ```bash
   docker-compose exec data-ingestion ping -c 3 fstream.binance.com
   ```

4. Review gap history:
   ```sql
   SELECT exchange, instrument, gap_start, duration_seconds, reason
   FROM data_gaps
   WHERE gap_start > NOW() - INTERVAL '1 hour'
   ORDER BY gap_start DESC;
   ```

---

### Issue: Dashboard Not Loading

**Symptoms:**
- http://localhost:8050 returns error or timeout
- Dashboard container health check failing

**Solution:**
1. Check dashboard logs:
   ```bash
   docker-compose logs -f dashboard
   ```

2. Verify port is available:
   ```bash
   netstat -an | grep 8050
   ```

3. Check if dependencies are healthy:
   ```bash
   docker-compose ps redis timescaledb
   ```

4. Restart dashboard:
   ```bash
   docker-compose restart dashboard
   ```

---

### Issue: Dashboard Not Populating Data

**Symptoms:**
- Dashboard loads but shows no charts or metrics
- All services show healthy status
- Data is flowing through the system

**Verification Steps:**
1. Verify data is in the database:
   ```sql
   -- Check metric count
   SELECT count(*) FROM metrics;

   -- Check recent metrics
   SELECT exchange, instrument, metric_name, value, timestamp
   FROM metrics
   ORDER BY timestamp DESC
   LIMIT 10;
   ```

2. Verify exchange subscriptions:
   ```bash
   # Check data-ingestion logs for successful subscriptions
   docker-compose logs data-ingestion | grep -i "subscribed\|connected"

   # Expected output:
   # "Successfully subscribed to binance BTC-USDT-PERP"
   # "Successfully subscribed to okx BTC-USDT-PERP"
   ```

3. Verify Redis state:
   ```bash
   # Check if orderbook data is being written
   redis-cli keys "orderbook:*"

   # Get a recent orderbook snapshot
   redis-cli get "orderbook:binance:BTC-USDT-PERP"
   ```

**Known Issue: Dashboard AsyncIO Event Loop Conflict**

If data is confirmed flowing but dashboard is blank, this is likely an AsyncIO event loop issue in the dashboard service.

**Symptoms:**
- Dashboard container is healthy
- Database shows 89,000+ metric records
- Exchange subscriptions are active
- Dashboard UI loads but remains blank

**Cause:**
The Dash application may have an AsyncIO event loop conflict when running callbacks that interact with async Redis/PostgreSQL clients.

**Workaround:**
Use the underlying data directly from PostgreSQL:
```bash
# Access PostgreSQL from host (requires explicit password)
psql -h localhost -U surveillance -d surveillance
# When prompted, enter password: surveillance_dev

# Then query metrics
SELECT * FROM metrics ORDER BY timestamp DESC LIMIT 100;
```

**Resolution:**
This requires updates to the dashboard service implementation (VIZ agent responsibility). The issue is in [services/dashboard/](../services/dashboard/) and needs proper AsyncIO loop handling in the Dash callbacks.

---

### Issue: Host PostgreSQL Connection Requires Password

**Symptoms:**
- Connecting from host machine with `psql` fails with "password authentication failed"
- Error: `postgresql://surveillance:surveillance_dev@localhost:5432/surveillance ❌`

**Cause:**
The connection string format `postgresql://user:pass@host:port/db` doesn't work in some environments. You must provide the password interactively.

**Solution:**
```bash
# Method 1: Use -W flag to force password prompt
psql -h localhost -U surveillance -d surveillance -W
# Enter password when prompted: surveillance_dev

# Method 2: Set PGPASSWORD environment variable
PGPASSWORD=surveillance_dev psql -h localhost -U surveillance -d surveillance

# Method 3: Use .pgpass file (Linux/macOS)
echo "localhost:5432:surveillance:surveillance:surveillance_dev" >> ~/.pgpass
chmod 600 ~/.pgpass
psql -h localhost -U surveillance -d surveillance
```

**Credentials:**
- Database: `surveillance`
- User: `surveillance`
- Password: `surveillance_dev`
- Host: `localhost` (from host) or `timescaledb` (from containers)
- Port: `5432`

The credentials are stored in:
- `.env` file (root directory)
- `docker-compose.yml` environment variables
- Service Dockerfiles default ENV variables

---

### Issue: Alerts Stuck in Active State

**Symptoms:**
- Alerts not resolving automatically
- Old alerts appearing in dashboard

**Cause:**
Auto-resolve may not be working or condition persists.

**Solution:**
1. Check if condition is still met (metric still elevated)
2. Manually resolve in Redis:
   ```bash
   # List active alerts
   redis-cli smembers "alerts:active"

   # Remove specific alert from active set
   redis-cli srem "alerts:active" "[alert_id]"
   ```

3. Check anomaly-detector logs:
   ```bash
   docker-compose logs anomaly-detector | grep -i "resolve"
   ```

---

## Adding New Instruments

### Step 1: Update instruments.yaml

```yaml
# config/instruments.yaml
instruments:
  # Add new instrument
  - id: ETH-USDT-PERP
    name: "ETH/USDT Perpetual"
    type: perpetual
    base: ETH
    quote: USDT
    enabled: true
    depth_levels: 20
    exchange_symbols:
      binance:
        symbol: ETHUSDT
        stream: ethusdt@depth20@100ms
      okx:
        symbol: ETH-USDT-SWAP
        inst_type: SWAP
```

### Step 2: Add Alert Thresholds

```yaml
# config/alerts.yaml
thresholds:
  ETH-USDT-PERP:
    spread_warning:
      threshold: 5.0    # ETH typically has wider spreads
      zscore: 2.0
    spread_critical:
      threshold: 10.0
      zscore: 3.0
    depth_warning:
      threshold: 300000  # $300K (less liquidity than BTC)
    depth_critical:
      threshold: 100000
```

### Step 3: Add Basis Pair (if applicable)

```yaml
# config/instruments.yaml
basis_pairs:
  - perp: ETH-USDT-PERP
    spot: ETH-USDT-SPOT
```

### Step 4: Restart Data Ingestion

```bash
docker-compose restart data-ingestion

# Verify new instrument is being captured
docker-compose logs -f data-ingestion | grep "ETH-USDT"
```

### Step 5: Verify in Dashboard

1. Open http://localhost:8050
2. Select ETH-USDT-PERP from instrument dropdown
3. Wait for z-score warmup (~30 seconds)

---

## Adding New Exchanges

### Step 1: Create Exchange Adapter

Create new adapter in `src/adapters/[exchange]/`:
- `websocket.py` - WebSocket client
- `normalizer.py` - Raw data to OrderBookSnapshot
- `rest.py` - REST fallback

The adapter must implement the `ExchangeAdapter` interface.

### Step 2: Add Exchange Configuration

```yaml
# config/exchanges.yaml
newexchange:
  enabled: true
  websocket:
    public: wss://ws.newexchange.com/ws/v5/public
  rest:
    base: https://api.newexchange.com
  connection:
    rate_limit_per_second: 10
    reconnect_delay_seconds: 5
    max_reconnect_attempts: 10
    ping_interval_seconds: 30
  streams:
    orderbook_depth: 20
    orderbook_speed: "100ms"
```

### Step 3: Add Symbol Mappings

```yaml
# config/instruments.yaml
instruments:
  - id: BTC-USDT-PERP
    exchange_symbols:
      newexchange:
        symbol: BTC-USDT-PERP
        inst_type: SWAP
```

### Step 4: Update Data Ingestion Service

Modify `services/data-ingestion/main.py` to instantiate the new adapter.

### Step 5: Restart All Services

```bash
docker-compose down
docker-compose up -d --build
```

---

## Database Operations

### View Table Sizes

```sql
SELECT
    tablename,
    pg_size_pretty(pg_total_relation_size('public.' || tablename)) AS total_size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size('public.' || tablename) DESC;
```

### Manual Data Cleanup

```sql
-- Delete old order book snapshots (older than 30 days)
DELETE FROM order_book_snapshots
WHERE timestamp < NOW() - INTERVAL '30 days';

-- Delete old metrics (older than 90 days)
DELETE FROM metrics
WHERE timestamp < NOW() - INTERVAL '90 days';

-- Analyze tables after cleanup
ANALYZE order_book_snapshots;
ANALYZE metrics;
```

### Backup Database

```bash
# Full backup
docker-compose exec timescaledb pg_dump -U surveillance surveillance > backup_$(date +%Y%m%d).sql

# Schema only
docker-compose exec timescaledb pg_dump -U surveillance --schema-only surveillance > schema_backup.sql
```

### Restore Database

```bash
# Restore from backup
cat backup_20250126.sql | docker-compose exec -T timescaledb psql -U surveillance surveillance
```

---

## Emergency Procedures

### Full System Restart

```bash
# Stop all services
docker-compose down

# Clear Redis data (WARNING: loses current state)
docker-compose up -d redis
docker-compose exec redis redis-cli flushdb
docker-compose down

# Start fresh
docker-compose up -d

# Monitor startup
docker-compose logs -f
```

### Disable Alerts Temporarily

```yaml
# config/features.yaml
# Edit to disable alerts
alerts:
  enabled: false
```

Then restart anomaly-detector:
```bash
docker-compose restart anomaly-detector
```

### Disable a Specific Exchange

```yaml
# config/exchanges.yaml
binance:
  enabled: false  # Disable this exchange
```

Then restart:
```bash
docker-compose restart data-ingestion
```

### Emergency Contact Escalation

1. **P1 Alerts (Critical):**
   - Slack channel: #market-ops-critical
   - PagerDuty: On-call rotation
   - Response time: < 5 minutes

2. **P2 Alerts (Warning):**
   - Slack channel: #market-ops
   - Response time: < 30 minutes

3. **Infrastructure Issues:**
   - Check service health: `docker-compose ps`
   - Check logs: `docker-compose logs -f`
   - Contact: #infrastructure-ops

---

## Appendix: Key File Locations

| File | Purpose |
|------|---------|
| `config/exchanges.yaml` | Exchange endpoints and settings |
| `config/instruments.yaml` | Trading pair definitions |
| `config/alerts.yaml` | Alert thresholds |
| `config/features.yaml` | Feature flags |
| `docker-compose.yml` | Container orchestration |
| `.env` | Environment variables |

## Appendix: Service Ports

| Service | Port | Protocol |
|---------|------|----------|
| Redis | 6379 | TCP |
| PostgreSQL | 5432 | TCP |
| Dashboard | 8050 | HTTP |

## Appendix: Log Levels

Set via `LOG_LEVEL` environment variable:

| Level | Description |
|-------|-------------|
| DEBUG | Verbose debugging information |
| INFO | Normal operational messages |
| WARNING | Warning messages (default) |
| ERROR | Error messages only |
| CRITICAL | Critical errors only |

Example:
```bash
LOG_LEVEL=DEBUG docker-compose up -d metrics-engine
```
