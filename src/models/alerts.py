"""
Alert data models for the surveillance system.

This module defines alert-related structures including definitions,
thresholds, evaluation results, and active alert instances.

Models:
    AlertPriority: Priority levels (P1, P2, P3)
    AlertSeverity: Severity levels (critical, warning, info)
    AlertCondition: Comparison conditions (gt, lt, abs_gt, abs_lt)
    AlertDefinition: Alert type configuration
    AlertThreshold: Per-instrument threshold configuration
    AlertResult: Result of alert evaluation
    Alert: Active or historical alert instance
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class AlertPriority(str, Enum):
    """
    Alert priority levels.

    Attributes:
        P1: Critical - Immediate action required.
        P2: Warning - Investigate soon, may escalate to P1.
        P3: Info - Awareness only, no action required.
    """

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def is_critical(self) -> bool:
        """Check if this is a critical priority."""
        return self == AlertPriority.P1

    @property
    def is_actionable(self) -> bool:
        """Check if this priority requires action (P1 or P2)."""
        return self in (AlertPriority.P1, AlertPriority.P2)


class AlertSeverity(str, Enum):
    """
    Alert severity levels.

    Attributes:
        CRITICAL: Severe condition requiring immediate attention.
        WARNING: Elevated condition requiring investigation.
        INFO: Informational, no immediate concern.
    """

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertCondition(str, Enum):
    """
    Comparison conditions for alert evaluation.

    Attributes:
        GT: Greater than (metric > threshold).
        LT: Less than (metric < threshold).
        ABS_GT: Absolute value greater than (|metric| > threshold).
        ABS_LT: Absolute value less than (|metric| < threshold).
    """

    GT = "gt"
    LT = "lt"
    ABS_GT = "abs_gt"
    ABS_LT = "abs_lt"

    def evaluate(self, value: Decimal, threshold: Decimal) -> bool:
        """
        Evaluate the condition.

        Args:
            value: The metric value to check.
            threshold: The threshold to compare against.

        Returns:
            bool: True if condition is met.
        """
        if self == AlertCondition.GT:
            return value > threshold
        elif self == AlertCondition.LT:
            return value < threshold
        elif self == AlertCondition.ABS_GT:
            return abs(value) > threshold
        elif self == AlertCondition.ABS_LT:
            return abs(value) < threshold
        return False


class AlertDefinition(BaseModel):
    """
    Alert type definition.

    Defines the behavior and requirements for a specific alert type.
    This is configuration-driven and loaded from alerts.yaml.

    Attributes:
        alert_type: Unique identifier for this alert type.
        name: Human-readable name.
        metric_name: The metric this alert monitors.
        default_priority: Default priority when triggered.
        default_severity: Default severity when triggered.
        condition: Comparison condition (gt, lt, abs_gt, abs_lt).
        requires_zscore: Whether z-score must also exceed threshold.
        persistence_seconds: Seconds condition must persist before alerting.
        throttle_seconds: Minimum seconds between repeated alerts.
        escalation_seconds: Seconds before escalating (P2 to P1).
        escalates_to: Alert type to escalate to.
        enabled: Whether this alert type is active.

    Example:
        >>> definition = AlertDefinition(
        ...     alert_type="spread_warning",
        ...     name="Spread Warning",
        ...     metric_name="spread_bps",
        ...     default_priority=AlertPriority.P2,
        ...     default_severity=AlertSeverity.WARNING,
        ...     condition=AlertCondition.GT,
        ...     requires_zscore=True,
        ...     throttle_seconds=60,
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    alert_type: str = Field(
        ...,
        description="Unique identifier for this alert type",
        min_length=1,
        max_length=100,
    )
    name: str = Field(
        ...,
        description="Human-readable name",
        min_length=1,
        max_length=200,
    )
    metric_name: str = Field(
        ...,
        description="The metric this alert monitors",
        min_length=1,
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
        description="Comparison condition for evaluation",
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
        description="Minimum seconds between repeated alerts of this type",
        ge=0,
    )
    escalation_seconds: Optional[int] = Field(
        default=None,
        description="Seconds before escalating (e.g., P2 to P1)",
        ge=0,
    )
    escalates_to: Optional[str] = Field(
        default=None,
        description="Alert type to escalate to",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this alert type is active",
    )

    @property
    def has_persistence(self) -> bool:
        """Check if this alert requires persistence."""
        return self.persistence_seconds is not None and self.persistence_seconds > 0

    @property
    def can_escalate(self) -> bool:
        """Check if this alert can escalate."""
        return self.escalates_to is not None


class AlertThreshold(BaseModel):
    """
    Per-instrument threshold configuration.

    Defines the specific thresholds for an alert type on a given instrument.

    Attributes:
        threshold: Primary metric threshold value.
        zscore_threshold: Z-score threshold (if requires_zscore is True).

    Example:
        >>> threshold = AlertThreshold(
        ...     threshold=Decimal("3.0"),
        ...     zscore_threshold=Decimal("2.0"),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    threshold: Decimal = Field(
        ...,
        description="Primary metric threshold value",
    )
    zscore_threshold: Optional[Decimal] = Field(
        default=None,
        description="Z-score threshold (required if alert requires_zscore is True)",
        ge=Decimal("0"),
    )


class AlertResult(BaseModel):
    """
    Result of alert evaluation.

    Returned by the alert evaluator indicating whether an alert should fire.

    Attributes:
        triggered: Whether the alert condition was met.
        alert_type: The alert type that was evaluated.
        priority: The priority if triggered.
        severity: The severity if triggered.
        skip_reason: Reason the alert was skipped (e.g., "zscore_warmup").
        message: Optional message with details.

    Example:
        >>> result = AlertResult(
        ...     triggered=True,
        ...     alert_type="spread_warning",
        ...     priority=AlertPriority.P2,
        ...     severity=AlertSeverity.WARNING,
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    triggered: bool = Field(
        ...,
        description="Whether the alert condition was met",
    )
    alert_type: Optional[str] = Field(
        default=None,
        description="The alert type that was evaluated",
    )
    priority: Optional[AlertPriority] = Field(
        default=None,
        description="The priority if triggered",
    )
    severity: Optional[AlertSeverity] = Field(
        default=None,
        description="The severity if triggered",
    )
    skip_reason: Optional[str] = Field(
        default=None,
        description="Reason the alert was skipped (e.g., 'zscore_warmup', 'throttled')",
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional message with details",
    )

    @property
    def was_skipped(self) -> bool:
        """Check if the alert was skipped (not triggered, but not due to condition)."""
        return not self.triggered and self.skip_reason is not None


class Alert(BaseModel):
    """
    Active or historical alert instance.

    Represents a specific alert occurrence with full context and lifecycle tracking.

    Attributes:
        alert_id: Unique identifier for this alert instance.
        alert_type: The type of alert (references AlertDefinition).
        priority: Current priority (may change due to escalation).
        severity: Alert severity.
        exchange: Exchange where the alert occurred.
        instrument: Instrument that triggered the alert.
        trigger_metric: Name of the metric that triggered.
        trigger_value: Value of the metric when triggered.
        trigger_threshold: Threshold that was exceeded.
        trigger_condition: Condition that was evaluated.
        zscore_value: Z-score value when triggered (if applicable).
        zscore_threshold: Z-score threshold (if applicable).
        triggered_at: When the alert was triggered.
        acknowledged_at: When the alert was acknowledged (if applicable).
        resolved_at: When the alert was resolved (if applicable).
        duration_seconds: How long the alert was active.
        peak_value: Peak metric value during alert.
        peak_at: When peak value occurred.
        escalated: Whether the alert was escalated.
        escalated_at: When escalation occurred.
        original_priority: Priority before escalation.
        context: Additional context data.
        resolution_type: How the alert was resolved.
        resolution_value: Metric value when resolved.

    Example:
        >>> alert = Alert(
        ...     alert_type="spread_warning",
        ...     priority=AlertPriority.P2,
        ...     severity=AlertSeverity.WARNING,
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     trigger_metric="spread_bps",
        ...     trigger_value=Decimal("3.5"),
        ...     trigger_threshold=Decimal("3.0"),
        ...     trigger_condition=AlertCondition.GT,
        ...     triggered_at=datetime.utcnow(),
        ... )
    """

    model_config = {"extra": "forbid"}

    # Identification
    alert_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this alert instance",
    )
    alert_type: str = Field(
        ...,
        description="The type of alert (references AlertDefinition)",
    )

    # Classification
    priority: AlertPriority = Field(
        ...,
        description="Current priority (may change due to escalation)",
    )
    severity: AlertSeverity = Field(
        ...,
        description="Alert severity",
    )

    # Location
    exchange: str = Field(
        ...,
        description="Exchange where the alert occurred",
    )
    instrument: str = Field(
        ...,
        description="Instrument that triggered the alert",
    )

    # Primary trigger
    trigger_metric: str = Field(
        ...,
        description="Name of the metric that triggered",
    )
    trigger_value: Decimal = Field(
        ...,
        description="Value of the metric when triggered",
    )
    trigger_threshold: Decimal = Field(
        ...,
        description="Threshold that was exceeded",
    )
    trigger_condition: AlertCondition = Field(
        ...,
        description="Condition that was evaluated",
    )

    # Z-score (optional)
    zscore_value: Optional[Decimal] = Field(
        default=None,
        description="Z-score value when triggered (if applicable)",
    )
    zscore_threshold: Optional[Decimal] = Field(
        default=None,
        description="Z-score threshold (if applicable)",
    )

    # Lifecycle timestamps
    triggered_at: datetime = Field(
        ...,
        description="When the alert was triggered",
    )
    acknowledged_at: Optional[datetime] = Field(
        default=None,
        description="When the alert was acknowledged",
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        description="When the alert was resolved",
    )

    # Duration tracking
    duration_seconds: Optional[int] = Field(
        default=None,
        description="How long the alert was active (seconds)",
        ge=0,
    )

    # Peak tracking
    peak_value: Optional[Decimal] = Field(
        default=None,
        description="Peak metric value during alert",
    )
    peak_at: Optional[datetime] = Field(
        default=None,
        description="When peak value occurred",
    )

    # Escalation
    escalated: bool = Field(
        default=False,
        description="Whether the alert was escalated",
    )
    escalated_at: Optional[datetime] = Field(
        default=None,
        description="When escalation occurred",
    )
    original_priority: Optional[AlertPriority] = Field(
        default=None,
        description="Priority before escalation",
    )

    # Context
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context data",
    )

    # Resolution
    resolution_type: Optional[str] = Field(
        default=None,
        description="How the alert was resolved (auto, manual, timeout)",
    )
    resolution_value: Optional[Decimal] = Field(
        default=None,
        description="Metric value when resolved",
    )

    @property
    def is_active(self) -> bool:
        """Check if the alert is currently active (not resolved)."""
        return self.resolved_at is None

    @property
    def is_acknowledged(self) -> bool:
        """Check if the alert has been acknowledged."""
        return self.acknowledged_at is not None

    @property
    def is_escalated(self) -> bool:
        """Check if the alert has been escalated."""
        return self.escalated

    def acknowledge(self, timestamp: Optional[datetime] = None) -> "Alert":
        """
        Mark the alert as acknowledged.

        Args:
            timestamp: Acknowledgment time, defaults to now.

        Returns:
            Alert: Updated alert with acknowledgment.
        """
        return self.model_copy(
            update={"acknowledged_at": timestamp or datetime.utcnow()}
        )

    def resolve(
        self,
        resolution_type: str,
        resolution_value: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None,
    ) -> "Alert":
        """
        Resolve the alert.

        Args:
            resolution_type: How resolved (auto, manual, timeout).
            resolution_value: Metric value when resolved.
            timestamp: Resolution time, defaults to now.

        Returns:
            Alert: Updated alert with resolution.
        """
        resolved_time = timestamp or datetime.utcnow()
        duration = int((resolved_time - self.triggered_at).total_seconds())

        return self.model_copy(
            update={
                "resolved_at": resolved_time,
                "resolution_type": resolution_type,
                "resolution_value": resolution_value,
                "duration_seconds": duration,
            }
        )

    def escalate(
        self,
        new_priority: AlertPriority,
        timestamp: Optional[datetime] = None,
    ) -> "Alert":
        """
        Escalate the alert to a higher priority.

        Args:
            new_priority: The new priority level.
            timestamp: Escalation time, defaults to now.

        Returns:
            Alert: Updated alert with escalation.
        """
        return self.model_copy(
            update={
                "escalated": True,
                "escalated_at": timestamp or datetime.utcnow(),
                "original_priority": self.priority,
                "priority": new_priority,
            }
        )

    def update_peak(
        self,
        value: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> "Alert":
        """
        Update peak value if new value is higher (for gt conditions) or lower (for lt).

        Args:
            value: Current metric value.
            timestamp: Time of measurement.

        Returns:
            Alert: Updated alert with peak if changed.
        """
        should_update = False
        if self.peak_value is None:
            should_update = True
        elif self.trigger_condition in (AlertCondition.GT, AlertCondition.ABS_GT):
            should_update = abs(value) > abs(self.peak_value)
        elif self.trigger_condition in (AlertCondition.LT, AlertCondition.ABS_LT):
            should_update = abs(value) < abs(self.peak_value)

        if should_update:
            return self.model_copy(
                update={
                    "peak_value": value,
                    "peak_at": timestamp or datetime.utcnow(),
                }
            )
        return self
