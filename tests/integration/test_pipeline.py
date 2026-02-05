"""
Pipeline integration tests.

This module tests the full data flow through the surveillance system:
- OrderBookSnapshot -> MetricsAggregator -> AlertEvaluator -> Alert

Key test scenarios:
1. Normal flow where no alerts fire
2. Spread > threshold AND z-score > threshold triggers alert
3. Spread > threshold BUT z-score in warmup does NOT trigger
4. Basis alerts with persistence requirements
5. End-to-end with mocked adapters

Note:
    These tests use mocked dependencies and can run without Redis/PostgreSQL.
    For full integration tests requiring infrastructure, use @pytest.mark.requires_redis.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.detection.evaluator import AlertEvaluator, create_evaluator
from src.metrics.aggregator import MetricsAggregator
from src.metrics.spread import SpreadCalculator
from src.models.alerts import (
    Alert,
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertResult,
    AlertSeverity,
    AlertThreshold,
)
from src.models.metrics import AggregatedMetrics, SpreadMetrics
from src.models.orderbook import OrderBookSnapshot, PriceLevel


# =============================================================================
# TEST: FULL PIPELINE - NORMAL FLOW
# =============================================================================


class TestPipelineNormalFlow:
    """Test normal pipeline flow where no alerts fire."""

    def test_orderbook_to_metrics_calculation(
        self,
        sample_perp_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test that order book snapshot produces valid spread metrics.

        Verifies:
        - MetricsAggregator processes the snapshot
        - Spread is calculated correctly
        - All metrics are of correct type (Decimal)
        """
        aggregator = MetricsAggregator(
            use_zscore=True,
            zscore_window=100,
            zscore_min_samples=30,
        )

        metrics = aggregator.calculate_all(perp=sample_perp_snapshot)

        assert metrics is not None
        assert metrics.exchange == "binance"
        assert metrics.instrument == "BTC-USDT-PERP"
        assert isinstance(metrics.spread.spread_bps, Decimal)
        assert isinstance(metrics.spread.mid_price, Decimal)
        assert metrics.spread.spread_bps >= Decimal("0")

    def test_normal_spread_does_not_trigger_alert(
        self,
        normal_spread_metrics: SpreadMetrics,
        spread_warning_definition: AlertDefinition,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that normal spread metrics do not trigger an alert.

        Normal spread (0.2 bps) is well below the 3.0 bps threshold.
        """
        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=spread_warning_definition,
            metric_value=normal_spread_metrics.spread_bps,
            zscore_value=normal_spread_metrics.zscore,
            threshold_config=spread_warning_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason is None
        assert "Threshold not met" in (result.message or "")

    def test_metrics_with_basis_calculation(
        self,
        sample_perp_snapshot: OrderBookSnapshot,
        sample_spot_snapshot: OrderBookSnapshot,
    ) -> None:
        """
        Test that basis metrics are calculated when spot is provided.
        """
        aggregator = MetricsAggregator(
            use_zscore=True,
            zscore_window=100,
            validate_basis_instruments=False,  # Skip validation for test
        )

        metrics = aggregator.calculate_all(
            perp=sample_perp_snapshot,
            spot=sample_spot_snapshot,
        )

        assert metrics.has_basis
        assert metrics.basis is not None
        assert isinstance(metrics.basis.basis_bps, Decimal)
        assert isinstance(metrics.basis.perp_mid, Decimal)
        assert isinstance(metrics.basis.spot_mid, Decimal)


# =============================================================================
# TEST: ALERT TRIGGERING - SPREAD EXCEEDS THRESHOLD
# =============================================================================


class TestAlertTriggering:
    """Test alert triggering when conditions are met."""

    def test_spread_exceeds_threshold_and_zscore_triggers_alert(
        self,
        high_spread_metrics: SpreadMetrics,
        spread_warning_definition: AlertDefinition,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that spread > threshold AND z-score > threshold triggers alert.

        High spread (3.5 bps > 3.0) and high z-score (2.5 > 2.0) should trigger.
        """
        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=spread_warning_definition,
            metric_value=high_spread_metrics.spread_bps,
            zscore_value=high_spread_metrics.zscore,
            threshold_config=spread_warning_threshold,
        )

        assert result.triggered is True
        assert result.priority == AlertPriority.P2
        assert result.severity == AlertSeverity.WARNING
        assert result.alert_type == "spread_warning"

    def test_critical_spread_triggers_critical_alert(
        self,
        critical_spread_metrics: SpreadMetrics,
        spread_critical_definition: AlertDefinition,
        spread_critical_threshold: AlertThreshold,
    ) -> None:
        """
        Test that critical spread triggers P1 critical alert.

        Critical spread (5.5 bps > 5.0) and z-score (3.5 > 3.0) should trigger P1.
        """
        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=spread_critical_definition,
            metric_value=critical_spread_metrics.spread_bps,
            zscore_value=critical_spread_metrics.zscore,
            threshold_config=spread_critical_threshold,
        )

        assert result.triggered is True
        assert result.priority == AlertPriority.P1
        assert result.severity == AlertSeverity.CRITICAL
        assert result.alert_type == "spread_critical"

    def test_threshold_exceeded_but_zscore_not_met_does_not_trigger(
        self,
        spread_warning_definition: AlertDefinition,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that exceeding threshold but not z-score does NOT trigger.

        Spread (3.5 bps > 3.0) but z-score (1.5 <= 2.0) should not trigger.
        This is the dual-condition logic - BOTH must be met.
        """
        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=spread_warning_definition,
            metric_value=Decimal("3.5"),  # Exceeds 3.0 threshold
            zscore_value=Decimal("1.5"),  # Does NOT exceed 2.0 z-score threshold
            threshold_config=spread_warning_threshold,
        )

        assert result.triggered is False
        assert "Z-score not met" in (result.message or "")


# =============================================================================
# TEST: Z-SCORE WARMUP BEHAVIOR
# =============================================================================


class TestZScoreWarmup:
    """Test z-score warmup behavior."""

    def test_spread_exceeds_threshold_but_zscore_warmup_does_not_trigger(
        self,
        warmup_spread_metrics: SpreadMetrics,
        spread_warning_definition: AlertDefinition,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        CRITICAL TEST: Spread > threshold BUT z-score in warmup does NOT trigger.

        This is a fundamental requirement of the system. During warmup period
        (first ~30 samples), z-score is None and alerts should be skipped
        with skip_reason="zscore_warmup".

        Spread (3.5 bps > 3.0) but zscore=None should NOT trigger.
        """
        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=spread_warning_definition,
            metric_value=warmup_spread_metrics.spread_bps,  # 3.5 > 3.0
            zscore_value=warmup_spread_metrics.zscore,  # None (warmup)
            threshold_config=spread_warning_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason == "zscore_warmup"
        assert "Z-score not available (warmup period)" in (result.message or "")

    def test_zscore_becomes_available_after_warmup(self) -> None:
        """
        Test that z-score becomes available after warmup completes.

        Simulates collecting 30+ samples to exit warmup period.
        """
        spread_calc = SpreadCalculator(
            use_zscore=True,
            zscore_window=100,
            zscore_min_samples=30,
        )

        # Simulate warmup period - feed less than 30 samples
        for i in range(25):
            snapshot = self._create_snapshot(i)
            metrics = spread_calc.calculate(snapshot)
            # During warmup, z-score should be None
            assert metrics.zscore is None

        # Continue feeding samples past warmup threshold
        for i in range(25, 35):
            snapshot = self._create_snapshot(i)
            metrics = spread_calc.calculate(snapshot)

        # After 35 samples (> 30), z-score should be available
        assert metrics.zscore is not None
        assert isinstance(metrics.zscore, Decimal)

    def _create_snapshot(self, index: int) -> OrderBookSnapshot:
        """Helper to create test snapshots."""
        base_price = Decimal("50000")
        # Add some variation to spread
        spread_offset = Decimal(str((index % 5) * 0.1))

        bids = [
            PriceLevel(
                price=base_price - Decimal("0.5") - spread_offset,
                quantity=Decimal("1.0"),
            ),
        ]
        asks = [
            PriceLevel(
                price=base_price + Decimal("0.5") + spread_offset,
                quantity=Decimal("0.8"),
            ),
        ]

        return OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime.now(timezone.utc),
            local_timestamp=datetime.now(timezone.utc),
            sequence_id=100000 + index,
            bids=bids,
            asks=asks,
            depth_levels=20,
        )


# =============================================================================
# TEST: PERSISTENCE REQUIREMENTS
# =============================================================================


class TestPersistenceRequirements:
    """Test alert persistence requirements."""

    def test_basis_alert_requires_persistence(
        self,
        high_basis_metrics,
        basis_warning_definition: AlertDefinition,
        basis_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that basis alert with persistence requirement doesn't fire immediately.

        Basis (12 bps > 10) and z-score (2.5 > 2.0) meet threshold conditions,
        but persistence (120s) is not yet met.
        """
        evaluator = create_evaluator()

        # Evaluate with persistence NOT met
        result = evaluator.evaluate_with_persistence(
            alert_def=basis_warning_definition,
            metric_value=high_basis_metrics.basis_bps,
            zscore_value=high_basis_metrics.zscore,
            threshold_config=basis_warning_threshold,
            persistence_met=False,  # Condition hasn't persisted long enough
        )

        assert result.triggered is False
        assert "Persistence not met" in (result.message or "")

    def test_basis_alert_fires_after_persistence_met(
        self,
        high_basis_metrics,
        basis_warning_definition: AlertDefinition,
        basis_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that basis alert fires after persistence requirement is met.
        """
        evaluator = create_evaluator()

        # Evaluate with persistence MET
        result = evaluator.evaluate_with_persistence(
            alert_def=basis_warning_definition,
            metric_value=high_basis_metrics.basis_bps,
            zscore_value=high_basis_metrics.zscore,
            threshold_config=basis_warning_threshold,
            persistence_met=True,  # Condition has persisted for 120+ seconds
        )

        assert result.triggered is True
        assert result.priority == AlertPriority.P2


# =============================================================================
# TEST: END-TO-END WITH MOCKED ADAPTERS
# =============================================================================


class TestEndToEndWithMocks:
    """Test end-to-end pipeline with mocked adapters."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_storage(
        self,
        sample_perp_snapshot: OrderBookSnapshot,
        mock_redis_client: MagicMock,
        mock_postgres_client: MagicMock,
    ) -> None:
        """
        Test full pipeline flow with mocked storage clients.

        Flow: OrderBook -> Store in Redis -> Calculate Metrics -> Evaluate Alert
        """
        # Step 1: Store order book in Redis (mocked)
        await mock_redis_client.set_orderbook(sample_perp_snapshot)
        mock_redis_client.set_orderbook.assert_called_once()

        # Step 2: Calculate metrics
        aggregator = MetricsAggregator(
            use_zscore=False,  # Disable z-score for simpler test
        )
        metrics = aggregator.calculate_all(perp=sample_perp_snapshot)

        # Step 3: Verify metrics calculated
        assert metrics is not None
        assert metrics.spread is not None

        # Step 4: Store metrics in PostgreSQL (mocked)
        await mock_postgres_client.insert_spread_metrics([
            ("binance", "BTC-USDT-PERP", sample_perp_snapshot.timestamp, metrics.spread)
        ])
        mock_postgres_client.insert_spread_metrics.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_flow_with_mocked_storage(
        self,
        sample_triggered_alert: Alert,
        mock_redis_client: MagicMock,
        mock_postgres_client: MagicMock,
    ) -> None:
        """
        Test alert storage flow with mocked storage clients.
        """
        # Store alert in Redis
        await mock_redis_client.set_alert(sample_triggered_alert)
        mock_redis_client.set_alert.assert_called_once()

        # Publish alert notification
        await mock_redis_client.publish_alert(sample_triggered_alert)
        mock_redis_client.publish_alert.assert_called_once()

        # Store alert in PostgreSQL for history
        await mock_postgres_client.insert_alert(sample_triggered_alert)
        mock_postgres_client.insert_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_zscore_buffer_storage_and_retrieval(
        self,
        mock_redis_client: MagicMock,
    ) -> None:
        """
        Test z-score buffer storage operations.
        """
        # Add samples to z-score buffer
        for i in range(10):
            await mock_redis_client.add_zscore_sample(
                exchange="binance",
                instrument="BTC-USDT-PERP",
                metric="spread_bps",
                value=Decimal(str(2.0 + i * 0.1)),
                window_size=300,
            )

        assert mock_redis_client.add_zscore_sample.call_count == 10

        # Get buffer length
        mock_redis_client.get_zscore_buffer_length.return_value = 10
        length = await mock_redis_client.get_zscore_buffer_length(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            metric="spread_bps",
        )
        assert length == 10


# =============================================================================
# TEST: ALERT DISABLED
# =============================================================================


class TestAlertDisabled:
    """Test behavior when alerts are disabled."""

    def test_disabled_alert_does_not_trigger(
        self,
        high_spread_metrics: SpreadMetrics,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that disabled alerts do not trigger even when conditions are met.
        """
        disabled_definition = AlertDefinition(
            alert_type="spread_warning",
            name="Spread Warning",
            metric_name="spread_bps",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.GT,
            requires_zscore=True,
            enabled=False,  # Alert is disabled
        )

        evaluator = create_evaluator()

        result = evaluator.evaluate(
            alert_def=disabled_definition,
            metric_value=high_spread_metrics.spread_bps,
            zscore_value=high_spread_metrics.zscore,
            threshold_config=spread_warning_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason == "alert_disabled"


# =============================================================================
# TEST: CONDITION TYPES
# =============================================================================


class TestConditionTypes:
    """Test different alert condition types."""

    def test_less_than_condition(self) -> None:
        """Test LT (less than) condition for depth alerts."""
        depth_warning_def = AlertDefinition(
            alert_type="depth_warning",
            name="Low Depth Warning",
            metric_name="depth_10bps_total",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.LT,  # Less than
            requires_zscore=False,  # No z-score for depth alerts
        )

        depth_threshold = AlertThreshold(
            threshold=Decimal("500000"),  # $500K threshold
        )

        evaluator = create_evaluator()

        # Test with depth below threshold
        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("250000"),  # $250K < $500K
            zscore_value=None,
            threshold_config=depth_threshold,
        )

        assert result.triggered is True

    def test_absolute_greater_than_condition(self) -> None:
        """Test ABS_GT (absolute value greater than) condition."""
        basis_def = AlertDefinition(
            alert_type="basis_warning",
            name="Basis Warning",
            metric_name="basis_bps",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.ABS_GT,  # Absolute value > threshold
            requires_zscore=False,
        )

        basis_threshold = AlertThreshold(
            threshold=Decimal("10.0"),  # 10 bps
        )

        evaluator = create_evaluator()

        # Test with negative basis (perp discount)
        result = evaluator.evaluate(
            alert_def=basis_def,
            metric_value=Decimal("-12.0"),  # |-12| = 12 > 10
            zscore_value=None,
            threshold_config=basis_threshold,
        )

        assert result.triggered is True


# =============================================================================
# TEST: INPUT VALIDATION
# =============================================================================


class TestInputValidation:
    """Test input validation in the pipeline."""

    def test_metric_value_must_be_decimal(
        self,
        spread_warning_definition: AlertDefinition,
        spread_warning_threshold: AlertThreshold,
    ) -> None:
        """
        Test that metric_value must be Decimal, not float.

        CRITICAL: Financial precision requires Decimal for all values.
        """
        evaluator = create_evaluator()

        with pytest.raises(ValueError) as exc_info:
            evaluator.evaluate(
                alert_def=spread_warning_definition,
                metric_value=3.5,  # float - should raise
                zscore_value=Decimal("2.5"),
                threshold_config=spread_warning_threshold,
            )

        assert "must be Decimal" in str(exc_info.value)

    def test_invalid_orderbook_rejected(self) -> None:
        """
        Test that invalid order book snapshots are rejected.
        """
        empty_snapshot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime.now(timezone.utc),
            local_timestamp=datetime.now(timezone.utc),
            sequence_id=100000,
            bids=[],  # Empty - invalid
            asks=[],  # Empty - invalid
            depth_levels=20,
        )

        aggregator = MetricsAggregator()

        with pytest.raises(ValueError) as exc_info:
            aggregator.calculate_all(perp=empty_snapshot)

        assert "Invalid primary snapshot" in str(exc_info.value)
