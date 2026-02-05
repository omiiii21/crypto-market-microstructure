"""
Unit tests for AlertManager.

Tests cover:
    - Full alert lifecycle with mocked storage
    - Throttling behavior
    - Deduplication
    - Escalation from P2 to P1
    - Auto-resolution when conditions clear
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertSeverity,
    AlertThreshold,
)
from src.models.metrics import (
    AggregatedMetrics,
    BasisMetrics,
    DepthMetrics,
    ImbalanceMetrics,
    SpreadMetrics,
)
from src.detection.evaluator import AlertEvaluator
from src.detection.persistence import PersistenceTracker
from src.detection.manager import AlertManager


@pytest.fixture
def mock_storage() -> AsyncMock:
    """Create a mock AlertStorage."""
    storage = AsyncMock()
    storage.save = AsyncMock()
    storage.update_resolution = AsyncMock(return_value=None)
    storage.update_escalation = AsyncMock(return_value=None)
    storage.update_peak = AsyncMock(return_value=None)
    storage.get_active_alerts = AsyncMock(return_value=[])
    storage.get_alerts_for_escalation_check = AsyncMock(return_value=[])
    return storage


@pytest.fixture
def evaluator() -> AlertEvaluator:
    """Create an AlertEvaluator instance."""
    return AlertEvaluator()


@pytest.fixture
def persistence_tracker() -> PersistenceTracker:
    """Create a PersistenceTracker instance."""
    return PersistenceTracker()


@pytest.fixture
def alert_definitions() -> Dict[str, AlertDefinition]:
    """Create alert definitions for testing."""
    return {
        "spread_warning": AlertDefinition(
            alert_type="spread_warning",
            name="Spread Warning",
            metric_name="spread_bps",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.GT,
            requires_zscore=True,
            throttle_seconds=60,
        ),
        "spread_critical": AlertDefinition(
            alert_type="spread_critical",
            name="Spread Critical",
            metric_name="spread_bps",
            default_priority=AlertPriority.P1,
            default_severity=AlertSeverity.CRITICAL,
            condition=AlertCondition.GT,
            requires_zscore=True,
            throttle_seconds=30,
        ),
        "depth_warning": AlertDefinition(
            alert_type="depth_warning",
            name="Depth Warning",
            metric_name="depth_10bps_total",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.LT,
            requires_zscore=False,
            throttle_seconds=60,
        ),
        "basis_warning": AlertDefinition(
            alert_type="basis_warning",
            name="Basis Warning",
            metric_name="basis_bps",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.ABS_GT,
            requires_zscore=True,
            persistence_seconds=120,
            throttle_seconds=60,
        ),
    }


@pytest.fixture
def thresholds() -> Dict[str, Dict[str, AlertThreshold]]:
    """Create thresholds for testing."""
    return {
        "BTC-USDT-PERP": {
            "spread_warning": AlertThreshold(
                threshold=Decimal("3.0"),
                zscore_threshold=Decimal("2.0"),
            ),
            "spread_critical": AlertThreshold(
                threshold=Decimal("5.0"),
                zscore_threshold=Decimal("3.0"),
            ),
            "depth_warning": AlertThreshold(
                threshold=Decimal("500000"),
            ),
            "basis_warning": AlertThreshold(
                threshold=Decimal("10.0"),
                zscore_threshold=Decimal("2.0"),
            ),
        },
    }


@pytest.fixture
def manager(
    mock_storage: AsyncMock,
    evaluator: AlertEvaluator,
    persistence_tracker: PersistenceTracker,
    alert_definitions: Dict[str, AlertDefinition],
    thresholds: Dict[str, Dict[str, AlertThreshold]],
) -> AlertManager:
    """Create an AlertManager instance."""
    return AlertManager(
        storage=mock_storage,
        evaluator=evaluator,
        persistence_tracker=persistence_tracker,
        alert_definitions=alert_definitions,
        thresholds=thresholds,
        global_throttle_seconds=60,
        escalation_seconds=300,
        auto_resolve=True,
    )


def create_test_metrics(
    spread_bps: Decimal = Decimal("2.0"),
    spread_zscore: Optional[Decimal] = Decimal("1.0"),
    depth_total: Decimal = Decimal("600000"),
    basis_bps: Optional[Decimal] = None,
    basis_zscore: Optional[Decimal] = None,
) -> AggregatedMetrics:
    """Create test metrics with specified values."""
    basis = None
    if basis_bps is not None:
        basis = BasisMetrics(
            basis_abs=basis_bps * Decimal("5.0"),  # Approximate
            basis_bps=basis_bps,
            perp_mid=Decimal("50050"),
            spot_mid=Decimal("50000"),
            zscore=basis_zscore,
        )

    return AggregatedMetrics(
        exchange="binance",
        instrument="BTC-USDT-PERP",
        timestamp=datetime.utcnow(),
        spread=SpreadMetrics(
            spread_abs=Decimal("5.0"),
            spread_bps=spread_bps,
            mid_price=Decimal("50000"),
            zscore=spread_zscore,
        ),
        depth=DepthMetrics(
            depth_5bps_bid=Decimal("250000"),
            depth_5bps_ask=Decimal("200000"),
            depth_5bps_total=Decimal("450000"),
            depth_10bps_bid=depth_total / 2,
            depth_10bps_ask=depth_total / 2,
            depth_10bps_total=depth_total,
            depth_25bps_bid=Decimal("1000000"),
            depth_25bps_ask=Decimal("900000"),
            depth_25bps_total=Decimal("1900000"),
            imbalance=Decimal("0.05"),
        ),
        basis=basis,
        imbalance=ImbalanceMetrics(
            top_of_book_imbalance=Decimal("0.1"),
            weighted_imbalance_5=Decimal("0.08"),
            weighted_imbalance_10=Decimal("0.05"),
        ),
    )


class TestAlertTriggering:
    """Tests for alert triggering logic."""

    @pytest.mark.asyncio
    async def test_alert_triggers_when_all_conditions_met(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert triggers when threshold AND z-score are both exceeded."""
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),  # > 3.0
            spread_zscore=Decimal("2.5"),  # > 2.0
        )

        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == "spread_warning"
        assert alerts[0].priority == AlertPriority.P2
        mock_storage.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_does_not_trigger_when_threshold_not_met(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert does NOT trigger when only z-score exceeded."""
        metrics = create_test_metrics(
            spread_bps=Decimal("2.5"),  # < 3.0 (below threshold)
            spread_zscore=Decimal("2.5"),  # > 2.0
        )

        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert len(alerts) == 0
        mock_storage.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_does_not_trigger_when_zscore_not_met(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert does NOT trigger when only threshold exceeded."""
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),  # > 3.0
            spread_zscore=Decimal("1.5"),  # < 2.0 (z-score not met)
        )

        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert len(alerts) == 0
        mock_storage.save.assert_not_called()


class TestZScoreWarmup:
    """Tests for z-score warmup handling."""

    @pytest.mark.asyncio
    async def test_alert_does_not_trigger_during_warmup(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """
        Test alert does NOT trigger when zscore is None (warmup).

        This is CRITICAL correct behavior - not an error.
        """
        metrics = create_test_metrics(
            spread_bps=Decimal("10.0"),  # Way above threshold
            spread_zscore=None,  # Z-score not available (warmup)
        )

        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert len(alerts) == 0
        mock_storage.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_triggers_after_warmup_complete(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert triggers once z-score becomes available."""
        # First call during warmup
        metrics_warmup = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=None,
        )
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics_warmup,
        )
        assert len(alerts1) == 0

        # Clear throttle state for clean test
        manager.clear_throttle_state()
        manager.clear_dedup_state()

        # Second call after warmup
        metrics_post_warmup = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )
        alerts2 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics_post_warmup,
        )
        assert len(alerts2) == 1


class TestPersistence:
    """Tests for persistence requirement handling."""

    @pytest.mark.asyncio
    async def test_alert_does_not_trigger_until_persistence_met(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert does NOT trigger until persistence duration is met."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)

        # First call - start persistence tracking
        metrics = create_test_metrics(
            spread_bps=Decimal("2.0"),  # Normal
            basis_bps=Decimal("12.0"),  # > 10.0
            basis_zscore=Decimal("2.5"),  # > 2.0
        )
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time,
        )

        # Should not trigger yet (needs 120s persistence)
        assert len(alerts1) == 0

        # Call at 60 seconds - still waiting
        alerts2 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time + timedelta(seconds=60),
        )
        assert len(alerts2) == 0

        # Call at 125 seconds - persistence met
        alerts3 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time + timedelta(seconds=125),
        )
        assert len(alerts3) == 1
        assert alerts3[0].alert_type == "basis_warning"


class TestThrottling:
    """Tests for alert throttling."""

    @pytest.mark.asyncio
    async def test_throttling_prevents_duplicate_alerts(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test throttling prevents duplicate alerts within window."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )

        # First call - should trigger
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time,
        )
        assert len(alerts1) == 1

        # Second call at 30 seconds - should be throttled (needs 60s)
        # Clear dedup state to isolate throttle testing
        manager.clear_dedup_state()

        alerts2 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time + timedelta(seconds=30),
        )
        assert len(alerts2) == 0

    @pytest.mark.asyncio
    async def test_alert_triggers_after_throttle_expires(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert triggers again after throttle window expires."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )

        # First call
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time,
        )
        assert len(alerts1) == 1

        # Clear dedup state to isolate throttle testing
        manager.clear_dedup_state()

        # Call after throttle expires (61 seconds, needs 60)
        alerts2 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time + timedelta(seconds=61),
        )
        assert len(alerts2) == 1


class TestDeduplication:
    """Tests for alert deduplication."""

    @pytest.mark.asyncio
    async def test_deduplication_prevents_duplicate_for_same_condition(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test deduplication prevents duplicate alerts for same active condition."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )

        # First call - should trigger
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time,
        )
        assert len(alerts1) == 1

        # Second call - should be deduplicated (not throttle, dedup)
        alerts2 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
            timestamp=base_time + timedelta(seconds=5),
        )
        assert len(alerts2) == 0


class TestEscalation:
    """Tests for P2 to P1 escalation."""

    @pytest.mark.asyncio
    async def test_p2_escalates_to_p1_after_timeout(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test P2 alerts escalate to P1 after escalation timeout."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)

        # Create a P2 alert that has been active for 310 seconds
        old_alert = Alert(
            alert_type="spread_warning",
            priority=AlertPriority.P2,
            severity=AlertSeverity.WARNING,
            exchange="binance",
            instrument="BTC-USDT-PERP",
            trigger_metric="spread_bps",
            trigger_value=Decimal("3.5"),
            trigger_threshold=Decimal("3.0"),
            trigger_condition=AlertCondition.GT,
            triggered_at=base_time - timedelta(seconds=310),  # Old enough
        )

        # Mock storage to return this alert
        mock_storage.get_alerts_for_escalation_check.return_value = [old_alert]
        mock_storage.update_escalation.return_value = old_alert.escalate(AlertPriority.P1)

        # Check escalations
        escalated = await manager.check_escalations(timestamp=base_time)

        assert len(escalated) == 1
        assert escalated[0].priority == AlertPriority.P1
        assert escalated[0].escalated is True
        mock_storage.update_escalation.assert_called_once()


class TestAutoResolution:
    """Tests for auto-resolution when conditions clear."""

    @pytest.mark.asyncio
    async def test_alert_auto_resolves_when_condition_clears(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test alert is auto-resolved when condition no longer met."""
        base_time = datetime(2025, 1, 26, 12, 0, 0)

        # First, trigger an alert
        metrics_high = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )
        alerts1 = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics_high,
            timestamp=base_time,
        )
        assert len(alerts1) == 1

        # Mock the active alert
        active_alert = alerts1[0]
        mock_storage.get_active_alerts.return_value = [active_alert]
        mock_storage.update_resolution.return_value = active_alert.resolve("auto")

        # Now condition clears
        metrics_normal = create_test_metrics(
            spread_bps=Decimal("2.0"),  # Below threshold
            spread_zscore=Decimal("0.5"),
        )
        await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics_normal,
            timestamp=base_time + timedelta(seconds=30),
        )

        # Should have called update_resolution
        mock_storage.update_resolution.assert_called()


class TestMultipleAlertTypes:
    """Tests for multiple alert types triggering."""

    @pytest.mark.asyncio
    async def test_multiple_alerts_can_trigger_simultaneously(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test multiple different alert types can trigger at once."""
        metrics = create_test_metrics(
            spread_bps=Decimal("5.5"),  # Triggers both spread_warning and spread_critical
            spread_zscore=Decimal("3.5"),  # Above both z-score thresholds
            depth_total=Decimal("400000"),  # Below 500000 threshold
        )

        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        # Should trigger spread_warning, spread_critical, and depth_warning
        alert_types = {a.alert_type for a in alerts}
        assert "spread_warning" in alert_types
        assert "spread_critical" in alert_types
        assert "depth_warning" in alert_types


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_unknown_instrument_uses_no_thresholds(
        self,
        manager: AlertManager,
        mock_storage: AsyncMock,
    ) -> None:
        """Test processing metrics for unknown instrument."""
        metrics = create_test_metrics(
            spread_bps=Decimal("10.0"),
            spread_zscore=Decimal("5.0"),
        )

        # Use an instrument with no configured thresholds
        alerts = await manager.process_metrics(
            exchange="binance",
            instrument="UNKNOWN-INSTRUMENT",  # No thresholds configured
            metrics=metrics,
        )

        # Should not trigger (no thresholds)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_get_active_condition_count(
        self,
        manager: AlertManager,
    ) -> None:
        """Test getting active condition count."""
        assert manager.get_active_condition_count() == 0

        # Trigger an alert
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )
        await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert manager.get_active_condition_count() == 1

    @pytest.mark.asyncio
    async def test_clear_state_methods(
        self,
        manager: AlertManager,
    ) -> None:
        """Test clearing throttle and dedup state."""
        # Trigger an alert
        metrics = create_test_metrics(
            spread_bps=Decimal("3.5"),
            spread_zscore=Decimal("2.5"),
        )
        await manager.process_metrics(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metrics=metrics,
        )

        assert manager.get_active_condition_count() > 0

        # Clear state
        manager.clear_throttle_state()
        manager.clear_dedup_state()

        assert manager.get_active_condition_count() == 0
