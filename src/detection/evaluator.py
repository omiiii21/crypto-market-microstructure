"""
Alert evaluator with dual-condition logic.

This module provides the AlertEvaluator class which evaluates whether
an alert should fire based on dual-condition logic (threshold AND z-score).

Key Features:
    - Dual-condition evaluation (threshold + z-score)
    - Z-score warmup handling (returns skip_reason="zscore_warmup")
    - Persistence condition integration
    - Support for multiple condition types (gt, lt, abs_gt, abs_lt)

Note:
    This module is owned by the ANOMALY-DETECTOR agent.
    Uses Decimal for all financial comparisons - NEVER float.

Example:
    >>> evaluator = AlertEvaluator()
    >>> result = evaluator.evaluate(
    ...     alert_def=alert_definition,
    ...     metric_value=Decimal("3.5"),
    ...     zscore_value=Decimal("2.5"),
    ...     threshold_config=alert_threshold,
    ... )
    >>> if result.triggered:
    ...     print(f"Alert triggered: {result.alert_type}")
"""

from decimal import Decimal
from typing import Optional

import structlog

from src.models.alerts import (
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertResult,
    AlertSeverity,
    AlertThreshold,
)

logger = structlog.get_logger(__name__)


class AlertEvaluator:
    """
    Evaluates alert conditions with dual-condition logic.

    An alert fires ONLY when ALL applicable conditions are met:
    1. Primary threshold condition (gt, lt, abs_gt, abs_lt)
    2. Z-score threshold (if alert_def.requires_zscore=True)
    3. Persistence duration (handled by PersistenceTracker, integrated in AlertManager)

    CRITICAL: If requires_zscore=True but zscore_value=None, the alert is skipped
    with skip_reason="zscore_warmup". This is CORRECT behavior during warmup.

    Attributes:
        None - this is a stateless evaluator.

    Example:
        >>> evaluator = AlertEvaluator()
        >>> result = evaluator.evaluate(
        ...     alert_def=AlertDefinition(
        ...         alert_type="spread_warning",
        ...         name="Spread Warning",
        ...         metric_name="spread_bps",
        ...         default_priority=AlertPriority.P2,
        ...         default_severity=AlertSeverity.WARNING,
        ...         condition=AlertCondition.GT,
        ...         requires_zscore=True,
        ...     ),
        ...     metric_value=Decimal("3.5"),
        ...     zscore_value=Decimal("2.5"),
        ...     threshold_config=AlertThreshold(
        ...         threshold=Decimal("3.0"),
        ...         zscore_threshold=Decimal("2.0"),
        ...     ),
        ... )
    """

    def evaluate(
        self,
        alert_def: AlertDefinition,
        metric_value: Decimal,
        zscore_value: Optional[Decimal],
        threshold_config: AlertThreshold,
    ) -> AlertResult:
        """
        Evaluate whether an alert condition is met.

        Implements dual-condition logic:
        1. Primary threshold must be exceeded
        2. If requires_zscore=True, z-score threshold must also be exceeded
        3. If requires_zscore=True but zscore_value=None, return skip_reason="zscore_warmup"

        Args:
            alert_def: The alert definition with condition type and requirements.
            metric_value: The current metric value (must be Decimal).
            zscore_value: The z-score value (None during warmup period).
            threshold_config: The threshold configuration for this alert.

        Returns:
            AlertResult: Evaluation result with triggered flag and details.

        Raises:
            ValueError: If metric_value is not a Decimal.

        Example:
            >>> result = evaluator.evaluate(alert_def, Decimal("5.0"), Decimal("3.1"), threshold)
            >>> if result.triggered:
            ...     print("Alert fired!")
            >>> elif result.skip_reason == "zscore_warmup":
            ...     print("Skipped - still warming up z-score")
        """
        # Validate input type - must use Decimal for financial precision
        if not isinstance(metric_value, Decimal):
            raise ValueError(
                f"metric_value must be Decimal, got {type(metric_value).__name__}"
            )

        # Check if alert definition is enabled
        if not alert_def.enabled:
            logger.debug(
                "alert_disabled",
                alert_type=alert_def.alert_type,
            )
            return AlertResult(
                triggered=False,
                alert_type=alert_def.alert_type,
                skip_reason="alert_disabled",
                message=f"Alert {alert_def.alert_type} is disabled",
            )

        # Condition 1: Check primary threshold
        threshold_met = alert_def.condition.evaluate(
            value=metric_value,
            threshold=threshold_config.threshold,
        )

        if not threshold_met:
            logger.debug(
                "alert_threshold_not_met",
                alert_type=alert_def.alert_type,
                metric_value=str(metric_value),
                threshold=str(threshold_config.threshold),
                condition=alert_def.condition.value,
            )
            return AlertResult(
                triggered=False,
                alert_type=alert_def.alert_type,
                message=f"Threshold not met: {metric_value} {alert_def.condition.value} {threshold_config.threshold}",
            )

        # Condition 2: Check z-score if required
        if alert_def.requires_zscore:
            # CRITICAL: If z-score is None (warmup), skip the alert
            if zscore_value is None:
                logger.info(
                    "alert_skipped",
                    reason="zscore_warmup",
                    alert_type=alert_def.alert_type,
                    metric_value=str(metric_value),
                )
                return AlertResult(
                    triggered=False,
                    alert_type=alert_def.alert_type,
                    skip_reason="zscore_warmup",
                    message="Z-score not available (warmup period)",
                )

            # Z-score threshold must be set if requires_zscore=True
            if threshold_config.zscore_threshold is None:
                logger.warning(
                    "alert_config_error",
                    alert_type=alert_def.alert_type,
                    error="requires_zscore=True but zscore_threshold not configured",
                )
                return AlertResult(
                    triggered=False,
                    alert_type=alert_def.alert_type,
                    skip_reason="config_error",
                    message="Z-score threshold not configured",
                )

            # Validate z-score type
            if not isinstance(zscore_value, Decimal):
                raise ValueError(
                    f"zscore_value must be Decimal, got {type(zscore_value).__name__}"
                )

            # Check z-score condition (always absolute value comparison)
            zscore_met = abs(zscore_value) > threshold_config.zscore_threshold

            if not zscore_met:
                logger.debug(
                    "alert_zscore_not_met",
                    alert_type=alert_def.alert_type,
                    zscore_value=str(zscore_value),
                    zscore_threshold=str(threshold_config.zscore_threshold),
                )
                return AlertResult(
                    triggered=False,
                    alert_type=alert_def.alert_type,
                    message=f"Z-score not met: |{zscore_value}| <= {threshold_config.zscore_threshold}",
                )

        # All conditions met - alert should trigger
        # Note: Persistence is handled by AlertManager, not the evaluator
        logger.info(
            "alert_condition_met",
            alert_type=alert_def.alert_type,
            metric_value=str(metric_value),
            threshold=str(threshold_config.threshold),
            zscore_value=str(zscore_value) if zscore_value else "N/A",
            zscore_threshold=str(threshold_config.zscore_threshold) if threshold_config.zscore_threshold else "N/A",
        )

        return AlertResult(
            triggered=True,
            alert_type=alert_def.alert_type,
            priority=alert_def.default_priority,
            severity=alert_def.default_severity,
            message=self._build_trigger_message(
                alert_def=alert_def,
                metric_value=metric_value,
                threshold=threshold_config.threshold,
                zscore_value=zscore_value,
                zscore_threshold=threshold_config.zscore_threshold,
            ),
        )

    def evaluate_with_persistence(
        self,
        alert_def: AlertDefinition,
        metric_value: Decimal,
        zscore_value: Optional[Decimal],
        threshold_config: AlertThreshold,
        persistence_met: bool,
    ) -> AlertResult:
        """
        Evaluate alert including persistence condition.

        This method first evaluates the threshold and z-score conditions,
        then checks if persistence requirement is met.

        Args:
            alert_def: The alert definition.
            metric_value: The current metric value.
            zscore_value: The z-score value (None during warmup).
            threshold_config: The threshold configuration.
            persistence_met: Whether the persistence duration has been met.

        Returns:
            AlertResult: Evaluation result with triggered flag and details.

        Example:
            >>> result = evaluator.evaluate_with_persistence(
            ...     alert_def=basis_warning_def,
            ...     metric_value=Decimal("12.0"),
            ...     zscore_value=Decimal("2.5"),
            ...     threshold_config=basis_threshold,
            ...     persistence_met=True,  # Condition has persisted for 120+ seconds
            ... )
        """
        # First evaluate threshold and z-score
        result = self.evaluate(
            alert_def=alert_def,
            metric_value=metric_value,
            zscore_value=zscore_value,
            threshold_config=threshold_config,
        )

        # If threshold/zscore conditions not met, return result as-is
        if not result.triggered:
            return result

        # Check persistence requirement
        if alert_def.has_persistence:
            if not persistence_met:
                logger.info(
                    "alert_persistence_not_met",
                    alert_type=alert_def.alert_type,
                    persistence_required=alert_def.persistence_seconds,
                )
                return AlertResult(
                    triggered=False,
                    alert_type=alert_def.alert_type,
                    message=f"Persistence not met: requires {alert_def.persistence_seconds}s",
                )

        # All conditions met including persistence
        return result

    def _build_trigger_message(
        self,
        alert_def: AlertDefinition,
        metric_value: Decimal,
        threshold: Decimal,
        zscore_value: Optional[Decimal],
        zscore_threshold: Optional[Decimal],
    ) -> str:
        """
        Build a human-readable trigger message.

        Args:
            alert_def: The alert definition.
            metric_value: The metric value that triggered.
            threshold: The threshold that was exceeded.
            zscore_value: The z-score value (if applicable).
            zscore_threshold: The z-score threshold (if applicable).

        Returns:
            str: Formatted trigger message.
        """
        condition_symbol = {
            AlertCondition.GT: ">",
            AlertCondition.LT: "<",
            AlertCondition.ABS_GT: "|x| >",
            AlertCondition.ABS_LT: "|x| <",
        }.get(alert_def.condition, "?")

        message = f"{alert_def.metric_name}: {metric_value} {condition_symbol} {threshold}"

        if alert_def.requires_zscore and zscore_value is not None and zscore_threshold is not None:
            message += f" (z: {zscore_value:.2f} > {zscore_threshold})"

        return message


def create_evaluator() -> AlertEvaluator:
    """
    Factory function to create an AlertEvaluator.

    Returns:
        AlertEvaluator: A new evaluator instance.

    Example:
        >>> evaluator = create_evaluator()
    """
    return AlertEvaluator()
