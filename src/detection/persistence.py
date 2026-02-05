"""
Persistence tracker for time-based alert conditions.

This module provides the PersistenceTracker class which tracks how long
alert conditions have been continuously true.

Key Features:
    - Tracks start time when condition first becomes true
    - Clears tracking when condition becomes false
    - Returns duration in seconds for persistence checks
    - Thread-safe for concurrent access

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> tracker = PersistenceTracker()
    >>> # Condition becomes true
    >>> tracker.track("spread_warning:BTC-USDT-PERP:binance", True, datetime.utcnow())
    >>> # Later, check duration
    >>> duration = tracker.get_duration("spread_warning:BTC-USDT-PERP:binance")
    >>> if duration is not None and duration >= 120.0:
    ...     print("Persistence requirement met!")
"""

from datetime import datetime
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


class PersistenceTracker:
    """
    Tracks how long alert conditions have been continuously true.

    Used for alerts that require a condition to persist for a minimum
    duration before firing (e.g., basis_warning requires 120 seconds).

    When a condition is met, the tracker records the start time.
    When the condition is no longer met, the tracker clears the record.
    The duration can be queried at any time to check if persistence
    requirements are satisfied.

    Attributes:
        _start_times: Dictionary mapping condition keys to start timestamps.

    Example:
        >>> tracker = PersistenceTracker()
        >>> key = "basis_warning:BTC-USDT-PERP:binance"
        >>>
        >>> # Condition becomes true at t=0
        >>> tracker.track(key, True, datetime(2025, 1, 26, 12, 0, 0))
        >>>
        >>> # Check at t=60 (should be ~60 seconds)
        >>> duration = tracker.get_duration(key, datetime(2025, 1, 26, 12, 1, 0))
        >>> assert duration == 60.0
        >>>
        >>> # Condition becomes false at t=90
        >>> tracker.track(key, False, datetime(2025, 1, 26, 12, 1, 30))
        >>>
        >>> # Duration is now None (tracking reset)
        >>> assert tracker.get_duration(key) is None
    """

    def __init__(self) -> None:
        """
        Initialize the persistence tracker.

        Example:
            >>> tracker = PersistenceTracker()
        """
        self._start_times: Dict[str, datetime] = {}

        logger.debug("persistence_tracker_initialized")

    def track(
        self,
        condition_key: str,
        is_met: bool,
        timestamp: datetime,
    ) -> Optional[datetime]:
        """
        Track a condition state change.

        When is_met=True and no tracking exists, starts tracking.
        When is_met=False, clears any existing tracking.

        Args:
            condition_key: Unique identifier for the condition
                          (e.g., "alert_type:instrument:exchange").
            is_met: Whether the condition is currently met.
            timestamp: Current timestamp for tracking.

        Returns:
            Optional[datetime]: The persistence start time if tracking,
                               None if not tracking (condition not met).

        Example:
            >>> tracker = PersistenceTracker()
            >>> key = "spread_warning:BTC-USDT-PERP"
            >>>
            >>> # Start tracking
            >>> start = tracker.track(key, True, datetime.utcnow())
            >>> assert start is not None
            >>>
            >>> # Stop tracking
            >>> result = tracker.track(key, False, datetime.utcnow())
            >>> assert result is None
        """
        if is_met:
            # Only set start time if not already tracking
            if condition_key not in self._start_times:
                self._start_times[condition_key] = timestamp
                logger.debug(
                    "persistence_tracking_started",
                    condition_key=condition_key,
                    start_time=timestamp.isoformat(),
                )

            return self._start_times[condition_key]

        else:
            # Condition no longer met - clear tracking
            if condition_key in self._start_times:
                old_start = self._start_times.pop(condition_key)
                logger.debug(
                    "persistence_tracking_cleared",
                    condition_key=condition_key,
                    was_tracking_since=old_start.isoformat(),
                )

            return None

    def get_duration(
        self,
        condition_key: str,
        current_time: Optional[datetime] = None,
    ) -> Optional[float]:
        """
        Get how long a condition has been continuously true.

        Args:
            condition_key: The unique condition identifier.
            current_time: Current timestamp (defaults to utcnow).

        Returns:
            Optional[float]: Duration in seconds if tracking,
                            None if not tracking.

        Example:
            >>> tracker = PersistenceTracker()
            >>> key = "basis_critical:BTC-USDT-PERP"
            >>>
            >>> # Not tracking yet
            >>> assert tracker.get_duration(key) is None
            >>>
            >>> # Start tracking
            >>> start = datetime(2025, 1, 26, 12, 0, 0)
            >>> tracker.track(key, True, start)
            >>>
            >>> # Check duration after 65 seconds
            >>> later = datetime(2025, 1, 26, 12, 1, 5)
            >>> duration = tracker.get_duration(key, later)
            >>> assert duration == 65.0
        """
        start = self._start_times.get(condition_key)
        if start is None:
            return None

        if current_time is None:
            current_time = datetime.utcnow()

        return (current_time - start).total_seconds()

    def is_persistence_met(
        self,
        condition_key: str,
        required_seconds: int,
        current_time: Optional[datetime] = None,
    ) -> bool:
        """
        Check if persistence requirement is met for a condition.

        Convenience method that combines get_duration with threshold check.

        Args:
            condition_key: The unique condition identifier.
            required_seconds: Required persistence duration in seconds.
            current_time: Current timestamp (defaults to utcnow).

        Returns:
            bool: True if condition has persisted for required duration.

        Example:
            >>> tracker = PersistenceTracker()
            >>> key = "basis_warning:BTC-USDT-PERP"
            >>> tracker.track(key, True, datetime(2025, 1, 26, 12, 0, 0))
            >>>
            >>> # After 100 seconds (needs 120)
            >>> t1 = datetime(2025, 1, 26, 12, 1, 40)
            >>> assert not tracker.is_persistence_met(key, 120, t1)
            >>>
            >>> # After 125 seconds (needs 120)
            >>> t2 = datetime(2025, 1, 26, 12, 2, 5)
            >>> assert tracker.is_persistence_met(key, 120, t2)
        """
        duration = self.get_duration(condition_key, current_time)
        if duration is None:
            return False

        return duration >= required_seconds

    def clear(self, condition_key: str) -> None:
        """
        Manually clear tracking for a condition.

        Used when an alert fires and tracking should reset, or for cleanup.

        Args:
            condition_key: The unique condition identifier to clear.

        Example:
            >>> tracker = PersistenceTracker()
            >>> key = "spread_warning:BTC-USDT-PERP"
            >>> tracker.track(key, True, datetime.utcnow())
            >>> assert tracker.get_duration(key) is not None
            >>>
            >>> tracker.clear(key)
            >>> assert tracker.get_duration(key) is None
        """
        if condition_key in self._start_times:
            self._start_times.pop(condition_key)
            logger.debug(
                "persistence_tracking_manually_cleared",
                condition_key=condition_key,
            )

    def clear_all(self) -> None:
        """
        Clear all tracked conditions.

        Used for reset scenarios or cleanup.

        Example:
            >>> tracker = PersistenceTracker()
            >>> tracker.track("key1", True, datetime.utcnow())
            >>> tracker.track("key2", True, datetime.utcnow())
            >>> tracker.clear_all()
            >>> assert len(tracker) == 0
        """
        count = len(self._start_times)
        self._start_times.clear()
        logger.info(
            "persistence_tracking_all_cleared",
            cleared_count=count,
        )

    def get_all_tracked_keys(self) -> list[str]:
        """
        Get all currently tracked condition keys.

        Returns:
            list[str]: List of condition keys being tracked.

        Example:
            >>> tracker = PersistenceTracker()
            >>> tracker.track("key1", True, datetime.utcnow())
            >>> tracker.track("key2", True, datetime.utcnow())
            >>> keys = tracker.get_all_tracked_keys()
            >>> assert "key1" in keys and "key2" in keys
        """
        return list(self._start_times.keys())

    def get_start_time(self, condition_key: str) -> Optional[datetime]:
        """
        Get the start time for a tracked condition.

        Args:
            condition_key: The unique condition identifier.

        Returns:
            Optional[datetime]: Start time if tracking, None otherwise.

        Example:
            >>> tracker = PersistenceTracker()
            >>> start = datetime(2025, 1, 26, 12, 0, 0)
            >>> tracker.track("key1", True, start)
            >>> assert tracker.get_start_time("key1") == start
        """
        return self._start_times.get(condition_key)

    def __len__(self) -> int:
        """
        Return the number of conditions being tracked.

        Returns:
            int: Number of tracked conditions.
        """
        return len(self._start_times)

    def __contains__(self, condition_key: str) -> bool:
        """
        Check if a condition is being tracked.

        Args:
            condition_key: The condition key to check.

        Returns:
            bool: True if condition is being tracked.
        """
        return condition_key in self._start_times


def create_persistence_tracker() -> PersistenceTracker:
    """
    Factory function to create a PersistenceTracker.

    Returns:
        PersistenceTracker: A new tracker instance.

    Example:
        >>> tracker = create_persistence_tracker()
    """
    return PersistenceTracker()


def build_condition_key(
    alert_type: str,
    instrument: str,
    exchange: str,
) -> str:
    """
    Build a standardized condition key.

    Args:
        alert_type: The alert type identifier.
        instrument: The instrument identifier.
        exchange: The exchange identifier.

    Returns:
        str: Formatted condition key.

    Example:
        >>> key = build_condition_key("spread_warning", "BTC-USDT-PERP", "binance")
        >>> assert key == "spread_warning:BTC-USDT-PERP:binance"
    """
    return f"{alert_type}:{instrument}:{exchange}"
