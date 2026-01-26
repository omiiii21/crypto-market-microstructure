# Crypto Market Microstructure

Real-time surveillance system for monitoring market quality and pricing integrity across crypto spot and derivatives markets.

> **Perspective**: This system is designed for **exchange market operations**, not trading. The goal is market stability, pricing correctness, and liquidity quality—not PnL.

## What This System Does

1. **Monitors Market Quality** — Tracks spreads, depth, and order book health in real-time
2. **Tracks Derivatives Pricing** — Ensures perp prices stay aligned with spot, index, and mark prices
3. **Detects Anomalies** — Dual-condition alerts (threshold + z-score) to reduce false positives
4. **Provides Visibility** — Real-time dashboards for operations and quant teams
5. **Supports Analysis** — Historical data for post-event analysis and attribution

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)

### Run with Docker

```bash
# Clone the repository
git clone https://github.com/yourusername/crypto-market-microstructure.git
cd crypto-market-microstructure

# Copy environment file
cp .env.example .env

# Start all services
docker-compose up -d

# View dashboard
open http://localhost:8050
```

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -e ".[dev]"

# Start infrastructure (Redis + TimescaleDB)
docker-compose up -d redis timescaledb

# Run data ingestion
python -m services.data_ingestion.main

# Run metrics engine
python -m services.metrics_engine.main

# Run anomaly detector
python -m services.anomaly_detector.main

# Run dashboard
python -m services.dashboard.main
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CRYPTO MARKET MICROSTRUCTURE                         │
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   Binance   │    │     OKX     │    │   [Future]  │    │   [Future]  │  │
│  │   Adapter   │    │   Adapter   │    │   Exchange  │    │   Exchange  │  │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘  │
│         │                  │                  │                  │          │
│         └──────────────────┼──────────────────┼──────────────────┘          │
│                            ▼                                                 │
│                  ┌─────────────────────┐                                    │
│                  │   Metrics Engine    │                                    │
│                  │  • Spread           │                                    │
│                  │  • Depth            │                                    │
│                  │  • Basis            │                                    │
│                  │  • Z-Score          │                                    │
│                  └──────────┬──────────┘                                    │
│                             ▼                                                │
│                  ┌─────────────────────┐                                    │
│                  │  Anomaly Detector   │                                    │
│                  │  • Threshold        │                                    │
│                  │  • Z-Score          │                                    │
│                  │  • Persistence      │                                    │
│                  └──────────┬──────────┘                                    │
│                             ▼                                                │
│         ┌───────────────────┼───────────────────┐                           │
│         ▼                   ▼                   ▼                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                     │
│  │    Redis    │    │ TimescaleDB │    │  Dashboard  │                     │
│  │  (Current)  │    │ (Historical)│    │ (Plotly)    │                     │
│  └─────────────┘    └─────────────┘    └─────────────┘                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Features

### Dual-Condition Alerts

Alerts fire only when **both** threshold AND statistical conditions are met:

```
Spread Alert fires when:
  ✓ spread_bps > 3.0 bps (threshold)
  ✓ spread_zscore > 2.0σ (statistical anomaly)
  
This prevents false positives during expected volatility.
```

### Z-Score Warmup Guards

The system doesn't fire alerts during startup or after data gaps:

```
System Start → Warmup (collecting 30 samples) → Active
                    ↓
         "Z-Score: ⏳ warming up (15/30)"
```

### Asset-Specific Thresholds

Different assets have different thresholds based on their volatility:

| Asset | Spread Warning | Spread Critical |
|-------|---------------|-----------------|
| BTC-USDT | 3 bps | 5 bps |
| ETH-USDT | 5 bps | 10 bps |

## Configuration

All configuration is in YAML files under `config/`:

```yaml
# config/alerts.yaml
thresholds:
  BTC-USDT-PERP:
    spread_warning:
      threshold: 3.0    # bps
      zscore: 2.0       # σ
    spread_critical:
      threshold: 5.0
      zscore: 3.0
```

See [Configuration Guide](docs/configuration.md) for details.

## Project Structure

```
crypto-market-microstructure/
├── config/           # YAML configuration files
├── src/
│   ├── models/       # Pydantic data models
│   ├── interfaces/   # Abstract base classes
│   ├── adapters/     # Exchange WebSocket clients
│   ├── metrics/      # Metric calculators
│   ├── detection/    # Alert logic
│   └── storage/      # Redis/PostgreSQL clients
├── services/         # Docker service entry points
├── tests/            # Unit and integration tests
└── docs/             # Documentation
```

## Documentation

- [Product Requirements (PRD)](docs/PRD.md) — Full system requirements
- [Agent Architecture](docs/agent-architecture.md) — How the codebase is organized
- [Configuration Guide](docs/configuration.md) — How to configure the system

## Development

This project uses specialized Claude Code agents for development:

| Agent | Responsibility |
|-------|---------------|
| `@architect` | Project structure, interfaces, Docker |
| `@data-engineer` | Exchange adapters, WebSocket clients |
| `@quant` | Metric calculations, z-score logic |
| `@anomaly-detector` | Alert evaluation, persistence |
| `@viz` | Dashboard, charts, UI |

See [CLAUDE.md](CLAUDE.md) for agent usage instructions.

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/unit/test_spread.py
```

## License

MIT

## Acknowledgments

Built as a demonstration of exchange-side market surveillance systems, showcasing:
- Market microstructure knowledge
- Real-time data processing
- Statistical anomaly detection
- Production-grade Python architecture
