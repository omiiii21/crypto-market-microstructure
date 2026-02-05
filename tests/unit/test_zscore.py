"""
Unit tests for ZScoreCalculator.

Tests the critical warmup guards and statistical calculations for
the z-score calculator.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.metrics.zscore import ZScoreCalculator, ZScoreStatus


class TestZScoreCalculator:
    """Test suite for ZScoreCalculator."""

    def test_initialization_default(self):
        """Test default initialization parameters."""
        calc = ZScoreCalculator()
        assert calc.window_size == 100
        assert calc.min_samples == 30
        assert calc.min_std == Decimal("0.0001")
        assert len(calc.buffer) == 0
        assert len(calc.timestamps) == 0

    def test_initialization_custom(self):
        """Test custom initialization parameters."""
        calc = ZScoreCalculator(
            window_size=50,
            min_samples=20,
            min_std=Decimal("0.001")
        )
        assert calc.window_size == 50
        assert calc.min_samples == 20
        assert calc.min_std == Decimal("0.001")

    def test_initialization_validation(self):
        """Test validation of initialization parameters."""
        # window_size < min_samples should raise ValueError
        with pytest.raises(ValueError, match="window_size.*must be >= min_samples"):
            ZScoreCalculator(window_size=20, min_samples=30)

    def test_warmup_guard_returns_none(self):
        """Test that z-score returns None during warmup period (< MIN_SAMPLES)."""
        calc = ZScoreCalculator(window_size=100, min_samples=30)

        # First 29 samples should return None
        for i in range(29):
            zscore = calc.add_sample(Decimal(str(i)))
            assert zscore is None, f"Sample {i+1} should return None during warmup"
            assert len(calc.buffer) == i + 1

        # Status should show not ready
        status = calc.status
        assert status.samples_collected == 29
        assert status.samples_required == 30
        assert status.is_ready is False
        assert status.current_mean is None
        assert status.current_std is None

    def test_zscore_available_after_warmup(self):
        """Test that z-score becomes available after MIN_SAMPLES collected."""
        calc = ZScoreCalculator(window_size=100, min_samples=30)

        # Add 30 samples with sufficient variance
        for i in range(30):
            calc.add_sample(Decimal(str(i * 10)))

        # 31st sample should return a z-score
        zscore = calc.add_sample(Decimal("1000"))
        assert zscore is not None, "Z-score should be available after warmup"
        assert isinstance(zscore, Decimal)

        # Status should show ready
        status = calc.status
        assert status.samples_collected == 31
        assert status.is_ready is True
        assert status.current_mean is not None
        assert status.current_std is not None

    def test_flat_market_guard_returns_none(self):
        """Test that z-score returns None when std < MIN_STD (flat market)."""
        calc = ZScoreCalculator(window_size=100, min_samples=30)

        # Add 30 identical samples (zero variance)
        for i in range(30):
            zscore = calc.add_sample(Decimal("50000"))
            assert zscore is None, "Z-score should be None for flat market"

        # Status should show not ready due to insufficient std
        status = calc.status
        assert status.samples_collected == 30
        assert status.is_ready is False  # std < MIN_STD

    def test_zscore_calculation_accuracy(self):
        """Test z-score calculation with known values."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)  # Small for testing

        # Add samples: [10, 20, 30, 40, 50]
        # Mean = 30, Std = sqrt((400+100+0+100+400)/4) = sqrt(250) = 15.811...
        values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")]
        for val in values:
            calc.add_sample(val)

        # Add value 60: zscore = (60 - 30) / 15.811... = 1.897...
        # Mean of [10,20,30,40,50,60] = 35
        # Std of [10,20,30,40,50,60] = sqrt((625+225+25+25+225+625)/5) = sqrt(350) = 18.708...
        zscore = calc.add_sample(Decimal("60"))
        assert zscore is not None

        # Z-score for 60: (60 - 35) / 18.708... ≈ 1.336
        expected = Decimal("1.336")
        assert abs(zscore - expected) < Decimal("0.01"), f"Expected ~{expected}, got {zscore}"

    def test_rolling_window_behavior(self):
        """Test that buffer respects maxlen (rolling window)."""
        calc = ZScoreCalculator(window_size=5, min_samples=3)

        # Add 10 samples (only last 5 should be kept)
        for i in range(10):
            calc.add_sample(Decimal(str(i)))

        assert len(calc.buffer) == 5, "Buffer should respect maxlen"
        assert calc.buffer[-1] == Decimal("9"), "Last sample should be 9"
        assert calc.buffer[0] == Decimal("5"), "First sample should be 5 (oldest in window)"

    def test_reset_clears_buffer(self):
        """Test that reset() clears the buffer and timestamps."""
        calc = ZScoreCalculator(window_size=100, min_samples=30)

        # Add some samples
        for i in range(40):
            calc.add_sample(Decimal(str(i)), datetime.utcnow())

        assert len(calc.buffer) == 40
        assert len(calc.timestamps) == 40

        # Reset
        calc.reset(reason="Test reset")

        assert len(calc.buffer) == 0, "Buffer should be empty after reset"
        assert len(calc.timestamps) == 0, "Timestamps should be empty after reset"

        # Status should show not ready
        status = calc.status
        assert status.samples_collected == 0
        assert status.is_ready is False

    def test_timestamp_tracking(self):
        """Test that timestamps are tracked correctly."""
        calc = ZScoreCalculator(window_size=100, min_samples=30)

        timestamps = [datetime(2024, 1, 1, 0, 0, i) for i in range(5)]
        for i, ts in enumerate(timestamps):
            calc.add_sample(Decimal(str(i)), ts)

        assert len(calc.timestamps) == 5
        assert calc.timestamps[0] == timestamps[0]
        assert calc.timestamps[-1] == timestamps[-1]

    def test_zscore_sign_positive(self):
        """Test z-score is positive for above-mean values."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        # Add samples with mean = 50
        for val in [40, 45, 50, 55, 60]:
            calc.add_sample(Decimal(str(val)))

        # Add value well above mean
        zscore = calc.add_sample(Decimal("100"))
        assert zscore is not None
        assert zscore > Decimal("0"), "Z-score should be positive for above-mean value"

    def test_zscore_sign_negative(self):
        """Test z-score is negative for below-mean values."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        # Add samples with mean = 50
        for val in [40, 45, 50, 55, 60]:
            calc.add_sample(Decimal(str(val)))

        # Add value well below mean
        zscore = calc.add_sample(Decimal("10"))
        assert zscore is not None
        assert zscore < Decimal("0"), "Z-score should be negative for below-mean value"

    def test_mean_calculation(self):
        """Test internal mean calculation."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")]
        for val in values:
            calc.add_sample(val)

        mean = calc._calculate_mean()
        expected = Decimal("30")  # (10+20+30+40+50) / 5
        assert mean == expected, f"Expected mean {expected}, got {mean}"

    def test_std_calculation(self):
        """Test internal standard deviation calculation."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        # Add samples: [10, 20, 30, 40, 50]
        # Variance = ((10-30)^2 + (20-30)^2 + (30-30)^2 + (40-30)^2 + (50-30)^2) / 4
        #          = (400 + 100 + 0 + 100 + 400) / 4 = 250
        # Std = sqrt(250) ≈ 15.811388...
        values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")]
        for val in values:
            calc.add_sample(val)

        std = calc._calculate_std()
        expected = Decimal("15.811388")
        assert abs(std - expected) < Decimal("0.01"), f"Expected std ~{expected}, got {std}"

    def test_status_property(self):
        """Test the status property returns correct information."""
        calc = ZScoreCalculator(window_size=100, min_samples=10)

        # Before samples
        status = calc.status
        assert isinstance(status, ZScoreStatus)
        assert status.samples_collected == 0
        assert status.samples_required == 10
        assert status.is_ready is False

        # Add 5 samples
        for i in range(5):
            calc.add_sample(Decimal(str(i * 10)))

        status = calc.status
        assert status.samples_collected == 5
        assert status.is_ready is False

        # Add 5 more samples (total 10)
        for i in range(5, 10):
            calc.add_sample(Decimal(str(i * 10)))

        status = calc.status
        assert status.samples_collected == 10
        assert status.is_ready is True  # Enough samples and variance
        assert status.current_mean is not None
        assert status.current_std is not None

    def test_repr(self):
        """Test string representation."""
        calc = ZScoreCalculator(window_size=50, min_samples=10)
        repr_str = repr(calc)
        assert "ZScoreCalculator" in repr_str
        assert "samples=0/10" in repr_str
        assert "ready=False" in repr_str


class TestZScoreEdgeCases:
    """Test edge cases and error conditions."""

    def test_single_sample_no_zscore(self):
        """Test that single sample returns None."""
        calc = ZScoreCalculator(window_size=100, min_samples=1)
        zscore = calc.add_sample(Decimal("50000"))
        assert zscore is None, "Single sample should not produce z-score (std=0)"

    def test_two_samples_with_variance(self):
        """Test that two samples with variance can produce z-score."""
        calc = ZScoreCalculator(window_size=100, min_samples=2)
        calc.add_sample(Decimal("100"))
        zscore = calc.add_sample(Decimal("200"))
        # Mean = 150, value = 200, std = 70.71..., zscore = (200-150)/70.71 ≈ 0.707
        assert zscore is not None
        expected = Decimal("0.707")
        assert abs(zscore - expected) < Decimal("0.01")

    def test_negative_values(self):
        """Test z-score calculation with negative values."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        values = [Decimal("-50"), Decimal("-40"), Decimal("-30"), Decimal("-20"), Decimal("-10")]
        for val in values:
            calc.add_sample(val)

        zscore = calc.add_sample(Decimal("0"))
        assert zscore is not None
        assert zscore > Decimal("0"), "Value above mean should have positive z-score"

    def test_very_small_variance(self):
        """Test that very small variance (< MIN_STD) returns None."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        # Add samples with very small variance
        base = Decimal("50000.0000")
        for i in range(5):
            calc.add_sample(base + Decimal(str(i)) * Decimal("0.00001"))

        # Std will be very small
        status = calc.status
        if status.current_std is not None and status.current_std < calc.min_std:
            assert status.is_ready is False

    def test_large_values_precision(self):
        """Test that large values maintain precision."""
        calc = ZScoreCalculator(window_size=100, min_samples=5)

        # Add large values typical of BTC prices
        values = [
            Decimal("50000.50"),
            Decimal("50001.75"),
            Decimal("50002.25"),
            Decimal("49999.80"),
            Decimal("50000.10"),
        ]
        for val in values:
            calc.add_sample(val)

        zscore = calc.add_sample(Decimal("50005.00"))
        assert zscore is not None
        assert isinstance(zscore, Decimal), "Z-score should maintain Decimal precision"
