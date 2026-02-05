"""
Unit tests for AlertEvaluator.

Tests cover:
    - Threshold condition evaluation (gt, lt, abs_gt, abs_lt)
    - Z-score condition evaluation
    - Z-score warmup handling (CRITICAL)
    - Combined condition evaluation
    - Edge cases and error handling
"""

from decimal import Decimal
from typing import Optional

import pytest

from src.models.alerts import (
    AlertCondition,
    AlertDefinition,
    AlertPriority,
    AlertSeverity,
    AlertThreshold,
)
from src.detection.evaluator import AlertEvaluator


@pytest.fixture
def evaluator() -> AlertEvaluator:
    """Create an AlertEvaluator instance."""
    return AlertEvaluator()


@pytest.fixture
def spread_warning_def() -> AlertDefinition:
    """Create a spread warning alert definition requiring z-score."""
    return AlertDefinition(
        alert_type="spread_warning",
        name="Spread Warning",
        metric_name="spread_bps",
        default_priority=AlertPriority.P2,
        default_severity=AlertSeverity.WARNING,
        condition=AlertCondition.GT,
        requires_zscore=True,
        throttle_seconds=60,
    )


@pytest.fixture
def depth_warning_def() -> AlertDefinition:
    """Create a depth warning definition NOT requiring z-score."""
    return AlertDefinition(
        alert_type="depth_warning",
        name="Depth Warning",
        metric_name="depth_10bps_total",
        default_priority=AlertPriority.P2,
        default_severity=AlertSeverity.WARNING,
        condition=AlertCondition.LT,
        requires_zscore=False,
        throttle_seconds=60,
    )


@pytest.fixture
def basis_warning_def() -> AlertDefinition:
    """Create a basis warning definition with abs_gt condition."""
    return AlertDefinition(
        alert_type="basis_warning",
        name="Basis Warning",
        metric_name="basis_bps",
        default_priority=AlertPriority.P2,
        default_severity=AlertSeverity.WARNING,
        condition=AlertCondition.ABS_GT,
        requires_zscore=True,
        persistence_seconds=120,
        throttle_seconds=60,
    )


@pytest.fixture
def spread_threshold() -> AlertThreshold:
    """Create threshold for spread warning."""
    return AlertThreshold(
        threshold=Decimal("3.0"),
        zscore_threshold=Decimal("2.0"),
    )


@pytest.fixture
def depth_threshold() -> AlertThreshold:
    """Create threshold for depth warning (no z-score)."""
    return AlertThreshold(
        threshold=Decimal("500000"),
    )


@pytest.fixture
def basis_threshold() -> AlertThreshold:
    """Create threshold for basis warning."""
    return AlertThreshold(
        threshold=Decimal("10.0"),
        zscore_threshold=Decimal("2.0"),
    )


class TestThresholdConditions:
    """Tests for primary threshold condition evaluation."""

    def test_gt_condition_met(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test greater-than condition is met when value exceeds threshold."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.5"),
            zscore_value=Decimal("2.5"),  # Also exceeds z-score threshold
            threshold_config=spread_threshold,
        )

        assert result.triggered is True
        assert result.alert_type == "spread_warning"
        assert result.priority == AlertPriority.P2
        assert result.severity == AlertSeverity.WARNING

    def test_gt_condition_not_met(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test greater-than condition not met when value below threshold."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("2.5"),  # Below 3.0 threshold
            zscore_value=Decimal("2.5"),
            threshold_config=spread_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason is None
        assert "Threshold not met" in (result.message or "")

    def test_lt_condition_met(
        self,
        evaluator: AlertEvaluator,
        depth_warning_def: AlertDefinition,
        depth_threshold: AlertThreshold,
    ) -> None:
        """Test less-than condition is met when value below threshold."""
        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("400000"),  # Below 500000 threshold
            zscore_value=None,  # Not required
            threshold_config=depth_threshold,
        )

        assert result.triggered is True
        assert result.alert_type == "depth_warning"

    def test_lt_condition_not_met(
        self,
        evaluator: AlertEvaluator,
        depth_warning_def: AlertDefinition,
        depth_threshold: AlertThreshold,
    ) -> None:
        """Test less-than condition not met when value above threshold."""
        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("600000"),  # Above 500000 threshold
            zscore_value=None,
            threshold_config=depth_threshold,
        )

        assert result.triggered is False

    def test_abs_gt_condition_met_positive(
        self,
        evaluator: AlertEvaluator,
        basis_warning_def: AlertDefinition,
        basis_threshold: AlertThreshold,
    ) -> None:
        """Test abs_gt condition met with positive value."""
        result = evaluator.evaluate(
            alert_def=basis_warning_def,
            metric_value=Decimal("12.0"),  # |12.0| > 10.0
            zscore_value=Decimal("2.5"),
            threshold_config=basis_threshold,
        )

        assert result.triggered is True

    def test_abs_gt_condition_met_negative(
        self,
        evaluator: AlertEvaluator,
        basis_warning_def: AlertDefinition,
        basis_threshold: AlertThreshold,
    ) -> None:
        """Test abs_gt condition met with negative value."""
        result = evaluator.evaluate(
            alert_def=basis_warning_def,
            metric_value=Decimal("-12.0"),  # |-12.0| > 10.0
            zscore_value=Decimal("-2.5"),  # Negative z-score also valid
            threshold_config=basis_threshold,
        )

        assert result.triggered is True


class TestZScoreConditions:
    """Tests for z-score condition evaluation."""

    def test_zscore_condition_met(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test alert triggers when both threshold AND z-score are met."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.5"),  # > 3.0
            zscore_value=Decimal("2.5"),  # > 2.0
            threshold_config=spread_threshold,
        )

        assert result.triggered is True

    def test_zscore_condition_not_met(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test alert does NOT trigger when threshold met but z-score not met."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.5"),  # > 3.0 (threshold met)
            zscore_value=Decimal("1.5"),  # <= 2.0 (z-score NOT met)
            threshold_config=spread_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason is None
        assert "Z-score not met" in (result.message or "")


class TestZScoreWarmup:
    """
    Tests for z-score warmup handling.

    CRITICAL: This is correct behavior, NOT an error.
    When zscore_value is None (during warmup), alerts requiring z-score
    must be skipped with skip_reason="zscore_warmup".
    """

    def test_zscore_warmup_skips_alert(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """
        Test that alert is skipped during z-score warmup.

        This is CORRECT behavior - not an error.
        """
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("10.0"),  # Way above threshold
            zscore_value=None,  # Z-score not available (warmup)
            threshold_config=spread_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason == "zscore_warmup"
        assert "warmup" in (result.message or "").lower()

    def test_no_zscore_required_allows_none(
        self,
        evaluator: AlertEvaluator,
        depth_warning_def: AlertDefinition,
        depth_threshold: AlertThreshold,
    ) -> None:
        """Test that alerts not requiring z-score work with None zscore."""
        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("400000"),  # Below threshold
            zscore_value=None,  # OK because requires_zscore=False
            threshold_config=depth_threshold,
        )

        assert result.triggered is True
        assert result.skip_reason is None


class TestDisabledAlerts:
    """Tests for disabled alert handling."""

    def test_disabled_alert_is_skipped(
        self,
        evaluator: AlertEvaluator,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test that disabled alerts are skipped."""
        disabled_def = AlertDefinition(
            alert_type="spread_warning",
            name="Spread Warning",
            metric_name="spread_bps",
            default_priority=AlertPriority.P2,
            default_severity=AlertSeverity.WARNING,
            condition=AlertCondition.GT,
            requires_zscore=True,
            enabled=False,  # Disabled
        )

        result = evaluator.evaluate(
            alert_def=disabled_def,
            metric_value=Decimal("10.0"),
            zscore_value=Decimal("5.0"),
            threshold_config=spread_threshold,
        )

        assert result.triggered is False
        assert result.skip_reason == "alert_disabled"


class TestInputValidation:
    """Tests for input validation."""

    def test_metric_value_must_be_decimal(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test that metric_value must be Decimal, not float."""
        with pytest.raises(ValueError) as exc_info:
            evaluator.evaluate(
                alert_def=spread_warning_def,
                metric_value=3.5,  # type: ignore - Float instead of Decimal
                zscore_value=Decimal("2.5"),
                threshold_config=spread_threshold,
            )

        assert "must be Decimal" in str(exc_info.value)


class TestPersistenceEvaluation:
    """Tests for evaluation with persistence."""

    def test_persistence_not_met_prevents_trigger(
        self,
        evaluator: AlertEvaluator,
        basis_warning_def: AlertDefinition,
        basis_threshold: AlertThreshold,
    ) -> None:
        """Test that alert doesn't trigger when persistence not met."""
        result = evaluator.evaluate_with_persistence(
            alert_def=basis_warning_def,
            metric_value=Decimal("12.0"),
            zscore_value=Decimal("2.5"),
            threshold_config=basis_threshold,
            persistence_met=False,
        )

        assert result.triggered is False
        assert "Persistence not met" in (result.message or "")

    def test_persistence_met_allows_trigger(
        self,
        evaluator: AlertEvaluator,
        basis_warning_def: AlertDefinition,
        basis_threshold: AlertThreshold,
    ) -> None:
        """Test that alert triggers when persistence is met."""
        result = evaluator.evaluate_with_persistence(
            alert_def=basis_warning_def,
            metric_value=Decimal("12.0"),
            zscore_value=Decimal("2.5"),
            threshold_config=basis_threshold,
            persistence_met=True,
        )

        assert result.triggered is True


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_exact_threshold_value_gt(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test that exact threshold value does NOT trigger (gt requires strictly greater)."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.0"),  # Exactly at threshold
            zscore_value=Decimal("2.5"),
            threshold_config=spread_threshold,
        )

        assert result.triggered is False

    def test_exact_zscore_threshold(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
        spread_threshold: AlertThreshold,
    ) -> None:
        """Test that exact z-score threshold does NOT trigger."""
        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.5"),
            zscore_value=Decimal("2.0"),  # Exactly at z-score threshold
            threshold_config=spread_threshold,
        )

        assert result.triggered is False

    def test_zero_values(
        self,
        evaluator: AlertEvaluator,
        depth_warning_def: AlertDefinition,
    ) -> None:
        """Test handling of zero metric values."""
        threshold = AlertThreshold(threshold=Decimal("0.0"))

        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("0.0"),
            zscore_value=None,
            threshold_config=threshold,
        )

        # 0.0 is not less than 0.0
        assert result.triggered is False

    def test_negative_zscore_with_abs_comparison(
        self,
        evaluator: AlertEvaluator,
        basis_warning_def: AlertDefinition,
        basis_threshold: AlertThreshold,
    ) -> None:
        """Test that negative z-score works with absolute comparison."""
        result = evaluator.evaluate(
            alert_def=basis_warning_def,
            metric_value=Decimal("-12.0"),
            zscore_value=Decimal("-2.5"),  # Negative but |2.5| > 2.0
            threshold_config=basis_threshold,
        )

        assert result.triggered is True

    def test_large_values(
        self,
        evaluator: AlertEvaluator,
        depth_warning_def: AlertDefinition,
    ) -> None:
        """Test handling of large decimal values."""
        threshold = AlertThreshold(threshold=Decimal("1000000000.0"))

        result = evaluator.evaluate(
            alert_def=depth_warning_def,
            metric_value=Decimal("999999999.99"),  # Just below threshold
            zscore_value=None,
            threshold_config=threshold,
        )

        assert result.triggered is True  # LT condition met

    def test_high_precision_decimals(
        self,
        evaluator: AlertEvaluator,
        spread_warning_def: AlertDefinition,
    ) -> None:
        """Test handling of high precision decimal values."""
        threshold = AlertThreshold(
            threshold=Decimal("3.000000001"),
            zscore_threshold=Decimal("2.0"),
        )

        result = evaluator.evaluate(
            alert_def=spread_warning_def,
            metric_value=Decimal("3.000000002"),  # Just above threshold
            zscore_value=Decimal("2.5"),
            threshold_config=threshold,
        )

        assert result.triggered is True
