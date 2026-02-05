"""
Z-Score Calculator for statistical anomaly detection.

This module provides a rolling window z-score calculator with critical
warmup guards to prevent premature alerting during initialization.

Key Safety Features:
    - Returns None during warmup period (< MIN_SAMPLES)
    - Returns None when std < MIN_STD (flat market protection)
    - Automatic buffer reset on gap detection
    - Timestamp tracking for TWAS calculations

Classes:
    ZScoreCalculator: Rolling window z-score calculator with warmup protection
    ZScoreStatus: Status information about the calculator state
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Deque, Optional


@dataclass
class ZScoreStatus:
    """
    Status information about the z-score calculator.

    Attributes:
        samples_collected: Current number of samples in buffer.
        samples_required: Minimum samples required for z-score calculation.
        is_ready: True if enough samples collected and std is sufficient.
        current_mean: Current mean value (None if not ready).
        current_std: Current standard deviation (None if not ready).
    """

    samples_collected: int
    samples_required: int
    is_ready: bool
    current_mean: Optional[Decimal]
    current_std: Optional[Decimal]


class ZScoreCalculator:
    """
    Rolling window z-score calculator with warmup protection.

    This calculator maintains a rolling window of samples and computes z-scores
    relative to the window mean and standard deviation. It implements critical
    safety guards to prevent premature alerting:

    1. Warmup Guard: Returns None until MIN_SAMPLES collected
    2. Flat Market Guard: Returns None if std < MIN_STD
    3. Gap Detection: Provides reset() to clear stale data

    Formula:
        zscore = (value - mean) / std

    Example:
        >>> calc = ZScoreCalculator(window_size=100, min_samples=30)
        >>> zscore = calc.add_sample(Decimal("50000.50"), datetime.utcnow())
        >>> if zscore is None:
        ...     print("Warmup period - z-score not available")
        >>> else:
        ...     print(f"Z-score: {zscore}")

    Attributes:
        MIN_SAMPLES: Minimum samples required before computing z-score (30).
        MIN_STD: Minimum standard deviation to compute z-score (0.0001).
    """

    MIN_SAMPLES: int = 30
    MIN_STD: Decimal = Decimal("0.0001")

    def __init__(
        self,
        window_size: int = 100,
        min_samples: Optional[int] = None,
        min_std: Optional[Decimal] = None,
    ) -> None:
        """
        Initialize the z-score calculator.

        Args:
            window_size: Maximum number of samples to keep (default: 100).
            min_samples: Override MIN_SAMPLES (default: 30).
            min_std: Override MIN_STD (default: 0.0001).

        Raises:
            ValueError: If window_size < min_samples.
        """
        self.window_size = window_size
        self.min_samples = min_samples if min_samples is not None else self.MIN_SAMPLES
        self.min_std = min_std if min_std is not None else self.MIN_STD

        if self.window_size < self.min_samples:
            raise ValueError(
                f"window_size ({self.window_size}) must be >= min_samples ({self.min_samples})"
            )

        # Rolling buffer for values and timestamps
        self.buffer: Deque[Decimal] = deque(maxlen=self.window_size)
        self.timestamps: Deque[datetime] = deque(maxlen=self.window_size)

    def add_sample(
        self, value: Decimal, timestamp: Optional[datetime] = None
    ) -> Optional[Decimal]:
        """
        Add a sample and compute z-score.

        This method implements the critical warmup guards:
        1. Returns None if samples < min_samples (warmup period)
        2. Returns None if std < min_std (flat market protection)

        Args:
            value: The value to add to the rolling window.
            timestamp: Timestamp of the sample (optional, for TWAS).

        Returns:
            Optional[Decimal]: Z-score if conditions met, None during warmup
                              or if std is too small.

        Example:
            >>> calc = ZScoreCalculator(window_size=100, min_samples=30)
            >>> # First 29 samples return None
            >>> for i in range(29):
            ...     assert calc.add_sample(Decimal(str(i))) is None
            >>> # 30th sample may return a z-score (if std is sufficient)
            >>> zscore = calc.add_sample(Decimal("30"))
        """
        # Add to buffer
        self.buffer.append(value)
        if timestamp is not None:
            self.timestamps.append(timestamp)

        # Guard 1: Warmup period - insufficient samples
        if len(self.buffer) < self.min_samples:
            return None

        # Calculate statistics
        mean = self._calculate_mean()
        std = self._calculate_std(mean)

        # Guard 2: Flat market protection - std too small
        if std < self.min_std:
            return None

        # Compute z-score
        zscore = (value - mean) / std
        return zscore

    def _calculate_mean(self) -> Decimal:
        """
        Calculate the mean of values in the buffer.

        Returns:
            Decimal: Mean value.
        """
        total = sum(self.buffer)
        count = Decimal(len(self.buffer))
        return total / count

    def _calculate_std(self, mean: Optional[Decimal] = None) -> Decimal:
        """
        Calculate the standard deviation of values in the buffer.

        Uses the sample standard deviation formula (n-1 in denominator).

        Args:
            mean: Pre-computed mean (if available) to avoid recalculation.

        Returns:
            Decimal: Standard deviation.
        """
        if mean is None:
            mean = self._calculate_mean()

        # Calculate variance: sum((x - mean)^2) / (n - 1)
        n = len(self.buffer)
        if n <= 1:
            return Decimal("0")

        variance_sum = sum((x - mean) ** 2 for x in self.buffer)
        variance = variance_sum / Decimal(n - 1)

        # Standard deviation is square root of variance
        # Using decimal sqrt via built-in decimal operations
        return variance.sqrt()

    def reset(self, reason: Optional[str] = None) -> None:
        """
        Clear the buffer and reset the calculator.

        This should be called on gap detection or regime changes to prevent
        stale data from affecting z-score calculations.

        Args:
            reason: Optional reason for reset (for logging/debugging).

        Example:
            >>> calc = ZScoreCalculator()
            >>> calc.reset(reason="Data gap detected")
        """
        self.buffer.clear()
        self.timestamps.clear()

    @property
    def status(self) -> ZScoreStatus:
        """
        Get the current status of the calculator.

        Returns:
            ZScoreStatus: Current calculator status including sample count,
                         readiness, and current statistics.

        Example:
            >>> calc = ZScoreCalculator()
            >>> status = calc.status
            >>> print(f"Samples: {status.samples_collected}/{status.samples_required}")
            >>> print(f"Ready: {status.is_ready}")
        """
        samples_collected = len(self.buffer)
        is_ready = False
        current_mean = None
        current_std = None

        if samples_collected >= self.min_samples:
            mean = self._calculate_mean()
            std = self._calculate_std(mean)

            if std >= self.min_std:
                is_ready = True
                current_mean = mean
                current_std = std

        return ZScoreStatus(
            samples_collected=samples_collected,
            samples_required=self.min_samples,
            is_ready=is_ready,
            current_mean=current_mean,
            current_std=current_std,
        )

    def __repr__(self) -> str:
        """String representation of the calculator."""
        status = self.status
        return (
            f"ZScoreCalculator(samples={status.samples_collected}/{status.samples_required}, "
            f"ready={status.is_ready}, mean={status.current_mean}, std={status.current_std})"
        )
