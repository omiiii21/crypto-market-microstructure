"""
Health and status models for the surveillance system.

This module defines health-related structures for monitoring system
and connection status, as well as data gap tracking.

Models:
    ConnectionStatus: Connection state enumeration
    GapMarker: Data gap record
    HealthStatus: Exchange/connection health metrics
    ZScoreWarmupStatus: Z-score warmup progress
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ConnectionStatus(str, Enum):
    """
    WebSocket connection status.

    Attributes:
        CONNECTED: Connection is active and healthy.
        DISCONNECTED: Connection is closed.
        DEGRADED: Connection is up but experiencing issues.
        RECONNECTING: Connection lost, attempting to reconnect.
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"

    @property
    def is_healthy(self) -> bool:
        """Check if connection is in a healthy state."""
        return self == ConnectionStatus.CONNECTED

    @property
    def is_usable(self) -> bool:
        """Check if connection can still receive data."""
        return self in (ConnectionStatus.CONNECTED, ConnectionStatus.DEGRADED)


class GapMarker(BaseModel):
    """
    Marks a period of missing data.

    Created when data gaps are detected (WebSocket disconnection, sequence gaps).
    Used for audit trail and to exclude gap periods from analysis.

    Attributes:
        exchange: Exchange identifier.
        instrument: Instrument that had the gap.
        gap_start: Timestamp of last good data.
        gap_end: Timestamp when data resumed.
        duration_seconds: Gap duration in seconds.
        reason: Reason for the gap.
        sequence_id_before: Last known sequence ID before gap.
        sequence_id_after: First sequence ID after gap.

    Example:
        >>> gap = GapMarker(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     gap_start=datetime(2025, 1, 26, 12, 0, 0),
        ...     gap_end=datetime(2025, 1, 26, 12, 0, 45),
        ...     duration_seconds=Decimal("45.0"),
        ...     reason="websocket_disconnect",
        ...     sequence_id_before=12345678,
        ...     sequence_id_after=12345700,
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    exchange: str = Field(
        ...,
        description="Exchange identifier",
        min_length=1,
        max_length=50,
    )
    instrument: str = Field(
        ...,
        description="Instrument that had the gap",
        min_length=1,
        max_length=50,
    )
    gap_start: datetime = Field(
        ...,
        description="Timestamp of last good data (UTC)",
    )
    gap_end: datetime = Field(
        ...,
        description="Timestamp when data resumed (UTC)",
    )
    duration_seconds: Decimal = Field(
        ...,
        description="Gap duration in seconds",
        ge=Decimal("0"),
    )
    reason: str = Field(
        ...,
        description="Reason for the gap",
        min_length=1,
        examples=[
            "websocket_disconnect",
            "exchange_maintenance",
            "sequence_gap",
            "timeout",
        ],
    )
    sequence_id_before: Optional[int] = Field(
        default=None,
        description="Last known sequence ID before gap",
        ge=0,
    )
    sequence_id_after: Optional[int] = Field(
        default=None,
        description="First sequence ID after gap",
        ge=0,
    )

    @model_validator(mode="after")
    def validate_gap(self) -> "GapMarker":
        """Validate gap marker consistency."""
        if self.gap_end < self.gap_start:
            raise ValueError(
                f"gap_end ({self.gap_end}) must be >= gap_start ({self.gap_start})"
            )
        return self

    @property
    def is_significant(self) -> bool:
        """
        Check if this gap is significant (> 5 seconds).

        Returns:
            bool: True if duration exceeds 5 seconds.
        """
        return self.duration_seconds > Decimal("5")

    @property
    def sequence_gap_size(self) -> Optional[int]:
        """
        Calculate the size of the sequence gap.

        Returns:
            Optional[int]: Number of missed sequences, or None if unknown.
        """
        if self.sequence_id_before is not None and self.sequence_id_after is not None:
            return self.sequence_id_after - self.sequence_id_before - 1
        return None


class HealthStatus(BaseModel):
    """
    Exchange/connection health metrics.

    Tracks the health of a WebSocket connection including latency,
    message rates, and gap history.

    Attributes:
        exchange: Exchange identifier.
        status: Current connection status.
        last_message_at: Timestamp of last received message.
        message_count: Total messages received in current session.
        lag_ms: Current processing lag in milliseconds.
        reconnect_count: Number of reconnections in current session.
        gaps_last_hour: Number of data gaps in the last hour.

    Example:
        >>> health = HealthStatus(
        ...     exchange="binance",
        ...     status=ConnectionStatus.CONNECTED,
        ...     last_message_at=datetime.utcnow(),
        ...     message_count=12345,
        ...     lag_ms=23,
        ...     reconnect_count=0,
        ...     gaps_last_hour=0,
        ... )
    """

    model_config = {"extra": "forbid"}

    exchange: str = Field(
        ...,
        description="Exchange identifier",
        min_length=1,
        max_length=50,
    )
    status: ConnectionStatus = Field(
        ...,
        description="Current connection status",
    )
    last_message_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last received message (UTC)",
    )
    message_count: int = Field(
        default=0,
        description="Total messages received in current session",
        ge=0,
    )
    lag_ms: int = Field(
        default=0,
        description="Current processing lag in milliseconds",
        ge=0,
    )
    reconnect_count: int = Field(
        default=0,
        description="Number of reconnections in current session",
        ge=0,
    )
    gaps_last_hour: int = Field(
        default=0,
        description="Number of data gaps in the last hour",
        ge=0,
    )

    @property
    def is_healthy(self) -> bool:
        """
        Check if the connection is healthy.

        Healthy means:
        - Status is CONNECTED
        - Lag < 1000ms
        - Gaps in last hour < 5

        Returns:
            bool: True if connection is healthy.
        """
        return (
            self.status.is_healthy
            and self.lag_ms < 1000
            and self.gaps_last_hour < 5
        )

    @property
    def is_degraded(self) -> bool:
        """
        Check if the connection is degraded but usable.

        Returns:
            bool: True if degraded (high lag or gaps but still connected).
        """
        return (
            self.status.is_usable
            and (self.lag_ms >= 1000 or self.gaps_last_hour >= 5)
        )

    @property
    def seconds_since_message(self) -> Optional[float]:
        """
        Calculate seconds since last message.

        Returns:
            Optional[float]: Seconds since last message, or None if never received.
        """
        if self.last_message_at is not None:
            return (datetime.utcnow() - self.last_message_at).total_seconds()
        return None


class ZScoreWarmupStatus(BaseModel):
    """
    Z-score warmup progress tracking.

    Used to display warmup status in the dashboard and log warmup progress.

    Attributes:
        metric_name: Name of the metric being tracked.
        instrument: Instrument identifier.
        exchange: Exchange identifier.
        is_warmed_up: Whether warmup is complete.
        sample_count: Current number of samples.
        min_samples: Minimum samples required.
        progress_pct: Warmup progress percentage.
        last_update: When this status was last updated.

    Example:
        >>> status = ZScoreWarmupStatus(
        ...     metric_name="spread_bps",
        ...     instrument="BTC-USDT-PERP",
        ...     exchange="binance",
        ...     is_warmed_up=False,
        ...     sample_count=15,
        ...     min_samples=30,
        ...     progress_pct=Decimal("50.0"),
        ...     last_update=datetime.utcnow(),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    metric_name: str = Field(
        ...,
        description="Name of the metric being tracked",
    )
    instrument: str = Field(
        ...,
        description="Instrument identifier",
    )
    exchange: str = Field(
        ...,
        description="Exchange identifier",
    )
    is_warmed_up: bool = Field(
        ...,
        description="Whether warmup is complete",
    )
    sample_count: int = Field(
        ...,
        description="Current number of samples",
        ge=0,
    )
    min_samples: int = Field(
        ...,
        description="Minimum samples required",
        ge=1,
    )
    progress_pct: Decimal = Field(
        ...,
        description="Warmup progress percentage (0-100)",
        ge=Decimal("0"),
        le=Decimal("100"),
    )
    last_update: datetime = Field(
        ...,
        description="When this status was last updated",
    )

    @property
    def samples_remaining(self) -> int:
        """
        Calculate samples remaining until warmup complete.

        Returns:
            int: Number of samples still needed.
        """
        return max(0, self.min_samples - self.sample_count)

    @property
    def display_text(self) -> str:
        """
        Generate display text for dashboard.

        Returns:
            str: Human-readable warmup status.
        """
        if self.is_warmed_up:
            return "active"
        return f"warming up ({self.sample_count}/{self.min_samples})"


class SystemHealthSummary(BaseModel):
    """
    Overall system health summary.

    Aggregates health from all exchanges and components.

    Attributes:
        timestamp: When this summary was generated.
        overall_status: Overall system status.
        exchanges: Health status per exchange.
        active_alerts_count: Number of active alerts.
        metrics_lag_ms: Metrics processing lag.
        storage_lag_ms: Storage write lag.

    Example:
        >>> summary = SystemHealthSummary(
        ...     timestamp=datetime.utcnow(),
        ...     overall_status=ConnectionStatus.CONNECTED,
        ...     exchanges={"binance": health_binance, "okx": health_okx},
        ...     active_alerts_count=2,
        ...     metrics_lag_ms=15,
        ...     storage_lag_ms=50,
        ... )
    """

    model_config = {"extra": "forbid"}

    timestamp: datetime = Field(
        ...,
        description="When this summary was generated",
    )
    overall_status: ConnectionStatus = Field(
        ...,
        description="Overall system status",
    )
    exchanges: dict[str, HealthStatus] = Field(
        default_factory=dict,
        description="Health status per exchange",
    )
    active_alerts_count: int = Field(
        default=0,
        description="Number of active alerts",
        ge=0,
    )
    metrics_lag_ms: int = Field(
        default=0,
        description="Metrics processing lag in milliseconds",
        ge=0,
    )
    storage_lag_ms: int = Field(
        default=0,
        description="Storage write lag in milliseconds",
        ge=0,
    )

    @property
    def all_exchanges_healthy(self) -> bool:
        """Check if all exchanges are healthy."""
        return all(h.is_healthy for h in self.exchanges.values())

    @property
    def any_exchange_disconnected(self) -> bool:
        """Check if any exchange is disconnected."""
        return any(
            h.status == ConnectionStatus.DISCONNECTED
            for h in self.exchanges.values()
        )
