"""
Shared Pydantic data models for the surveillance system.

This module exports all data models used throughout the system.
All models use Decimal for financial precision.

Modules:
    orderbook: Order book snapshots and price levels
    ticker: Ticker and trade snapshots
    metrics: Computed market quality metrics
    alerts: Alert definitions, thresholds, and instances
    health: System health and connection status

Example:
    >>> from src.models import OrderBookSnapshot, PriceLevel, SpreadMetrics
    >>> from src.models import Alert, AlertPriority, AlertSeverity
"""

# Order book models
from src.models.orderbook import (
    OrderBookSnapshot,
    PriceLevel,
)

# Ticker models
from src.models.ticker import (
    TickerSnapshot,
    TradeSnapshot,
    TradeSide,
)

# Metrics models
from src.models.metrics import (
    AggregatedMetrics,
    BasisMetrics,
    CrossExchangeMetrics,
    DepthMetrics,
    ImbalanceMetrics,
    SpreadMetrics,
)

# Alert models
from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertResult,
    AlertSeverity,
    AlertThreshold,
)

# Health models
from src.models.health import (
    ConnectionStatus,
    GapMarker,
    HealthStatus,
    SystemHealthSummary,
    ZScoreWarmupStatus,
)

__all__ = [
    # Order book
    "PriceLevel",
    "OrderBookSnapshot",
    # Ticker
    "TickerSnapshot",
    "TradeSnapshot",
    "TradeSide",
    # Metrics
    "SpreadMetrics",
    "DepthMetrics",
    "BasisMetrics",
    "ImbalanceMetrics",
    "CrossExchangeMetrics",
    "AggregatedMetrics",
    # Alerts
    "AlertPriority",
    "AlertSeverity",
    "AlertCondition",
    "AlertDefinition",
    "AlertThreshold",
    "AlertResult",
    "Alert",
    # Health
    "ConnectionStatus",
    "GapMarker",
    "HealthStatus",
    "ZScoreWarmupStatus",
    "SystemHealthSummary",
]
