# Crypto Market Microstructure

## Project Overview

Real-time surveillance system for monitoring market quality and pricing integrity across crypto spot and derivatives markets. 

**Perspective**: This system is designed for **exchange market operations**, not trading. The goal is market stability, pricing correctness, and liquidity quality—not PnL.

## Key Documents

| Document | Location | Purpose |
|----------|----------|---------|
| PRD v2.0 | `docs/PRD.md` | Source of truth for all requirements |
| Agent Architecture | `docs/agent-architecture.md` | Agent design and communication protocol |
| Configuration Guide | `docs/configuration.md` | How to configure thresholds, exchanges, alerts |

## Tech Stack

- **Language**: Python 3.11+
- **Async**: AsyncIO, aiohttp for WebSocket clients
- **Real-time State**: Redis 7
- **Historical Data**: TimescaleDB (PostgreSQL 15 + time-series extension)
- **Dashboard**: Plotly Dash
- **Deployment**: Docker Compose
- **Data Validation**: Pydantic v2
- **Logging**: structlog (JSON structured logging)

## Scope (Phase 1)

| Dimension | Scope |
|-----------|-------|
| Exchanges | Binance, OKX |
| Instruments | BTC-USDT Perpetual, BTC-USDT Spot |
| Data Source | WebSocket (primary), REST polling (fallback) |

## Project Structure

```
crypto-market-microstructure/
├── CLAUDE.md                       # This file - project context
├── README.md                       # Setup and usage instructions
├── docker-compose.yml              # Container orchestration
├── pyproject.toml                  # Python project config
│
├── config/                         # Configuration files
│   ├── exchanges.yaml              # Exchange endpoints, rate limits
│   ├── instruments.yaml            # Trading pairs, symbols
│   ├── alerts.yaml                 # Alert definitions, thresholds
│   └── features.yaml               # Feature flags (regime detection, backfill)
│
├── src/
│   ├── models/                     # Shared Pydantic models (ARCHITECT owns)
│   │   ├── orderbook.py            # OrderBookSnapshot, PriceLevel
│   │   ├── ticker.py               # TickerSnapshot
│   │   ├── metrics.py              # SpreadMetrics, DepthMetrics, BasisMetrics
│   │   ├── alerts.py               # Alert, AlertResult, AlertDefinition
│   │   └── health.py               # HealthStatus, GapMarker
│   │
│   ├── interfaces/                 # Abstract interfaces (ARCHITECT owns)
│   │   └── exchange_adapter.py     # ExchangeAdapter ABC
│   │
│   ├── config/                     # Config loading (ARCHITECT owns)
│   │   ├── loader.py               # YAML → Pydantic
│   │   └── models.py               # Config Pydantic models
│   │
│   ├── adapters/                   # Exchange adapters (DATA-ENGINEER owns)
│   │   ├── binance/
│   │   │   ├── websocket.py        # Binance WebSocket client
│   │   │   ├── normalizer.py       # Raw → OrderBookSnapshot
│   │   │   └── rest.py             # REST fallback
│   │   └── okx/
│   │       ├── websocket.py
│   │       ├── normalizer.py
│   │       └── rest.py
│   │
│   ├── metrics/                    # Metric calculators (QUANT owns)
│   │   ├── spread.py               # SpreadCalculator
│   │   ├── depth.py                # DepthCalculator
│   │   ├── basis.py                # BasisCalculator
│   │   ├── zscore.py               # ZScoreCalculator (with warmup guards)
│   │   └── aggregator.py           # MetricsAggregator
│   │
│   ├── detection/                  # Alert logic (ANOMALY-DETECTOR owns)
│   │   ├── evaluator.py            # AlertEvaluator (dual-condition)
│   │   ├── persistence.py          # PersistenceTracker
│   │   ├── manager.py              # AlertManager (lifecycle)
│   │   ├── storage.py              # AlertStorage (Redis + PostgreSQL)
│   │   └── channels/
│   │       ├── console.py
│   │       └── slack.py
│   │
│   └── storage/                    # Storage clients (ARCHITECT owns)
│       ├── redis_client.py
│       └── postgres_client.py
│
├── services/                       # Docker service entry points
│   ├── data-ingestion/
│   │   ├── Dockerfile
│   │   └── main.py
│   ├── metrics-engine/
│   │   ├── Dockerfile
│   │   └── main.py
│   ├── anomaly-detector/
│   │   ├── Dockerfile
│   │   └── main.py
│   └── dashboard/                  # VIZ owns
│       ├── Dockerfile
│       ├── app.py
│       ├── layouts/
│       ├── components/
│       └── callbacks/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
└── docs/
    ├── PRD.md
    ├── agent-architecture.md
    └── configuration.md
```

## Agent Ownership

| Agent | Owns | Does NOT Touch |
|-------|------|----------------|
| **ARCHITECT** | models/, interfaces/, config/, storage/, docker-compose.yml, README.md | adapters/, metrics/, detection/, dashboard/ |
| **DATA-ENGINEER** | adapters/ | metrics/, detection/, dashboard/ |
| **QUANT** | metrics/ | adapters/, detection/, dashboard/ |
| **ANOMALY-DETECTOR** | detection/ | adapters/, metrics/, dashboard/ |
| **VIZ** | services/dashboard/ | adapters/, metrics/, detection/ |

## Critical Rules

### 1. Financial Precision
```python
# ✅ CORRECT - Use Decimal
from decimal import Decimal
spread_bps = Decimal("2.5")

# ❌ WRONG - Never use float for financial data
spread_bps = 2.5
```

### 2. Z-Score Warmup Guards
```python
# Z-score calculator MUST return None during warmup
def add_sample(self, value: Decimal) -> Optional[Decimal]:
    if len(self.buffer) < self.min_samples:  # min_samples = 30
        return None  # ✅ Correct: Don't compute z-score yet
    # ... compute z-score
```

### 3. Dual-Condition Alerts
```python
# Alert fires ONLY if ALL conditions met:
# 1. Metric exceeds threshold (e.g., spread > 3 bps)
# 2. Z-score exceeds threshold (e.g., zscore > 2.0) - if required
# 3. Persistence met (e.g., condition lasted > 120s) - if required

if zscore_value is None and alert_def.requires_zscore:
    return AlertResult(triggered=False, skip_reason="zscore_warmup")
```

### 4. No Hardcoded Values
```python
# ✅ CORRECT - From config
threshold = config.alerts.thresholds["BTC-USDT-PERP"].spread_warning.threshold

# ❌ WRONG - Hardcoded
threshold = 3.0
```

### 5. Interface Compliance
All exchange adapters MUST implement `ExchangeAdapter` interface defined by ARCHITECT.

## Key Thresholds (BTC-USDT-PERP)

| Metric | Warning | Critical |
|--------|---------|----------|
| Spread | > 3 bps AND z > 2σ | > 5 bps AND z > 3σ |
| Basis | > 10 bps AND z > 2σ (persist 2m) | > 20 bps AND z > 3σ (persist 1m) |
| Depth (10bps) | < $500K | < $200K |

## Current Phase

**Phase 1: Foundation**
- [ ] Project structure (ARCHITECT)
- [ ] Pydantic models (ARCHITECT)
- [ ] ExchangeAdapter interface (ARCHITECT)
- [ ] Configuration loader (ARCHITECT)
- [ ] Docker setup (ARCHITECT)

## How to Invoke Agents

```
@architect Set up the initial project structure
@data-engineer Implement BinanceAdapter for perpetual futures
@quant Implement SpreadCalculator with all metrics
@anomaly-detector Implement AlertEvaluator with dual-condition logic
@viz Create the main dashboard layout
```

## Environment Variables

```bash
# .env
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://surveillance:password@localhost:5432/surveillance
LOG_LEVEL=INFO
```
