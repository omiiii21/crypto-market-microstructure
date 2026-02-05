"""
Alert manager for alert lifecycle management.

This module provides the AlertManager class which orchestrates the complete
alert lifecycle: evaluation, triggering, escalation, and resolution.

Key Features:
    - Processes metrics and evaluates all applicable alerts
    - Manages alert lifecycle (trigger, escalate, resolve)
    - Implements throttling to prevent duplicate alerts
    - Implements deduplication for active alerts
    - Integrates with storage for persistence
    - Supports auto-resolution when conditions clear

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> manager = AlertManager(storage, evaluator, tracker, config)
    >>> alerts = await manager.process_metrics(
    ...     exchange="binance",
    ...     instrument="BTC-USDT-PERP",
    ...     metrics=aggregated_metrics,
    ... )
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

import structlog

from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertResult,
    AlertSeverity,
    AlertThreshold,
)
from src.models.metrics import AggregatedMetrics
from src.detection.evaluator import AlertEvaluator
from src.detection.persistence import PersistenceTracker, build_condition_key
from src.detection.storage import AlertStorage

logger = structlog.get_logger(__name__)


# Default configuration values
DEFAULT_THROTTLE_SECONDS = 60
DEFAULT_ESCALATION_SECONDS = 300
DEFAULT_DEDUP_WINDOW_SECONDS = 300


class AlertManager:
    """
    Orchestrates the complete alert lifecycle.

    Responsibilities:
    - Evaluate metrics against all applicable alert definitions
    - Create Alert objects when conditions are met
    - Implement throttling (same alert not repeated within window)
    - Implement deduplication (don't create duplicates for same condition)
    - Track persistence for time-based conditions
    - Escalate P2 alerts to P1 after timeout
    - Auto-resolve alerts when conditions clear

    Attributes:
        storage: AlertStorage for persistence.
        evaluator: AlertEvaluator for condition evaluation.
        persistence_tracker: PersistenceTracker for time-based conditions.
        alert_definitions: Dict of alert type to AlertDefinition.
        thresholds: Dict of instrument to alert thresholds.
        _last_fired: Dict tracking when each alert type last fired.
        _active_conditions: Set of currently active condition keys.

    Example:
        >>> manager = AlertManager(
        ...     storage=storage,
        ...     evaluator=evaluator,
        ...     persistence_tracker=tracker,
        ...     alert_definitions=definitions,
        ...     thresholds=thresholds,
        ... )
        >>> alerts = await manager.process_metrics(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     metrics=metrics,
        ... )
    """

    def __init__(
        self,
        storage: AlertStorage,
        evaluator: AlertEvaluator,
        persistence_tracker: PersistenceTracker,
        alert_definitions: Dict[str, AlertDefinition],
        thresholds: Dict[str, Dict[str, AlertThreshold]],
        global_throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
        escalation_seconds: int = DEFAULT_ESCALATION_SECONDS,
        auto_resolve: bool = True,
    ) -> None:
        """
        Initialize the AlertManager.

        Args:
            storage: AlertStorage for persisting alerts.
            evaluator: AlertEvaluator for condition evaluation.
            persistence_tracker: PersistenceTracker for time-based conditions.
            alert_definitions: Dict mapping alert_type to AlertDefinition.
            thresholds: Dict mapping instrument to alert thresholds.
            global_throttle_seconds: Default throttle window for alerts.
            escalation_seconds: Seconds before P2 escalates to P1.
            auto_resolve: Whether to auto-resolve when conditions clear.

        Example:
            >>> manager = AlertManager(
            ...     storage=storage,
            ...     evaluator=evaluator,
            ...     persistence_tracker=tracker,
            ...     alert_definitions={"spread_warning": spread_def},
            ...     thresholds={"BTC-USDT-PERP": {"spread_warning": threshold}},
            ... )
        """
        self.storage = storage
        self.evaluator = evaluator
        self.persistence_tracker = persistence_tracker
        self.alert_definitions = alert_definitions
        self.thresholds = thresholds
        self.global_throttle_seconds = global_throttle_seconds
        self.escalation_seconds = escalation_seconds
        self.auto_resolve = auto_resolve

        # Throttling: track when each alert type last fired
        self._last_fired: Dict[str, datetime] = {}

        # Deduplication: track active conditions
        self._active_conditions: Set[str] = set()

        logger.info(
            "alert_manager_initialized",
            definitions_count=len(alert_definitions),
            instruments_with_thresholds=list(thresholds.keys()),
            global_throttle_seconds=global_throttle_seconds,
            escalation_seconds=escalation_seconds,
            auto_resolve=auto_resolve,
        )

    async def process_metrics(
        self,
        exchange: str,
        instrument: str,
        metrics: AggregatedMetrics,
        timestamp: Optional[datetime] = None,
    ) -> List[Alert]:
        """
        Process metrics and evaluate all applicable alerts.

        Evaluates each applicable alert definition against the metrics,
        handles persistence tracking, throttling, deduplication, and
        creates Alert objects for triggered conditions.

        Args:
            exchange: Exchange identifier (e.g., "binance").
            instrument: Instrument identifier (e.g., "BTC-USDT-PERP").
            metrics: Aggregated metrics from the metrics engine.
            timestamp: Current timestamp (defaults to utcnow).

        Returns:
            List[Alert]: List of newly triggered alerts.

        Example:
            >>> alerts = await manager.process_metrics(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     metrics=aggregated_metrics,
            ... )
            >>> for alert in alerts:
            ...     print(f"Triggered: {alert.alert_type}")
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        triggered_alerts: List[Alert] = []
        current_conditions: Set[str] = set()

        # Get thresholds for this instrument (or fallback to default)
        instrument_thresholds = self.thresholds.get(
            instrument,
            self.thresholds.get("*", {}),
        )

        # Process each alert definition
        for alert_type, alert_def in self.alert_definitions.items():
            # Skip if no threshold configured for this instrument
            if alert_type not in instrument_thresholds:
                continue

            threshold_config = instrument_thresholds[alert_type]

            # Get metric value for this alert
            metric_value = self._get_metric_value(metrics, alert_def.metric_name)
            if metric_value is None:
                continue

            # Get z-score value (may be None during warmup)
            zscore_value = self._get_zscore_value(metrics, alert_def.metric_name)

            # Build condition key for persistence and deduplication
            condition_key = build_condition_key(alert_type, instrument, exchange)

            # Evaluate the condition (threshold + z-score)
            result = self.evaluator.evaluate(
                alert_def=alert_def,
                metric_value=metric_value,
                zscore_value=zscore_value,
                threshold_config=threshold_config,
            )

            # Track persistence
            self.persistence_tracker.track(
                condition_key=condition_key,
                is_met=result.triggered,
                timestamp=timestamp,
            )

            # If threshold and z-score met, check persistence
            if result.triggered:
                current_conditions.add(condition_key)

                # Check persistence requirement
                if alert_def.has_persistence:
                    persistence_met = self.persistence_tracker.is_persistence_met(
                        condition_key=condition_key,
                        required_seconds=alert_def.persistence_seconds,  # type: ignore
                        current_time=timestamp,
                    )
                    if not persistence_met:
                        logger.debug(
                            "alert_waiting_for_persistence",
                            alert_type=alert_type,
                            condition_key=condition_key,
                            required_seconds=alert_def.persistence_seconds,
                        )
                        continue

                # Check throttling
                if self._should_throttle(alert_def, condition_key, timestamp):
                    logger.info(
                        "alert_throttled",
                        alert_type=alert_type,
                        condition_key=condition_key,
                        last_fired=self._last_fired.get(condition_key, datetime.min).isoformat(),
                    )
                    continue

                # Check deduplication (don't create duplicate for active condition)
                if self._is_duplicate(condition_key):
                    # Update peak value for existing alert
                    await self._update_existing_alert_peak(
                        condition_key=condition_key,
                        metric_value=metric_value,
                        timestamp=timestamp,
                    )
                    continue

                # Create and save new alert
                alert = self._create_alert(
                    alert_def=alert_def,
                    result=result,
                    exchange=exchange,
                    instrument=instrument,
                    metric_value=metric_value,
                    threshold=threshold_config.threshold,
                    zscore_value=zscore_value,
                    zscore_threshold=threshold_config.zscore_threshold,
                    timestamp=timestamp,
                )

                await self.storage.save(alert)
                triggered_alerts.append(alert)

                # Update throttling and deduplication state
                self._last_fired[condition_key] = timestamp
                self._active_conditions.add(condition_key)

                # Clear persistence tracking (alert fired, reset for next occurrence)
                self.persistence_tracker.clear(condition_key)

                logger.info(
                    "alert_triggered",
                    alert_type=alert_type,
                    alert_id=alert.alert_id,
                    priority=alert.priority.value,
                    metric_value=str(metric_value),
                    zscore_value=str(zscore_value) if zscore_value else "N/A",
                )

        # Handle auto-resolution for conditions that cleared
        if self.auto_resolve:
            await self._resolve_cleared_conditions(
                current_conditions=current_conditions,
                exchange=exchange,
                instrument=instrument,
                metrics=metrics,
                timestamp=timestamp,
            )

        return triggered_alerts

    async def check_escalations(
        self,
        timestamp: Optional[datetime] = None,
    ) -> List[Alert]:
        """
        Check for and process P2 to P1 escalations.

        P2 alerts that have been active for longer than escalation_seconds
        are escalated to P1.

        Args:
            timestamp: Current timestamp (defaults to utcnow).

        Returns:
            List[Alert]: List of escalated alerts.

        Example:
            >>> escalated = await manager.check_escalations()
            >>> for alert in escalated:
            ...     print(f"Escalated: {alert.alert_id} to {alert.priority.value}")
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        escalated_alerts: List[Alert] = []

        try:
            # Get P2 alerts eligible for escalation
            eligible = await self.storage.get_alerts_for_escalation_check(
                escalation_threshold_seconds=self.escalation_seconds,
            )

            for alert in eligible:
                # Escalate to P1
                escalated = await self.storage.update_escalation(
                    alert_id=alert.alert_id,
                    new_priority=AlertPriority.P1,
                    escalated_at=timestamp,
                )

                if escalated:
                    escalated_alerts.append(escalated)
                    logger.info(
                        "alert_escalated",
                        alert_id=alert.alert_id,
                        from_priority="P2",
                        to_priority="P1",
                        age_seconds=(timestamp - alert.triggered_at).total_seconds(),
                    )

        except Exception as e:
            logger.error(
                "escalation_check_failed",
                error=str(e),
            )

        return escalated_alerts

    async def resolve_alert(
        self,
        alert_id: str,
        resolution_type: str,
        resolution_value: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None,
    ) -> Optional[Alert]:
        """
        Manually resolve an alert.

        Args:
            alert_id: The unique alert identifier.
            resolution_type: How resolved (auto, manual, timeout).
            resolution_value: Metric value at resolution.
            timestamp: Resolution timestamp (defaults to utcnow).

        Returns:
            Optional[Alert]: The resolved alert, or None if not found.

        Example:
            >>> resolved = await manager.resolve_alert(
            ...     alert_id="abc123",
            ...     resolution_type="manual",
            ... )
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        resolved = await self.storage.update_resolution(
            alert_id=alert_id,
            resolved_at=timestamp,
            resolution_type=resolution_type,
            resolution_value=resolution_value,
        )

        if resolved:
            # Clean up deduplication tracking
            condition_key = self._build_condition_key_from_alert(resolved)
            self._active_conditions.discard(condition_key)

        return resolved

    def _get_metric_value(
        self,
        metrics: AggregatedMetrics,
        metric_name: str,
    ) -> Optional[Decimal]:
        """
        Extract a specific metric value from aggregated metrics.

        Args:
            metrics: The aggregated metrics.
            metric_name: The metric name to extract.

        Returns:
            Optional[Decimal]: The metric value, or None if not available.
        """
        # Map metric names to their locations in AggregatedMetrics
        if metric_name == "spread_bps":
            return metrics.spread.spread_bps
        elif metric_name == "spread_abs":
            return metrics.spread.spread_abs
        elif metric_name == "basis_bps":
            if metrics.basis:
                return metrics.basis.basis_bps
            return None
        elif metric_name == "basis_abs":
            if metrics.basis:
                return metrics.basis.basis_abs
            return None
        elif metric_name == "depth_10bps_total":
            return metrics.depth.depth_10bps_total
        elif metric_name == "depth_5bps_total":
            return metrics.depth.depth_5bps_total
        elif metric_name == "depth_25bps_total":
            return metrics.depth.depth_25bps_total
        elif metric_name == "imbalance":
            return metrics.imbalance.top_of_book_imbalance
        else:
            logger.warning(
                "unknown_metric_name",
                metric_name=metric_name,
            )
            return None

    def _get_zscore_value(
        self,
        metrics: AggregatedMetrics,
        metric_name: str,
    ) -> Optional[Decimal]:
        """
        Extract the z-score for a specific metric.

        Args:
            metrics: The aggregated metrics.
            metric_name: The metric name.

        Returns:
            Optional[Decimal]: The z-score value, or None if not available/warmup.
        """
        if metric_name in ("spread_bps", "spread_abs"):
            return metrics.spread.zscore
        elif metric_name in ("basis_bps", "basis_abs"):
            if metrics.basis:
                return metrics.basis.zscore
            return None
        else:
            # Metrics without z-score
            return None

    def _should_throttle(
        self,
        alert_def: AlertDefinition,
        condition_key: str,
        timestamp: datetime,
    ) -> bool:
        """
        Check if an alert should be throttled.

        Args:
            alert_def: The alert definition.
            condition_key: The condition identifier.
            timestamp: Current timestamp.

        Returns:
            bool: True if alert should be throttled.
        """
        last_fired = self._last_fired.get(condition_key)
        if last_fired is None:
            return False

        throttle_seconds = alert_def.throttle_seconds or self.global_throttle_seconds
        elapsed = (timestamp - last_fired).total_seconds()

        return elapsed < throttle_seconds

    def _is_duplicate(self, condition_key: str) -> bool:
        """
        Check if an alert would be a duplicate of an active alert.

        Args:
            condition_key: The condition identifier.

        Returns:
            bool: True if duplicate of active alert.
        """
        return condition_key in self._active_conditions

    def _create_alert(
        self,
        alert_def: AlertDefinition,
        result: AlertResult,
        exchange: str,
        instrument: str,
        metric_value: Decimal,
        threshold: Decimal,
        zscore_value: Optional[Decimal],
        zscore_threshold: Optional[Decimal],
        timestamp: datetime,
    ) -> Alert:
        """
        Create an Alert object from evaluation result.

        Args:
            alert_def: The alert definition.
            result: The evaluation result.
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric_value: The metric value.
            threshold: The threshold that was exceeded.
            zscore_value: The z-score value.
            zscore_threshold: The z-score threshold.
            timestamp: Trigger timestamp.

        Returns:
            Alert: The new alert object.
        """
        return Alert(
            alert_type=alert_def.alert_type,
            priority=result.priority or alert_def.default_priority,
            severity=result.severity or alert_def.default_severity,
            exchange=exchange,
            instrument=instrument,
            trigger_metric=alert_def.metric_name,
            trigger_value=metric_value,
            trigger_threshold=threshold,
            trigger_condition=alert_def.condition,
            zscore_value=zscore_value,
            zscore_threshold=zscore_threshold,
            triggered_at=timestamp,
            peak_value=metric_value,
            peak_at=timestamp,
        )

    def _build_condition_key_from_alert(self, alert: Alert) -> str:
        """
        Build a condition key from an alert.

        Args:
            alert: The alert.

        Returns:
            str: The condition key.
        """
        return build_condition_key(
            alert_type=alert.alert_type,
            instrument=alert.instrument,
            exchange=alert.exchange,
        )

    async def _update_existing_alert_peak(
        self,
        condition_key: str,
        metric_value: Decimal,
        timestamp: datetime,
    ) -> None:
        """
        Update the peak value for an existing active alert.

        Args:
            condition_key: The condition identifier.
            metric_value: Current metric value.
            timestamp: Current timestamp.
        """
        # Find the active alert for this condition
        active_alerts = await self.storage.get_active_alerts()

        for alert in active_alerts:
            alert_key = self._build_condition_key_from_alert(alert)
            if alert_key == condition_key:
                await self.storage.update_peak(
                    alert_id=alert.alert_id,
                    peak_value=metric_value,
                    peak_at=timestamp,
                )
                break

    async def _resolve_cleared_conditions(
        self,
        current_conditions: Set[str],
        exchange: str,
        instrument: str,
        metrics: AggregatedMetrics,
        timestamp: datetime,
    ) -> None:
        """
        Auto-resolve alerts for conditions that have cleared.

        Args:
            current_conditions: Set of conditions still active.
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metrics: Current metrics (for resolution value).
            timestamp: Current timestamp.
        """
        # Find conditions that were active but are no longer
        cleared = self._active_conditions - current_conditions

        for condition_key in list(cleared):
            # Only process conditions for this exchange:instrument
            if not condition_key.endswith(f":{instrument}:{exchange}"):
                continue

            # Find and resolve the alert
            active_alerts = await self.storage.get_active_alerts()

            for alert in active_alerts:
                alert_key = self._build_condition_key_from_alert(alert)
                if alert_key == condition_key:
                    # Get current metric value for resolution
                    resolution_value = self._get_metric_value(
                        metrics,
                        alert.trigger_metric,
                    )

                    resolved = await self.storage.update_resolution(
                        alert_id=alert.alert_id,
                        resolved_at=timestamp,
                        resolution_type="auto",
                        resolution_value=resolution_value,
                    )

                    if resolved:
                        self._active_conditions.discard(condition_key)
                        logger.info(
                            "alert_auto_resolved",
                            alert_id=alert.alert_id,
                            alert_type=alert.alert_type,
                            duration_seconds=resolved.duration_seconds,
                        )
                    break

    def get_active_condition_count(self) -> int:
        """
        Get the count of active conditions.

        Returns:
            int: Number of active conditions.
        """
        return len(self._active_conditions)

    def clear_throttle_state(self) -> None:
        """
        Clear all throttle state.

        Used for testing or reset scenarios.
        """
        self._last_fired.clear()
        logger.info("throttle_state_cleared")

    def clear_dedup_state(self) -> None:
        """
        Clear all deduplication state.

        Used for testing or reset scenarios.
        """
        self._active_conditions.clear()
        logger.info("dedup_state_cleared")


async def create_alert_manager(
    storage: AlertStorage,
    evaluator: AlertEvaluator,
    persistence_tracker: PersistenceTracker,
    alert_definitions: Dict[str, AlertDefinition],
    thresholds: Dict[str, Dict[str, AlertThreshold]],
    global_throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
    escalation_seconds: int = DEFAULT_ESCALATION_SECONDS,
    auto_resolve: bool = True,
) -> AlertManager:
    """
    Factory function to create an AlertManager.

    Args:
        storage: AlertStorage for persistence.
        evaluator: AlertEvaluator for condition evaluation.
        persistence_tracker: PersistenceTracker for time-based conditions.
        alert_definitions: Dict of alert definitions.
        thresholds: Dict of instrument thresholds.
        global_throttle_seconds: Default throttle window.
        escalation_seconds: Escalation timeout.
        auto_resolve: Whether to auto-resolve.

    Returns:
        AlertManager: A new manager instance.

    Example:
        >>> manager = await create_alert_manager(
        ...     storage=storage,
        ...     evaluator=evaluator,
        ...     persistence_tracker=tracker,
        ...     alert_definitions=definitions,
        ...     thresholds=thresholds,
        ... )
    """
    return AlertManager(
        storage=storage,
        evaluator=evaluator,
        persistence_tracker=persistence_tracker,
        alert_definitions=alert_definitions,
        thresholds=thresholds,
        global_throttle_seconds=global_throttle_seconds,
        escalation_seconds=escalation_seconds,
        auto_resolve=auto_resolve,
    )
