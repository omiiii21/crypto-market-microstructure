"""
Unit tests for PersistenceTracker.

Tests cover:
    - Duration tracking for conditions
    - Clearing when conditions are not met
    - Persistence requirement checking
    - Edge cases and boundary conditions
"""

from datetime import datetime, timedelta

import pytest

from src.detection.persistence import (
    PersistenceTracker,
    create_persistence_tracker,
    build_condition_key,
)


@pytest.fixture
def tracker() -> PersistenceTracker:
    """Create a PersistenceTracker instance."""
    return PersistenceTracker()


class TestBasicTracking:
    """Tests for basic persistence tracking."""

    def test_track_condition_met_starts_tracking(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that tracking starts when condition is first met."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        result = tracker.track(key, is_met=True, timestamp=start_time)

        assert result == start_time
        assert key in tracker
        assert tracker.get_start_time(key) == start_time

    def test_track_condition_not_met_clears_tracking(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that tracking is cleared when condition becomes false."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        # Start tracking
        tracker.track(key, is_met=True, timestamp=start_time)
        assert key in tracker

        # Stop tracking
        result = tracker.track(key, is_met=False, timestamp=start_time + timedelta(seconds=30))

        assert result is None
        assert key not in tracker
        assert tracker.get_start_time(key) is None

    def test_repeated_true_does_not_reset_start_time(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that repeated true conditions don't reset start time."""
        key = "basis_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        # First true
        tracker.track(key, is_met=True, timestamp=start_time)

        # Second true (later)
        later_time = start_time + timedelta(seconds=60)
        result = tracker.track(key, is_met=True, timestamp=later_time)

        # Should still return original start time
        assert result == start_time
        assert tracker.get_start_time(key) == start_time


class TestDurationCalculation:
    """Tests for duration calculation."""

    def test_get_duration_returns_correct_seconds(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that duration is calculated correctly."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check at 60 seconds
        check_time = start_time + timedelta(seconds=60)
        duration = tracker.get_duration(key, current_time=check_time)

        assert duration == 60.0

    def test_get_duration_returns_none_when_not_tracking(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that duration is None when not tracking."""
        key = "nonexistent_condition"

        duration = tracker.get_duration(key)

        assert duration is None

    def test_get_duration_after_clear(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test that duration is None after tracking is cleared."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track(key, is_met=True, timestamp=start_time)
        tracker.track(key, is_met=False, timestamp=start_time + timedelta(seconds=30))

        duration = tracker.get_duration(key)

        assert duration is None


class TestPersistenceRequirements:
    """Tests for persistence requirement checking."""

    def test_is_persistence_met_below_threshold(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test persistence is not met when duration below threshold."""
        key = "basis_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)
        required_seconds = 120

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check at 100 seconds (needs 120)
        check_time = start_time + timedelta(seconds=100)
        is_met = tracker.is_persistence_met(key, required_seconds, check_time)

        assert is_met is False

    def test_is_persistence_met_at_threshold(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test persistence is met exactly at threshold."""
        key = "basis_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)
        required_seconds = 120

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check at exactly 120 seconds
        check_time = start_time + timedelta(seconds=120)
        is_met = tracker.is_persistence_met(key, required_seconds, check_time)

        assert is_met is True

    def test_is_persistence_met_above_threshold(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test persistence is met when duration above threshold."""
        key = "basis_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)
        required_seconds = 120

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check at 150 seconds (needs 120)
        check_time = start_time + timedelta(seconds=150)
        is_met = tracker.is_persistence_met(key, required_seconds, check_time)

        assert is_met is True

    def test_is_persistence_met_when_not_tracking(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test persistence is not met when not tracking."""
        key = "nonexistent_condition"

        is_met = tracker.is_persistence_met(key, 60)

        assert is_met is False


class TestClearOperations:
    """Tests for clear operations."""

    def test_clear_single_condition(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test clearing a single condition."""
        key1 = "spread_warning:BTC-USDT-PERP:binance"
        key2 = "basis_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track(key1, is_met=True, timestamp=start_time)
        tracker.track(key2, is_met=True, timestamp=start_time)

        tracker.clear(key1)

        assert key1 not in tracker
        assert key2 in tracker

    def test_clear_nonexistent_condition(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test clearing a nonexistent condition doesn't raise."""
        tracker.clear("nonexistent_condition")  # Should not raise

    def test_clear_all(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test clearing all tracked conditions."""
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track("key1", is_met=True, timestamp=start_time)
        tracker.track("key2", is_met=True, timestamp=start_time)
        tracker.track("key3", is_met=True, timestamp=start_time)

        assert len(tracker) == 3

        tracker.clear_all()

        assert len(tracker) == 0


class TestUtilityMethods:
    """Tests for utility methods."""

    def test_get_all_tracked_keys(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test getting all tracked keys."""
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track("key1", is_met=True, timestamp=start_time)
        tracker.track("key2", is_met=True, timestamp=start_time)

        keys = tracker.get_all_tracked_keys()

        assert len(keys) == 2
        assert "key1" in keys
        assert "key2" in keys

    def test_len(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test len() returns correct count."""
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        assert len(tracker) == 0

        tracker.track("key1", is_met=True, timestamp=start_time)
        assert len(tracker) == 1

        tracker.track("key2", is_met=True, timestamp=start_time)
        assert len(tracker) == 2

        tracker.track("key1", is_met=False, timestamp=start_time)
        assert len(tracker) == 1

    def test_contains(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test 'in' operator for checking tracked conditions."""
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track("key1", is_met=True, timestamp=start_time)

        assert "key1" in tracker
        assert "key2" not in tracker


class TestConditionKeyBuilder:
    """Tests for condition key builder function."""

    def test_build_condition_key(self) -> None:
        """Test building a standardized condition key."""
        key = build_condition_key(
            alert_type="spread_warning",
            instrument="BTC-USDT-PERP",
            exchange="binance",
        )

        assert key == "spread_warning:BTC-USDT-PERP:binance"

    def test_build_condition_key_with_special_chars(self) -> None:
        """Test building key with special characters in instrument."""
        key = build_condition_key(
            alert_type="basis_critical",
            instrument="ETH-USDT-PERP",
            exchange="okx",
        )

        assert key == "basis_critical:ETH-USDT-PERP:okx"


class TestFactoryFunction:
    """Tests for factory function."""

    def test_create_persistence_tracker(self) -> None:
        """Test factory creates a working tracker."""
        tracker = create_persistence_tracker()

        assert isinstance(tracker, PersistenceTracker)
        assert len(tracker) == 0


class TestEdgeCases:
    """Tests for edge cases."""

    def test_same_timestamp_multiple_calls(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test multiple track calls with same timestamp."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        timestamp = datetime(2025, 1, 26, 12, 0, 0)

        # Multiple true calls at same time
        result1 = tracker.track(key, is_met=True, timestamp=timestamp)
        result2 = tracker.track(key, is_met=True, timestamp=timestamp)

        assert result1 == timestamp
        assert result2 == timestamp
        assert tracker.get_duration(key, timestamp) == 0.0

    def test_subsecond_duration(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test duration calculation with subsecond precision."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0, 0)

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check at 0.5 seconds
        check_time = start_time + timedelta(milliseconds=500)
        duration = tracker.get_duration(key, current_time=check_time)

        assert duration == pytest.approx(0.5, abs=0.001)

    def test_very_long_duration(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test duration calculation over very long periods."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        start_time = datetime(2025, 1, 26, 12, 0, 0)

        tracker.track(key, is_met=True, timestamp=start_time)

        # Check after 24 hours
        check_time = start_time + timedelta(hours=24)
        duration = tracker.get_duration(key, current_time=check_time)

        assert duration == 24 * 60 * 60  # 86400 seconds

    def test_condition_toggle_sequence(
        self,
        tracker: PersistenceTracker,
    ) -> None:
        """Test toggling condition on and off multiple times."""
        key = "spread_warning:BTC-USDT-PERP:binance"
        base_time = datetime(2025, 1, 26, 12, 0, 0)

        # First cycle
        tracker.track(key, is_met=True, timestamp=base_time)
        assert tracker.get_duration(key, base_time + timedelta(seconds=30)) == 30.0

        tracker.track(key, is_met=False, timestamp=base_time + timedelta(seconds=60))
        assert tracker.get_duration(key) is None

        # Second cycle
        tracker.track(key, is_met=True, timestamp=base_time + timedelta(seconds=120))
        # Duration should be from new start, not original
        assert tracker.get_duration(key, base_time + timedelta(seconds=150)) == 30.0
