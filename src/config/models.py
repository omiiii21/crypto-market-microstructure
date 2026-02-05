"""
Pydantic models for application configuration.

This module defines all configuration models that are validated when loading
YAML configuration files. The models ensure type safety and provide sensible
defaults for optional settings.

All financial values use Decimal for precision to avoid floating-point errors.

Configuration files:
    - config/exchanges.yaml: Exchange connection settings
    - config/instruments.yaml: Trading instrument definitions
    - config/alerts.yaml: Alert definitions and thresholds
    - config/features.yaml: Feature flags and system settings

Example:
    >>> from src.config.models import AppConfig
    >>> config = AppConfig(...)
    >>> threshold = config.alerts.get_threshold("BTC-USDT-PERP", "spread_warning")
"""

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# ENUMS
# =============================================================================


class InstrumentType(str, Enum):
    """Type of trading instrument."""

    PERPETUAL = "perpetual"
    SPOT = "spot"
    FUTURES = "futures"


class AlertCondition(str, Enum):
    """Comparison conditions for alert evaluation."""

    GT = "gt"  # Greater than
    LT = "lt"  # Less than
    ABS_GT = "abs_gt"  # Absolute value greater than
    ABS_LT = "abs_lt"  # Absolute value less than


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertPriority(str, Enum):
    """Alert priority levels."""

    P1 = "P1"  # Critical - immediate action
    P2 = "P2"  # Warning - investigate soon
    P3 = "P3"  # Info - awareness only


class LogFormat(str, Enum):
    """Logging format options."""

    JSON = "json"
    TEXT = "text"


class LogLevel(str, Enum):
    """Logging level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# =============================================================================
# EXCHANGE CONFIGURATION
# =============================================================================


class WebSocketEndpoints(BaseModel):
    """WebSocket endpoint URLs for an exchange."""

    model_config = {"frozen": True, "extra": "forbid"}

    futures: Optional[str] = Field(
        default=None,
        description="WebSocket URL for futures/perpetual streams",
    )
    spot: Optional[str] = Field(
        default=None,
        description="WebSocket URL for spot streams",
    )
    public: Optional[str] = Field(
        default=None,
        description="WebSocket URL for public streams (single endpoint)",
    )


class RestEndpoints(BaseModel):
    """REST API endpoint URLs for an exchange."""

    model_config = {"frozen": True, "extra": "forbid"}

    futures: Optional[str] = Field(
        default=None,
        description="REST API base URL for futures",
    )
    spot: Optional[str] = Field(
        default=None,
        description="REST API base URL for spot",
    )
    base: Optional[str] = Field(
        default=None,
        description="REST API base URL (single endpoint)",
    )


class ConnectionSettings(BaseModel):
    """Connection settings for an exchange."""

    model_config = {"frozen": True, "extra": "forbid"}

    rate_limit_per_second: int = Field(
        default=10,
        description="Maximum REST requests per second",
        ge=1,
        le=100,
    )
    reconnect_delay_seconds: int = Field(
        default=5,
        description="Delay before reconnection attempt",
        ge=1,
        le=60,
    )
    max_reconnect_attempts: int = Field(
        default=10,
        description="Maximum reconnection attempts before failure",
        ge=1,
        le=100,
    )
    ping_interval_seconds: int = Field(
        default=30,
        description="WebSocket ping interval",
        ge=5,
        le=120,
    )
    ping_timeout_seconds: int = Field(
        default=10,
        description="WebSocket ping timeout",
        ge=1,
        le=60,
    )


class StreamSettings(BaseModel):
    """Stream configuration for an exchange."""

    model_config = {"frozen": True, "extra": "forbid"}

    orderbook_depth: int = Field(
        default=20,
        description="Number of order book levels to capture",
        ge=5,
        le=100,
    )
    orderbook_speed: str = Field(
        default="100ms",
        description="Order book update speed",
    )
    orderbook_channel: Optional[str] = Field(
        default=None,
        description="Order book channel name (OKX)",
    )


class ExchangeConfig(BaseModel):
    """Configuration for a single exchange."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="Whether this exchange is enabled",
    )
    websocket: WebSocketEndpoints = Field(
        default_factory=WebSocketEndpoints,
        description="WebSocket endpoint configuration",
    )
    rest: RestEndpoints = Field(
        default_factory=RestEndpoints,
        description="REST API endpoint configuration",
    )
    connection: ConnectionSettings = Field(
        default_factory=ConnectionSettings,
        description="Connection settings",
    )
    streams: StreamSettings = Field(
        default_factory=StreamSettings,
        description="Stream configuration",
    )

    def get_websocket_url(self, market_type: str = "futures") -> Optional[str]:
        """
        Get the WebSocket URL for a market type.

        Args:
            market_type: "futures", "spot", or "public"

        Returns:
            Optional[str]: WebSocket URL or None if not configured.
        """
        if market_type == "futures":
            return self.websocket.futures or self.websocket.public
        elif market_type == "spot":
            return self.websocket.spot or self.websocket.public
        return self.websocket.public

    def get_rest_url(self, market_type: str = "futures") -> Optional[str]:
        """
        Get the REST API URL for a market type.

        Args:
            market_type: "futures", "spot", or "base"

        Returns:
            Optional[str]: REST URL or None if not configured.
        """
        if market_type == "futures":
            return self.rest.futures or self.rest.base
        elif market_type == "spot":
            return self.rest.spot or self.rest.base
        return self.rest.base


# =============================================================================
# INSTRUMENT CONFIGURATION
# =============================================================================


class ExchangeSymbolConfig(BaseModel):
    """Exchange-specific symbol configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    symbol: str = Field(
        ...,
        description="Exchange-specific symbol",
    )
    stream: Optional[str] = Field(
        default=None,
        description="Stream name for order book (Binance)",
    )
    ticker_stream: Optional[str] = Field(
        default=None,
        description="Stream name for ticker (Binance)",
    )
    mark_price_stream: Optional[str] = Field(
        default=None,
        description="Stream name for mark price (Binance)",
    )
    inst_type: Optional[str] = Field(
        default=None,
        description="Instrument type (OKX: SPOT, SWAP)",
    )


class InstrumentConfig(BaseModel):
    """Configuration for a trading instrument."""

    model_config = {"frozen": True, "extra": "forbid"}

    id: str = Field(
        ...,
        description="Normalized instrument ID (e.g., BTC-USDT-PERP)",
        min_length=1,
        max_length=50,
    )
    name: str = Field(
        ...,
        description="Human-readable instrument name",
    )
    type: InstrumentType = Field(
        ...,
        description="Instrument type (perpetual, spot)",
    )
    base: str = Field(
        ...,
        description="Base currency (e.g., BTC)",
        min_length=1,
        max_length=10,
    )
    quote: str = Field(
        ...,
        description="Quote currency (e.g., USDT)",
        min_length=1,
        max_length=10,
    )
    enabled: bool = Field(
        default=True,
        description="Whether this instrument is enabled",
    )
    exchange_symbols: Dict[str, ExchangeSymbolConfig] = Field(
        default_factory=dict,
        description="Exchange-specific symbol mappings",
    )
    depth_levels: int = Field(
        default=20,
        description="Number of order book levels to capture",
        ge=5,
        le=100,
    )

    @property
    def is_perpetual(self) -> bool:
        """Check if this is a perpetual instrument."""
        return self.type == InstrumentType.PERPETUAL

    @property
    def is_spot(self) -> bool:
        """Check if this is a spot instrument."""
        return self.type == InstrumentType.SPOT

    def get_exchange_symbol(self, exchange: str) -> Optional[ExchangeSymbolConfig]:
        """
        Get exchange-specific symbol configuration.

        Args:
            exchange: Exchange name (e.g., "binance", "okx")

        Returns:
            Optional[ExchangeSymbolConfig]: Symbol config or None if not found.
        """
        return self.exchange_symbols.get(exchange)


class BasisPairConfig(BaseModel):
    """Configuration for basis calculation pair (perp/spot)."""

    model_config = {"frozen": True, "extra": "forbid"}

    perp: str = Field(
        ...,
        description="Perpetual instrument ID",
    )
    spot: str = Field(
        ...,
        description="Spot instrument ID",
    )


# =============================================================================
# ALERT CONFIGURATION
# =============================================================================


class GlobalAlertSettings(BaseModel):
    """Global alert behavior settings."""

    model_config = {"frozen": True, "extra": "forbid"}

    throttle_seconds: int = Field(
        default=60,
        description="Minimum seconds between same alert type",
        ge=0,
        le=3600,
    )
    dedup_window_seconds: int = Field(
        default=300,
        description="Alert deduplication window in seconds",
        ge=0,
        le=3600,
    )
    auto_resolve: bool = Field(
        default=True,
        description="Automatically resolve alerts when condition clears",
    )


class PriorityEscalation(BaseModel):
    """Escalation configuration for a priority level."""

    model_config = {"frozen": True, "extra": "forbid"}

    to: AlertPriority = Field(
        ...,
        description="Priority to escalate to",
    )
    after_seconds: int = Field(
        ...,
        description="Seconds before escalation",
        ge=0,
    )


class PriorityConfig(BaseModel):
    """Configuration for an alert priority level."""

    model_config = {"frozen": True, "extra": "forbid"}

    name: str = Field(
        ...,
        description="Human-readable priority name",
    )
    description: str = Field(
        ...,
        description="Priority description",
    )
    channels: List[str] = Field(
        default_factory=list,
        description="Notification channels for this priority",
    )
    escalation: Optional[PriorityEscalation] = Field(
        default=None,
        description="Escalation configuration",
    )
    color: str = Field(
        default="#000000",
        description="Display color for this priority",
    )


class AlertDefinitionConfig(BaseModel):
    """Definition of an alert type."""

    model_config = {"frozen": True, "extra": "forbid"}

    name: str = Field(
        ...,
        description="Human-readable alert name",
    )
    description: str = Field(
        ...,
        description="Alert description",
    )
    metric: str = Field(
        ...,
        description="Metric this alert monitors",
    )
    default_priority: AlertPriority = Field(
        ...,
        description="Default priority when triggered",
    )
    default_severity: AlertSeverity = Field(
        ...,
        description="Default severity when triggered",
    )
    condition: AlertCondition = Field(
        ...,
        description="Comparison condition",
    )
    requires_zscore: bool = Field(
        default=False,
        description="Whether z-score threshold must also be met",
    )
    persistence_seconds: Optional[int] = Field(
        default=None,
        description="Seconds condition must persist before alerting",
        ge=0,
    )
    throttle_seconds: int = Field(
        default=60,
        description="Minimum seconds between repeated alerts",
        ge=0,
    )
    escalates_to: Optional[str] = Field(
        default=None,
        description="Alert type to escalate to",
    )


class ThresholdValue(BaseModel):
    """Threshold configuration for a specific alert type and instrument."""

    model_config = {"frozen": True, "extra": "forbid"}

    threshold: Decimal = Field(
        ...,
        description="Primary metric threshold value",
    )
    zscore: Optional[Decimal] = Field(
        default=None,
        description="Z-score threshold (if dual-condition)",
        ge=Decimal("0"),
    )

    @field_validator("threshold", mode="before")
    @classmethod
    def coerce_threshold(cls, v: Any) -> Decimal:
        """Convert threshold to Decimal."""
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v

    @field_validator("zscore", mode="before")
    @classmethod
    def coerce_zscore(cls, v: Any) -> Optional[Decimal]:
        """Convert zscore to Decimal."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class ChannelConfig(BaseModel):
    """Configuration for a notification channel."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="Whether this channel is enabled",
    )
    format: str = Field(
        default="structured",
        description="Output format (structured, simple)",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Webhook URL for external channels",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Channel name (e.g., Slack channel)",
    )
    username: Optional[str] = Field(
        default=None,
        description="Bot username",
    )
    icon_emoji: Optional[str] = Field(
        default=None,
        description="Bot icon emoji",
    )


class AlertsConfig(BaseModel):
    """Complete alerts configuration."""

    model_config = {"frozen": True, "extra": "forbid", "populate_by_name": True}

    global_settings: GlobalAlertSettings = Field(
        default_factory=GlobalAlertSettings,
        description="Global alert settings",
        alias="global",
    )
    priorities: Dict[str, PriorityConfig] = Field(
        default_factory=dict,
        description="Priority level definitions",
    )
    definitions: Dict[str, AlertDefinitionConfig] = Field(
        default_factory=dict,
        description="Alert type definitions",
    )
    thresholds: Dict[str, Dict[str, ThresholdValue]] = Field(
        default_factory=dict,
        description="Per-instrument alert thresholds",
    )
    channels: Dict[str, ChannelConfig] = Field(
        default_factory=dict,
        description="Notification channel configurations",
    )

    def get_threshold(
        self, instrument: str, alert_type: str
    ) -> Optional[ThresholdValue]:
        """
        Get threshold for an instrument and alert type.

        Falls back to default ("*") if instrument-specific threshold not found.

        Args:
            instrument: Instrument ID (e.g., "BTC-USDT-PERP")
            alert_type: Alert type (e.g., "spread_warning")

        Returns:
            Optional[ThresholdValue]: Threshold config or None if not found.
        """
        # Try instrument-specific threshold
        if instrument in self.thresholds:
            if alert_type in self.thresholds[instrument]:
                return self.thresholds[instrument][alert_type]

        # Fall back to default
        if "*" in self.thresholds:
            if alert_type in self.thresholds["*"]:
                return self.thresholds["*"][alert_type]

        return None

    def get_definition(self, alert_type: str) -> Optional[AlertDefinitionConfig]:
        """
        Get alert definition by type.

        Args:
            alert_type: Alert type (e.g., "spread_warning")

        Returns:
            Optional[AlertDefinitionConfig]: Definition or None if not found.
        """
        return self.definitions.get(alert_type)


# =============================================================================
# FEATURES CONFIGURATION
# =============================================================================


class ZScoreConfig(BaseModel):
    """Z-score calculation configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="Enable z-score calculations",
    )
    window_size: int = Field(
        default=300,
        description="Rolling window size (samples)",
        ge=30,
        le=3600,
    )
    min_samples: int = Field(
        default=30,
        description="Minimum samples before z-score is valid (warmup)",
        ge=10,
        le=300,
    )
    min_std: Decimal = Field(
        default=Decimal("0.0001"),
        description="Minimum std deviation to avoid divide-by-zero",
        ge=Decimal("0"),
    )
    warmup_log_interval: int = Field(
        default=10,
        description="Log warmup progress every N seconds",
        ge=1,
        le=60,
    )
    reset_on_gap: bool = Field(
        default=True,
        description="Reset z-score buffer when gap detected",
    )
    reset_on_gap_threshold: int = Field(
        default=5,
        description="Only reset if gap exceeds N seconds",
        ge=1,
    )

    @field_validator("min_std", mode="before")
    @classmethod
    def coerce_min_std(cls, v: Any) -> Decimal:
        """Convert min_std to Decimal."""
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class GapHandlingConfig(BaseModel):
    """Gap detection and handling configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    mark_gaps: bool = Field(
        default=True,
        description="Insert gap markers in database",
    )
    gap_threshold_seconds: int = Field(
        default=5,
        description="Minimum gap duration to record",
        ge=1,
    )
    interpolate: bool = Field(
        default=False,
        description="Never interpolate (fabricated data is bad)",
    )
    alert_on_gap: bool = Field(
        default=True,
        description="Generate P3 alert for gaps",
    )
    track_sequence_ids: bool = Field(
        default=True,
        description="Use exchange sequence IDs for gap detection",
    )


class RegimeDetectionConfig(BaseModel):
    """Regime detection configuration (future feature)."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = Field(
        default=False,
        description="Enable regime detection (Phase 2)",
    )


class BackfillConfig(BaseModel):
    """Data backfill configuration (future feature)."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = Field(
        default=False,
        description="Enable data backfill (Phase 2)",
    )


class DataCaptureConfig(BaseModel):
    """Data capture configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    realtime_interval_ms: int = Field(
        default=100,
        description="Process data every N milliseconds",
        ge=10,
        le=1000,
    )
    storage_interval_seconds: int = Field(
        default=1,
        description="Store to PostgreSQL every N seconds",
        ge=1,
        le=60,
    )
    depth_levels: int = Field(
        default=20,
        description="Number of order book levels to capture",
        ge=5,
        le=100,
    )


class RedisStorageConfig(BaseModel):
    """Redis storage configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    current_state_ttl_seconds: int = Field(
        default=60,
        description="TTL for current state data",
        ge=10,
    )
    zscore_buffer_ttl_seconds: int = Field(
        default=600,
        description="TTL for z-score rolling buffer",
        ge=60,
    )
    alert_dedup_ttl_seconds: int = Field(
        default=60,
        description="TTL for alert deduplication",
        ge=10,
    )


class PostgresStorageConfig(BaseModel):
    """PostgreSQL/TimescaleDB storage configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    snapshot_retention_days: int = Field(
        default=30,
        description="Retention period for order book snapshots",
        ge=1,
    )
    metrics_retention_days: int = Field(
        default=90,
        description="Retention period for computed metrics",
        ge=1,
    )
    alerts_retention_days: int = Field(
        default=365,
        description="Retention period for alerts",
        ge=1,
    )
    compress_after_days: int = Field(
        default=7,
        description="Compress data after N days",
        ge=1,
    )


class StorageConfig(BaseModel):
    """Storage configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    redis: RedisStorageConfig = Field(
        default_factory=RedisStorageConfig,
        description="Redis storage settings",
    )
    postgres: PostgresStorageConfig = Field(
        default_factory=PostgresStorageConfig,
        description="PostgreSQL storage settings",
    )


class DashboardConfig(BaseModel):
    """Dashboard configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    current_state_refresh_ms: int = Field(
        default=1000,
        description="Current state refresh interval",
        ge=100,
    )
    charts_refresh_ms: int = Field(
        default=5000,
        description="Charts refresh interval",
        ge=1000,
    )
    alerts_refresh_ms: int = Field(
        default=1000,
        description="Alerts refresh interval",
        ge=100,
    )
    health_refresh_ms: int = Field(
        default=1000,
        description="Health panel refresh interval",
        ge=100,
    )
    default_time_range: str = Field(
        default="1h",
        description="Default time range for charts",
    )
    default_exchange: str = Field(
        default="all",
        description="Default exchange filter",
    )
    max_data_points: int = Field(
        default=3600,
        description="Maximum data points per chart",
        ge=100,
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    format: LogFormat = Field(
        default=LogFormat.JSON,
        description="Log output format",
    )
    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Default log level",
    )


class FeaturesConfig(BaseModel):
    """Complete features configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    zscore: ZScoreConfig = Field(
        default_factory=ZScoreConfig,
        description="Z-score configuration",
    )
    gap_handling: GapHandlingConfig = Field(
        default_factory=GapHandlingConfig,
        description="Gap handling configuration",
    )
    regime_detection: RegimeDetectionConfig = Field(
        default_factory=RegimeDetectionConfig,
        description="Regime detection configuration",
    )
    backfill: BackfillConfig = Field(
        default_factory=BackfillConfig,
        description="Backfill configuration",
    )
    data_capture: DataCaptureConfig = Field(
        default_factory=DataCaptureConfig,
        description="Data capture configuration",
    )
    storage: StorageConfig = Field(
        default_factory=StorageConfig,
        description="Storage configuration",
    )
    dashboard: DashboardConfig = Field(
        default_factory=DashboardConfig,
        description="Dashboard configuration",
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description="Logging configuration",
    )


# =============================================================================
# CONNECTION CONFIGURATION (from environment)
# =============================================================================


class RedisConnectionConfig(BaseModel):
    """Redis connection configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )
    db: int = Field(
        default=0,
        description="Redis database number",
        ge=0,
    )
    max_connections: int = Field(
        default=10,
        description="Maximum connection pool size",
        ge=1,
    )
    socket_timeout: int = Field(
        default=5,
        description="Socket timeout in seconds",
        ge=1,
    )


class PostgresConnectionConfig(BaseModel):
    """PostgreSQL connection configuration."""

    model_config = {"frozen": True, "extra": "forbid"}

    url: str = Field(
        default="postgresql://surveillance:password@localhost:5432/surveillance",
        description="PostgreSQL connection URL",
    )
    pool_size: int = Field(
        default=5,
        description="Connection pool size",
        ge=1,
    )
    max_overflow: int = Field(
        default=10,
        description="Maximum overflow connections",
        ge=0,
    )
    pool_timeout: int = Field(
        default=30,
        description="Pool timeout in seconds",
        ge=1,
    )


# =============================================================================
# ROOT CONFIGURATION
# =============================================================================


class AppConfig(BaseModel):
    """
    Root application configuration.

    Aggregates all configuration sections into a single validated object.
    This is the main configuration object used throughout the application.

    Example:
        >>> config = AppConfig(...)
        >>> threshold = config.alerts.get_threshold("BTC-USDT-PERP", "spread_warning")
        >>> if threshold:
        ...     print(f"Spread warning threshold: {threshold.threshold} bps")
    """

    model_config = {"frozen": True, "extra": "forbid"}

    exchanges: Dict[str, ExchangeConfig] = Field(
        ...,
        description="Exchange configurations keyed by name",
    )
    instruments: List[InstrumentConfig] = Field(
        ...,
        description="Instrument configurations",
    )
    basis_pairs: List[BasisPairConfig] = Field(
        default_factory=list,
        description="Basis calculation pairs (perp/spot)",
    )
    alerts: AlertsConfig = Field(
        ...,
        description="Alert configurations",
    )
    features: FeaturesConfig = Field(
        ...,
        description="Feature flags and settings",
    )
    redis: RedisConnectionConfig = Field(
        default_factory=RedisConnectionConfig,
        description="Redis connection config",
    )
    postgres: PostgresConnectionConfig = Field(
        default_factory=PostgresConnectionConfig,
        description="PostgreSQL connection config",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Application log level",
    )

    @model_validator(mode="after")
    def validate_config(self) -> "AppConfig":
        """Validate cross-references in configuration."""
        # Validate that all basis pair instruments exist
        instrument_ids = {inst.id for inst in self.instruments}
        for pair in self.basis_pairs:
            if pair.perp not in instrument_ids:
                raise ValueError(f"Basis pair references unknown perp: {pair.perp}")
            if pair.spot not in instrument_ids:
                raise ValueError(f"Basis pair references unknown spot: {pair.spot}")

        return self

    def get_exchange(self, name: str) -> Optional[ExchangeConfig]:
        """
        Get exchange configuration by name.

        Args:
            name: Exchange name (e.g., "binance")

        Returns:
            Optional[ExchangeConfig]: Exchange config or None if not found.
        """
        return self.exchanges.get(name)

    def get_instrument(self, instrument_id: str) -> Optional[InstrumentConfig]:
        """
        Get instrument configuration by ID.

        Args:
            instrument_id: Instrument ID (e.g., "BTC-USDT-PERP")

        Returns:
            Optional[InstrumentConfig]: Instrument config or None if not found.
        """
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                return instrument
        return None

    def get_enabled_exchanges(self) -> List[str]:
        """
        Get list of enabled exchange names.

        Returns:
            List[str]: Names of enabled exchanges.
        """
        return [name for name, config in self.exchanges.items() if config.enabled]

    def get_enabled_instruments(self) -> List[InstrumentConfig]:
        """
        Get list of enabled instruments.

        Returns:
            List[InstrumentConfig]: Enabled instrument configurations.
        """
        return [inst for inst in self.instruments if inst.enabled]

    def get_spot_for_perp(self, perp_id: str) -> Optional[str]:
        """
        Get the spot instrument ID for basis calculation.

        Args:
            perp_id: Perpetual instrument ID

        Returns:
            Optional[str]: Spot instrument ID or None if no pair defined.
        """
        for pair in self.basis_pairs:
            if pair.perp == perp_id:
                return pair.spot
        return None
