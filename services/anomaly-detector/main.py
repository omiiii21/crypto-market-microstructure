"""
Anomaly Detector Service entry point.

This service is responsible for:
- Subscribing to Redis pub/sub for metric updates
- Evaluating all alert conditions (dual-condition logic)
- Creating and resolving alerts as needed
- Dispatching alerts to notification channels
- Checking escalations every 30 seconds
- Auto-resolving cleared conditions

Usage:
    python -m services.anomaly-detector.main

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DATABASE_URL: PostgreSQL connection URL
    LOG_LEVEL: Logging level (default: INFO)
    CONFIG_PATH: Path to config directory (default: config)
    SLACK_WEBHOOK_URL: Slack webhook URL for notifications (optional)
    SLACK_CHANNEL: Slack channel for notifications (default: #market-ops)

Note:
    This module is owned by the ARCHITECT agent for integration.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config.models import AppConfig, AlertDefinitionConfig, ThresholdValue
from src.detection.dispatcher import ChannelDispatcher, create_dispatcher
from src.detection.evaluator import AlertEvaluator, create_evaluator
from src.detection.manager import AlertManager
from src.detection.persistence import PersistenceTracker, create_persistence_tracker
from src.detection.storage import AlertStorage, create_alert_storage
from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertSeverity,
    AlertThreshold,
)
from src.models.metrics import AggregatedMetrics, SpreadMetrics, DepthMetrics, BasisMetrics
from src.services import ServiceRunner, setup_logging
from src.storage.postgres_client import PostgresClient
from src.storage.redis_client import RedisClient

logger = structlog.get_logger(__name__)


# Redis pub/sub channel for metrics updates
CHANNEL_METRICS = "updates:metrics"

# Escalation check interval in seconds
ESCALATION_CHECK_INTERVAL = 30


class AnomalyDetectorService(ServiceRunner):
    """
    Anomaly detection service for alert evaluation and dispatch.

    Subscribes to metric updates, evaluates alert conditions, manages
    alert lifecycle, and dispatches notifications.

    Attributes:
        alert_storage: Storage for alert persistence.
        alert_evaluator: Evaluator for condition checking.
        persistence_tracker: Tracker for time-based conditions.
        alert_manager: Manager for alert lifecycle.
        dispatcher: Channel dispatcher for notifications.
    """

    def __init__(self, config_path: str = "config") -> None:
        """Initialize the anomaly detector service."""
        super().__init__(config_path)
        self.alert_storage: Optional[AlertStorage] = None
        self.alert_evaluator: Optional[AlertEvaluator] = None
        self.persistence_tracker: Optional[PersistenceTracker] = None
        self.alert_manager: Optional[AlertManager] = None
        self.dispatcher: Optional[ChannelDispatcher] = None
        self._alert_definitions: Dict[str, AlertDefinition] = {}
        self._thresholds: Dict[str, Dict[str, AlertThreshold]] = {}
        self._escalation_task: Optional[asyncio.Task] = None

    @property
    def service_name(self) -> str:
        """Return service name."""
        return "anomaly-detector"

    async def _initialize(self) -> None:
        """Initialize alert components."""
        if self.config is None or self.redis_client is None or self.postgres_client is None:
            raise RuntimeError("Service not properly initialized")

        # Load alert definitions from config
        self._load_alert_definitions()

        # Create alert storage
        self.alert_storage = await create_alert_storage(
            redis_client=self.redis_client,
            postgres_client=self.postgres_client,
        )

        # Create evaluator
        self.alert_evaluator = create_evaluator()

        # Create persistence tracker
        self.persistence_tracker = create_persistence_tracker()

        # Create alert manager
        self.alert_manager = AlertManager(
            storage=self.alert_storage,
            evaluator=self.alert_evaluator,
            persistence_tracker=self.persistence_tracker,
            alert_definitions=self._alert_definitions,
            thresholds=self._thresholds,
            global_throttle_seconds=self.config.alerts.global_settings.throttle_seconds,
            escalation_seconds=300,  # 5 minutes for P2 -> P1 escalation
            auto_resolve=self.config.alerts.global_settings.auto_resolve,
        )

        # Create dispatcher with channels
        slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
        slack_channel = os.getenv("SLACK_CHANNEL", "#market-ops")

        self.dispatcher = await create_dispatcher(
            console_format="structured",
            console_colors=True,
            slack_webhook_url=slack_webhook,
            slack_channel=slack_channel,
            slack_enabled=slack_webhook is not None,
        )

        self.logger.info(
            "alert_components_initialized",
            definitions=list(self._alert_definitions.keys()),
            instruments_with_thresholds=list(self._thresholds.keys()),
            slack_enabled=slack_webhook is not None,
        )

    def _load_alert_definitions(self) -> None:
        """Load alert definitions from configuration."""
        if self.config is None:
            return

        # Convert config definitions to AlertDefinition models
        for alert_type, def_config in self.config.alerts.definitions.items():
            self._alert_definitions[alert_type] = AlertDefinition(
                alert_type=alert_type,
                name=def_config.name,
                metric_name=def_config.metric,
                default_priority=AlertPriority(def_config.default_priority),
                default_severity=AlertSeverity(def_config.default_severity),
                condition=AlertCondition(def_config.condition),
                requires_zscore=def_config.requires_zscore,
                persistence_seconds=def_config.persistence_seconds,
                throttle_seconds=def_config.throttle_seconds,
            )

        # Convert config thresholds to AlertThreshold models
        for instrument, alert_thresholds in self.config.alerts.thresholds.items():
            self._thresholds[instrument] = {}
            for alert_type, threshold_config in alert_thresholds.items():
                self._thresholds[instrument][alert_type] = AlertThreshold(
                    threshold=threshold_config.threshold,
                    zscore_threshold=threshold_config.zscore,
                )

    async def _run(self) -> None:
        """Main service loop - subscribe to metrics and process alerts."""
        if self.redis_client is None or self.alert_manager is None:
            raise RuntimeError("Service not properly initialized")

        # Start escalation check task
        self._escalation_task = asyncio.create_task(self._escalation_loop())

        # Subscribe to metrics updates
        try:
            async with self.redis_client.subscribe([CHANNEL_METRICS]) as messages:
                async for message in messages:
                    if self.shutdown_event.is_set():
                        break

                    try:
                        await self._process_metrics(message)
                    except Exception as e:
                        self.logger.error(
                            "metrics_processing_error",
                            error=str(e),
                        )

        except asyncio.CancelledError:
            self.logger.info("pubsub_cancelled")

        # Cancel escalation task
        if self._escalation_task:
            self._escalation_task.cancel()
            try:
                await self._escalation_task
            except asyncio.CancelledError:
                pass

    async def _process_metrics(self, message: Dict[str, Any]) -> None:
        """
        Process a metrics update message.

        Args:
            message: Pub/sub message containing metrics data.
        """
        if self.config is None or self.alert_manager is None or self.dispatcher is None:
            return

        channel = message.get("channel")
        data = message.get("data", {})

        if channel != CHANNEL_METRICS:
            return

        exchange = data.get("exchange")
        instrument = data.get("instrument")
        timestamp_str = data.get("timestamp")

        if not exchange or not instrument:
            return

        # Parse timestamp
        timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.utcnow()

        # Build AggregatedMetrics from message data
        metrics = self._build_metrics_from_data(data, exchange, instrument, timestamp)

        # Process through alert manager
        triggered_alerts = await self.alert_manager.process_metrics(
            exchange=exchange,
            instrument=instrument,
            metrics=metrics,
            timestamp=timestamp,
        )

        # Dispatch any triggered alerts
        for alert in triggered_alerts:
            try:
                count = await self.dispatcher.dispatch(alert)
                self.logger.info(
                    "alert_dispatched",
                    alert_id=alert.alert_id,
                    alert_type=alert.alert_type,
                    priority=alert.priority.value,
                    channels_notified=count,
                )
            except Exception as e:
                self.logger.error(
                    "alert_dispatch_error",
                    alert_id=alert.alert_id,
                    error=str(e),
                )

    def _build_metrics_from_data(
        self,
        data: Dict[str, Any],
        exchange: str,
        instrument: str,
        timestamp: datetime,
    ) -> AggregatedMetrics:
        """
        Build AggregatedMetrics from pub/sub message data.

        Args:
            data: Message data dictionary.
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            timestamp: Metrics timestamp.

        Returns:
            AggregatedMetrics: Constructed metrics object.
        """
        from src.models.metrics import ImbalanceMetrics

        # Extract spread metrics
        spread_bps_str = data.get("spread_bps", "0")
        spread_zscore_str = data.get("spread_zscore")

        spread = SpreadMetrics(
            spread_abs=Decimal("0"),
            spread_bps=Decimal(spread_bps_str),
            mid_price=Decimal("0"),
            zscore=Decimal(spread_zscore_str) if spread_zscore_str else None,
        )

        # Extract depth metrics
        depth_10bps_str = data.get("depth_10bps_total", "0")

        depth = DepthMetrics(
            depth_5bps_bid=Decimal("0"),
            depth_5bps_ask=Decimal("0"),
            depth_5bps_total=Decimal("0"),
            depth_10bps_bid=Decimal("0"),
            depth_10bps_ask=Decimal("0"),
            depth_10bps_total=Decimal(depth_10bps_str),
            depth_25bps_bid=Decimal("0"),
            depth_25bps_ask=Decimal("0"),
            depth_25bps_total=Decimal("0"),
            imbalance=Decimal("0"),
        )

        # Extract basis metrics (if available)
        basis: Optional[BasisMetrics] = None
        basis_bps_str = data.get("basis_bps")
        basis_zscore_str = data.get("basis_zscore")

        if basis_bps_str is not None:
            basis = BasisMetrics(
                perp_mid=Decimal("0"),
                spot_mid=Decimal("0"),
                basis_abs=Decimal("0"),
                basis_bps=Decimal(basis_bps_str),
                zscore=Decimal(basis_zscore_str) if basis_zscore_str else None,
            )

        # Create imbalance metrics (minimal for alert evaluation)
        imbalance = ImbalanceMetrics(
            top_of_book_imbalance=Decimal("0"),
            weighted_imbalance_5=Decimal("0"),
            weighted_imbalance_10=Decimal("0"),
        )

        return AggregatedMetrics(
            exchange=exchange,
            instrument=instrument,
            timestamp=timestamp,
            spread=spread,
            depth=depth,
            basis=basis,
            imbalance=imbalance,
        )

    async def _escalation_loop(self) -> None:
        """Periodically check for and process alert escalations."""
        try:
            while not self.shutdown_event.is_set():
                await asyncio.sleep(ESCALATION_CHECK_INTERVAL)

                if self.alert_manager is None or self.dispatcher is None:
                    continue

                try:
                    # Check for escalations
                    escalated_alerts = await self.alert_manager.check_escalations()

                    # Dispatch escalation notifications
                    for alert in escalated_alerts:
                        try:
                            count = await self.dispatcher.dispatch_escalation(alert)
                            self.logger.info(
                                "escalation_dispatched",
                                alert_id=alert.alert_id,
                                alert_type=alert.alert_type,
                                new_priority=alert.priority.value,
                                channels_notified=count,
                            )
                        except Exception as e:
                            self.logger.error(
                                "escalation_dispatch_error",
                                alert_id=alert.alert_id,
                                error=str(e),
                            )

                except Exception as e:
                    self.logger.error(
                        "escalation_check_error",
                        error=str(e),
                    )

        except asyncio.CancelledError:
            self.logger.debug("escalation_loop_cancelled")

    async def _cleanup(self) -> None:
        """Service-specific cleanup."""
        # Log final state
        if self.alert_manager is not None:
            active_count = self.alert_manager.get_active_condition_count()
            self.logger.info(
                "cleanup_state",
                active_conditions=active_count,
            )


async def main() -> None:
    """Main entry point."""
    # Set up initial logging
    setup_logging()

    config_path = os.getenv("CONFIG_PATH", "config")

    logger.info(
        "anomaly_detector_service_starting",
        version="1.0.0",
        config_path=config_path,
    )

    service = AnomalyDetectorService(config_path=config_path)

    try:
        await service.run()
    except Exception as e:
        logger.error("service_failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
