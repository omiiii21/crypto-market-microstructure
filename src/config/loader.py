"""
Configuration loader for YAML-based application configuration.

This module provides utilities to load and validate configuration from YAML
files. All configuration is validated using Pydantic models to ensure type
safety and catch configuration errors early.

Configuration files expected:
    - config/exchanges.yaml: Exchange connection settings
    - config/instruments.yaml: Trading instrument definitions
    - config/alerts.yaml: Alert definitions and thresholds
    - config/features.yaml: Feature flags and system settings

Environment variables override:
    - REDIS_URL: Redis connection URL
    - DATABASE_URL: PostgreSQL connection URL
    - LOG_LEVEL: Application log level

Example:
    >>> from src.config.loader import load_config
    >>> config = load_config("config")
    >>> print(config.get_enabled_exchanges())
    ['binance', 'okx']
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import ValidationError

from src.config.models import (
    AlertDefinitionConfig,
    AlertsConfig,
    AppConfig,
    BackfillConfig,
    BasisPairConfig,
    ChannelConfig,
    ConnectionSettings,
    DashboardConfig,
    DataCaptureConfig,
    ExchangeConfig,
    ExchangeSymbolConfig,
    FeaturesConfig,
    GapHandlingConfig,
    GlobalAlertSettings,
    InstrumentConfig,
    LoggingConfig,
    LogLevel,
    PostgresConnectionConfig,
    PriorityConfig,
    PriorityEscalation,
    RedisConnectionConfig,
    RegimeDetectionConfig,
    RestEndpoints,
    StorageConfig,
    StreamSettings,
    ThresholdValue,
    WebSocketEndpoints,
    ZScoreConfig,
)


class ConfigLoadError(Exception):
    """
    Raised when configuration loading fails.

    Attributes:
        message: Error message describing what went wrong.
        file_path: Path to the file that caused the error, if applicable.
        cause: Original exception that caused the error, if any.
    """

    def __init__(
        self,
        message: str,
        file_path: Optional[Path] = None,
        cause: Optional[Exception] = None,
    ):
        """
        Initialize ConfigLoadError.

        Args:
            message: Error message.
            file_path: Path to the problematic file.
            cause: Original exception.
        """
        self.message = message
        self.file_path = file_path
        self.cause = cause
        super().__init__(message)


class ConfigLoader:
    """
    Loads and validates application configuration from YAML files.

    Expects the following directory structure:
        config/
        ├── exchanges.yaml    - Exchange connections and settings
        ├── instruments.yaml  - Trading instruments and symbols
        ├── alerts.yaml       - Alert definitions and thresholds
        └── features.yaml     - Feature flags and system settings

    Example:
        >>> loader = ConfigLoader("config")
        >>> config = loader.load()
        >>> print(config.exchanges.keys())
        dict_keys(['binance', 'okx'])
    """

    def __init__(self, config_dir: Path | str = "config"):
        """
        Initialize config loader.

        Args:
            config_dir: Path to configuration directory (default: 'config').

        Raises:
            ConfigLoadError: If config directory does not exist.
        """
        self.config_dir = Path(config_dir)
        if not self.config_dir.exists():
            raise ConfigLoadError(
                f"Configuration directory not found: {self.config_dir}",
                file_path=self.config_dir,
            )
        if not self.config_dir.is_dir():
            raise ConfigLoadError(
                f"Configuration path is not a directory: {self.config_dir}",
                file_path=self.config_dir,
            )

    def _load_yaml(self, filename: str) -> Dict[str, Any]:
        """
        Load a YAML file from the config directory.

        Args:
            filename: Name of YAML file (e.g., 'exchanges.yaml').

        Returns:
            Dict containing parsed YAML content.

        Raises:
            ConfigLoadError: If file not found, empty, or invalid YAML.
        """
        file_path = self.config_dir / filename
        if not file_path.exists():
            raise ConfigLoadError(
                f"Configuration file not found: {file_path}",
                file_path=file_path,
            )

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data is None:
                    raise ConfigLoadError(
                        f"Configuration file is empty: {file_path}",
                        file_path=file_path,
                    )
                return data
        except yaml.YAMLError as e:
            raise ConfigLoadError(
                f"Invalid YAML syntax in {file_path}: {e}",
                file_path=file_path,
                cause=e,
            ) from e
        except OSError as e:
            raise ConfigLoadError(
                f"Error reading {file_path}: {e}",
                file_path=file_path,
                cause=e,
            ) from e

    def _load_exchanges(self) -> Dict[str, ExchangeConfig]:
        """
        Load exchange configurations from exchanges.yaml.

        Returns:
            Dict of ExchangeConfig keyed by exchange name.

        Raises:
            ConfigLoadError: If validation fails or no exchanges configured.
        """
        data = self._load_yaml("exchanges.yaml")
        exchanges: Dict[str, ExchangeConfig] = {}

        try:
            raw_exchanges = data.get("exchanges", {})
            for exchange_name, exchange_data in raw_exchanges.items():
                # Parse nested structures
                ws_data = exchange_data.get("websocket", {})
                rest_data = exchange_data.get("rest", {})
                conn_data = exchange_data.get("connection", {})
                stream_data = exchange_data.get("streams", {})

                websocket = WebSocketEndpoints(
                    futures=ws_data.get("futures"),
                    spot=ws_data.get("spot"),
                    public=ws_data.get("public"),
                )

                rest = RestEndpoints(
                    futures=rest_data.get("futures"),
                    spot=rest_data.get("spot"),
                    base=rest_data.get("base"),
                )

                connection = ConnectionSettings(
                    rate_limit_per_second=conn_data.get("rate_limit_per_second", 10),
                    reconnect_delay_seconds=conn_data.get("reconnect_delay_seconds", 5),
                    max_reconnect_attempts=conn_data.get("max_reconnect_attempts", 10),
                    ping_interval_seconds=conn_data.get("ping_interval_seconds", 30),
                    ping_timeout_seconds=conn_data.get("ping_timeout_seconds", 10),
                )

                streams = StreamSettings(
                    orderbook_depth=stream_data.get("orderbook_depth", 20),
                    orderbook_speed=stream_data.get("orderbook_speed", "100ms"),
                    orderbook_channel=stream_data.get("orderbook_channel"),
                )

                exchanges[exchange_name] = ExchangeConfig(
                    enabled=exchange_data.get("enabled", True),
                    websocket=websocket,
                    rest=rest,
                    connection=connection,
                    streams=streams,
                )

        except ValidationError as e:
            raise ConfigLoadError(
                f"Invalid exchange configuration: {e}",
                file_path=self.config_dir / "exchanges.yaml",
                cause=e,
            ) from e

        if not exchanges:
            raise ConfigLoadError(
                "No exchanges configured in exchanges.yaml",
                file_path=self.config_dir / "exchanges.yaml",
            )

        return exchanges

    def _load_instruments(self) -> tuple[List[InstrumentConfig], List[BasisPairConfig]]:
        """
        Load instrument configurations from instruments.yaml.

        Returns:
            Tuple of (instruments list, basis_pairs list).

        Raises:
            ConfigLoadError: If validation fails or no instruments configured.
        """
        data = self._load_yaml("instruments.yaml")
        instruments: List[InstrumentConfig] = []
        basis_pairs: List[BasisPairConfig] = []

        try:
            raw_instruments = data.get("instruments", [])
            for inst_data in raw_instruments:
                # Parse exchange symbols
                exchange_symbols: Dict[str, ExchangeSymbolConfig] = {}
                raw_symbols = inst_data.get("exchange_symbols", {})
                for exchange_name, symbol_data in raw_symbols.items():
                    exchange_symbols[exchange_name] = ExchangeSymbolConfig(
                        symbol=symbol_data.get("symbol", ""),
                        stream=symbol_data.get("stream"),
                        ticker_stream=symbol_data.get("ticker_stream"),
                        mark_price_stream=symbol_data.get("mark_price_stream"),
                        inst_type=symbol_data.get("inst_type"),
                    )

                instruments.append(
                    InstrumentConfig(
                        id=inst_data["id"],
                        name=inst_data["name"],
                        type=inst_data["type"],
                        base=inst_data["base"],
                        quote=inst_data["quote"],
                        enabled=inst_data.get("enabled", True),
                        exchange_symbols=exchange_symbols,
                        depth_levels=inst_data.get("depth_levels", 20),
                    )
                )

            # Parse basis pairs
            raw_pairs = data.get("basis_pairs", [])
            for pair_data in raw_pairs:
                basis_pairs.append(
                    BasisPairConfig(
                        perp=pair_data["perp"],
                        spot=pair_data["spot"],
                    )
                )

        except ValidationError as e:
            raise ConfigLoadError(
                f"Invalid instrument configuration: {e}",
                file_path=self.config_dir / "instruments.yaml",
                cause=e,
            ) from e
        except KeyError as e:
            raise ConfigLoadError(
                f"Missing required field in instrument configuration: {e}",
                file_path=self.config_dir / "instruments.yaml",
                cause=e,
            ) from e

        if not instruments:
            raise ConfigLoadError(
                "No instruments configured in instruments.yaml",
                file_path=self.config_dir / "instruments.yaml",
            )

        return instruments, basis_pairs

    def _load_alerts(self) -> AlertsConfig:
        """
        Load alert configurations from alerts.yaml.

        Returns:
            AlertsConfig object.

        Raises:
            ConfigLoadError: If validation fails.
        """
        data = self._load_yaml("alerts.yaml")

        try:
            # Parse global settings
            global_data = data.get("global", {})
            global_settings = GlobalAlertSettings(
                throttle_seconds=global_data.get("throttle_seconds", 60),
                dedup_window_seconds=global_data.get("dedup_window_seconds", 300),
                auto_resolve=global_data.get("auto_resolve", True),
            )

            # Parse priorities
            priorities: Dict[str, PriorityConfig] = {}
            raw_priorities = data.get("priorities", {})
            for priority_name, priority_data in raw_priorities.items():
                escalation = None
                if "escalation" in priority_data:
                    esc_data = priority_data["escalation"]
                    escalation = PriorityEscalation(
                        to=esc_data["to"],
                        after_seconds=esc_data["after_seconds"],
                    )

                priorities[priority_name] = PriorityConfig(
                    name=priority_data["name"],
                    description=priority_data["description"],
                    channels=priority_data.get("channels", []),
                    escalation=escalation,
                    color=priority_data.get("color", "#000000"),
                )

            # Parse definitions
            definitions: Dict[str, AlertDefinitionConfig] = {}
            raw_definitions = data.get("definitions", {})
            for alert_type, def_data in raw_definitions.items():
                definitions[alert_type] = AlertDefinitionConfig(
                    name=def_data["name"],
                    description=def_data["description"],
                    metric=def_data["metric"],
                    default_priority=def_data["default_priority"],
                    default_severity=def_data["default_severity"],
                    condition=def_data["condition"],
                    requires_zscore=def_data.get("requires_zscore", False),
                    persistence_seconds=def_data.get("persistence_seconds"),
                    throttle_seconds=def_data.get("throttle_seconds", 60),
                    escalates_to=def_data.get("escalates_to"),
                )

            # Parse thresholds
            thresholds: Dict[str, Dict[str, ThresholdValue]] = {}
            raw_thresholds = data.get("thresholds", {})
            for instrument, alert_thresholds in raw_thresholds.items():
                thresholds[instrument] = {}
                for alert_type, threshold_data in alert_thresholds.items():
                    thresholds[instrument][alert_type] = ThresholdValue(
                        threshold=threshold_data["threshold"],
                        zscore=threshold_data.get("zscore"),
                    )

            # Parse channels
            channels: Dict[str, ChannelConfig] = {}
            raw_channels = data.get("channels", {})
            for channel_name, channel_data in raw_channels.items():
                channels[channel_name] = ChannelConfig(
                    enabled=channel_data.get("enabled", True),
                    format=channel_data.get("format", "structured"),
                    webhook_url=channel_data.get("webhook_url"),
                    channel=channel_data.get("channel"),
                    username=channel_data.get("username"),
                    icon_emoji=channel_data.get("icon_emoji"),
                )

            return AlertsConfig(
                global_settings=global_settings,
                priorities=priorities,
                definitions=definitions,
                thresholds=thresholds,
                channels=channels,
            )

        except ValidationError as e:
            raise ConfigLoadError(
                f"Invalid alerts configuration: {e}",
                file_path=self.config_dir / "alerts.yaml",
                cause=e,
            ) from e
        except KeyError as e:
            raise ConfigLoadError(
                f"Missing required field in alerts configuration: {e}",
                file_path=self.config_dir / "alerts.yaml",
                cause=e,
            ) from e

    def _load_features(self) -> FeaturesConfig:
        """
        Load feature flags from features.yaml.

        Returns:
            FeaturesConfig object.

        Raises:
            ConfigLoadError: If validation fails.
        """
        data = self._load_yaml("features.yaml")

        try:
            # Parse zscore config
            zscore_data = data.get("zscore", {})
            zscore = ZScoreConfig(
                enabled=zscore_data.get("enabled", True),
                window_size=zscore_data.get("window_size", 300),
                min_samples=zscore_data.get("min_samples", 30),
                min_std=zscore_data.get("min_std", 0.0001),
                warmup_log_interval=zscore_data.get("warmup_log_interval", 10),
                reset_on_gap=zscore_data.get("reset_on_gap", True),
                reset_on_gap_threshold=zscore_data.get("reset_on_gap_threshold", 5),
            )

            # Parse gap handling
            gap_data = data.get("gap_handling", {})
            gap_handling = GapHandlingConfig(
                mark_gaps=gap_data.get("mark_gaps", True),
                gap_threshold_seconds=gap_data.get("gap_threshold_seconds", 5),
                interpolate=gap_data.get("interpolate", False),
                alert_on_gap=gap_data.get("alert_on_gap", True),
                track_sequence_ids=gap_data.get("track_sequence_ids", True),
            )

            # Parse regime detection
            regime_data = data.get("regime_detection", {})
            regime_detection = RegimeDetectionConfig(
                enabled=regime_data.get("enabled", False),
            )

            # Parse backfill
            backfill_data = data.get("backfill", {})
            backfill = BackfillConfig(
                enabled=backfill_data.get("enabled", False),
            )

            # Parse data capture
            capture_data = data.get("data_capture", {})
            data_capture = DataCaptureConfig(
                realtime_interval_ms=capture_data.get("realtime_interval_ms", 100),
                storage_interval_seconds=capture_data.get("storage_interval_seconds", 1),
                depth_levels=capture_data.get("depth_levels", 20),
            )

            # Parse storage
            storage_data = data.get("storage", {})
            redis_storage_data = storage_data.get("redis", {})
            postgres_storage_data = storage_data.get("postgres", {})

            from src.config.models import (
                PostgresStorageConfig,
                RedisStorageConfig,
            )

            storage = StorageConfig(
                redis=RedisStorageConfig(
                    current_state_ttl_seconds=redis_storage_data.get(
                        "current_state_ttl_seconds", 60
                    ),
                    zscore_buffer_ttl_seconds=redis_storage_data.get(
                        "zscore_buffer_ttl_seconds", 600
                    ),
                    alert_dedup_ttl_seconds=redis_storage_data.get(
                        "alert_dedup_ttl_seconds", 60
                    ),
                ),
                postgres=PostgresStorageConfig(
                    snapshot_retention_days=postgres_storage_data.get(
                        "snapshot_retention_days", 30
                    ),
                    metrics_retention_days=postgres_storage_data.get(
                        "metrics_retention_days", 90
                    ),
                    alerts_retention_days=postgres_storage_data.get(
                        "alerts_retention_days", 365
                    ),
                    compress_after_days=postgres_storage_data.get(
                        "compress_after_days", 7
                    ),
                ),
            )

            # Parse dashboard
            dashboard_data = data.get("dashboard", {})
            dashboard = DashboardConfig(
                current_state_refresh_ms=dashboard_data.get(
                    "current_state_refresh_ms", 1000
                ),
                charts_refresh_ms=dashboard_data.get("charts_refresh_ms", 5000),
                alerts_refresh_ms=dashboard_data.get("alerts_refresh_ms", 1000),
                health_refresh_ms=dashboard_data.get("health_refresh_ms", 1000),
                default_time_range=dashboard_data.get("default_time_range", "1h"),
                default_exchange=dashboard_data.get("default_exchange", "all"),
                max_data_points=dashboard_data.get("max_data_points", 3600),
            )

            # Parse logging
            logging_data = data.get("logging", {})
            logging_config = LoggingConfig(
                format=logging_data.get("format", "json"),
                level=logging_data.get("level", "INFO"),
            )

            return FeaturesConfig(
                zscore=zscore,
                gap_handling=gap_handling,
                regime_detection=regime_detection,
                backfill=backfill,
                data_capture=data_capture,
                storage=storage,
                dashboard=dashboard,
                logging=logging_config,
            )

        except ValidationError as e:
            raise ConfigLoadError(
                f"Invalid features configuration: {e}",
                file_path=self.config_dir / "features.yaml",
                cause=e,
            ) from e

    def _load_redis_connection(self) -> RedisConnectionConfig:
        """
        Load Redis connection configuration from environment.

        Environment variables:
            - REDIS_URL: Redis connection URL (default: redis://localhost:6379)

        Returns:
            RedisConnectionConfig object.
        """
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        return RedisConnectionConfig(url=redis_url)

    def _load_postgres_connection(self) -> PostgresConnectionConfig:
        """
        Load PostgreSQL connection configuration from environment.

        Environment variables:
            - DATABASE_URL: PostgreSQL connection URL

        Returns:
            PostgresConnectionConfig object.
        """
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://surveillance:password@localhost:5432/surveillance",
        )
        return PostgresConnectionConfig(url=db_url)

    def _get_log_level(self) -> LogLevel:
        """
        Get log level from environment.

        Environment variables:
            - LOG_LEVEL: Log level (default: INFO)

        Returns:
            LogLevel enum value.
        """
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        try:
            return LogLevel(level_str)
        except ValueError:
            return LogLevel.INFO

    def load(self) -> AppConfig:
        """
        Load and validate all configuration files.

        This is the main entry point for loading configuration. It loads
        all YAML files, merges environment variables, and returns a fully
        validated AppConfig object.

        Returns:
            AppConfig: Validated application configuration.

        Raises:
            ConfigLoadError: If any configuration is invalid or missing.

        Example:
            >>> loader = ConfigLoader("config")
            >>> config = loader.load()
            >>> print(config.get_enabled_exchanges())
        """
        try:
            exchanges = self._load_exchanges()
            instruments, basis_pairs = self._load_instruments()
            alerts = self._load_alerts()
            features = self._load_features()
            redis = self._load_redis_connection()
            postgres = self._load_postgres_connection()
            log_level = self._get_log_level()

            config = AppConfig(
                exchanges=exchanges,
                instruments=instruments,
                basis_pairs=basis_pairs,
                alerts=alerts,
                features=features,
                redis=redis,
                postgres=postgres,
                log_level=log_level,
            )

            return config

        except ConfigLoadError:
            raise
        except ValidationError as e:
            raise ConfigLoadError(
                f"Configuration validation failed: {e}",
                cause=e,
            ) from e
        except Exception as e:
            raise ConfigLoadError(
                f"Unexpected error loading configuration: {e}",
                cause=e,
            ) from e


def load_config(config_dir: Path | str = "config") -> AppConfig:
    """
    Convenience function to load application configuration.

    This is the recommended way to load configuration in application code.

    Args:
        config_dir: Path to configuration directory (default: 'config').

    Returns:
        AppConfig: Validated application configuration.

    Raises:
        ConfigLoadError: If configuration loading fails.

    Example:
        >>> from src.config import load_config
        >>> config = load_config()
        >>> threshold = config.alerts.get_threshold("BTC-USDT-PERP", "spread_warning")
        >>> print(f"Spread threshold: {threshold.threshold} bps")
    """
    loader = ConfigLoader(config_dir)
    return loader.load()
