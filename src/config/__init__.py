"""
Configuration management for the surveillance system.

This module handles loading and validating configuration from YAML files.
All configuration values are validated using Pydantic models to ensure
type safety and catch configuration errors early.

The configuration system supports:
- Exchange connections (WebSocket, REST)
- Instrument definitions and symbol mappings
- Alert definitions and per-instrument thresholds
- Feature flags (z-score, gap handling, etc.)
- Database and storage settings

Configuration is loaded from YAML files in the config/ directory:
    - exchanges.yaml: Exchange connection settings
    - instruments.yaml: Trading instrument definitions
    - alerts.yaml: Alert definitions and thresholds
    - features.yaml: Feature flags and system settings

Environment variables can override connection settings:
    - REDIS_URL: Redis connection URL
    - DATABASE_URL: PostgreSQL connection URL
    - LOG_LEVEL: Application log level

Example:
    >>> from src.config import load_config, AppConfig
    >>> config = load_config()
    >>> threshold = config.alerts.get_threshold("BTC-USDT-PERP", "spread_warning")
    >>> if threshold:
    ...     print(f"Spread warning threshold: {threshold.threshold} bps")

Modules:
    loader: Configuration file loading utilities
    models: Pydantic models for configuration validation
"""

from src.config.loader import ConfigLoadError, ConfigLoader, load_config
from src.config.models import (
    # Enums
    AlertCondition,
    AlertPriority,
    AlertSeverity,
    InstrumentType,
    LogFormat,
    LogLevel,
    # Exchange config
    ConnectionSettings,
    ExchangeConfig,
    RestEndpoints,
    StreamSettings,
    WebSocketEndpoints,
    # Instrument config
    BasisPairConfig,
    ExchangeSymbolConfig,
    InstrumentConfig,
    # Alert config
    AlertDefinitionConfig,
    AlertsConfig,
    ChannelConfig,
    GlobalAlertSettings,
    PriorityConfig,
    PriorityEscalation,
    ThresholdValue,
    # Features config
    BackfillConfig,
    DashboardConfig,
    DataCaptureConfig,
    FeaturesConfig,
    GapHandlingConfig,
    LoggingConfig,
    PostgresStorageConfig,
    RedisStorageConfig,
    RegimeDetectionConfig,
    StorageConfig,
    ZScoreConfig,
    # Connection config
    PostgresConnectionConfig,
    RedisConnectionConfig,
    # Root config
    AppConfig,
)

__all__: list[str] = [
    # Loader
    "load_config",
    "ConfigLoader",
    "ConfigLoadError",
    # Enums
    "AlertCondition",
    "AlertPriority",
    "AlertSeverity",
    "InstrumentType",
    "LogFormat",
    "LogLevel",
    # Exchange config
    "WebSocketEndpoints",
    "RestEndpoints",
    "ConnectionSettings",
    "StreamSettings",
    "ExchangeConfig",
    # Instrument config
    "ExchangeSymbolConfig",
    "InstrumentConfig",
    "BasisPairConfig",
    # Alert config
    "GlobalAlertSettings",
    "PriorityEscalation",
    "PriorityConfig",
    "AlertDefinitionConfig",
    "ThresholdValue",
    "ChannelConfig",
    "AlertsConfig",
    # Features config
    "ZScoreConfig",
    "GapHandlingConfig",
    "RegimeDetectionConfig",
    "BackfillConfig",
    "DataCaptureConfig",
    "RedisStorageConfig",
    "PostgresStorageConfig",
    "StorageConfig",
    "DashboardConfig",
    "LoggingConfig",
    "FeaturesConfig",
    # Connection config
    "RedisConnectionConfig",
    "PostgresConnectionConfig",
    # Root config
    "AppConfig",
]
