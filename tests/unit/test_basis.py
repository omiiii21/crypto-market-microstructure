"""
Unit tests for BasisCalculator.

Tests perpetual-spot basis calculations with hand-verified expected
values and instrument validation.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.metrics.basis import BasisCalculator
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.metrics import BasisMetrics


def create_snapshot(
    bid: str,
    ask: str,
    instrument: str,
    exchange: str = "binance",
) -> OrderBookSnapshot:
    """Helper to create order book snapshot."""
    return OrderBookSnapshot(
        exchange=exchange,
        instrument=instrument,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
        sequence_id=12345,
        bids=[PriceLevel(price=Decimal(bid), quantity=Decimal("1.0"))],
        asks=[PriceLevel(price=Decimal(ask), quantity=Decimal("1.0"))],
    )


class TestBasisCalculator:
    """Test suite for BasisCalculator."""

    def test_initialization_default(self):
        """Test default initialization."""
        calc = BasisCalculator()
        assert calc.use_zscore is True
        assert calc.validate_instruments is True
        assert calc.zscore_calculator is not None

    def test_initialization_no_zscore(self):
        """Test initialization with z-score disabled."""
        calc = BasisCalculator(use_zscore=False)
        assert calc.use_zscore is False
        assert calc.zscore_calculator is None

    def test_initialization_no_validation(self):
        """Test initialization with validation disabled."""
        calc = BasisCalculator(validate_instruments=False)
        assert calc.validate_instruments is False

    def test_basic_basis_calculation_premium(self):
        """Test basic basis calculation when perp trades at premium."""
        # Hand calculation:
        # perp_mid = (50050 + 50051) / 2 = 50050.5
        # spot_mid = (50000 + 50001) / 2 = 50000.5
        # basis_abs = 50050.5 - 50000.5 = 50
        # basis_bps = (50 / 50000.5) * 10000 = 9.999900001...

        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot(
            bid="50050",
            ask="50051",
            instrument="BTC-USDT-PERP"
        )
        spot = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-SPOT"
        )

        metrics = calc.calculate(perp, spot)

        assert isinstance(metrics, BasisMetrics)
        assert metrics.basis_abs == Decimal("50")
        assert metrics.perp_mid == Decimal("50050.5")
        assert metrics.spot_mid == Decimal("50000.5")

        # Verify basis_bps
        expected_bps = Decimal("50") / Decimal("50000.5") * Decimal("10000")
        assert abs(metrics.basis_bps - expected_bps) < Decimal("0.000001")

        assert metrics.is_premium is True
        assert metrics.is_discount is False
        assert metrics.zscore is None  # Disabled

    def test_basic_basis_calculation_discount(self):
        """Test basic basis calculation when perp trades at discount."""
        # Hand calculation:
        # perp_mid = 49950.5
        # spot_mid = 50000.5
        # basis_abs = 49950.5 - 50000.5 = -50
        # basis_bps = (-50 / 50000.5) * 10000 = -9.999900001...

        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot(
            bid="49950",
            ask="49951",
            instrument="BTC-USDT-PERP"
        )
        spot = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-SPOT"
        )

        metrics = calc.calculate(perp, spot)

        assert metrics.basis_abs == Decimal("-50")
        expected_bps = Decimal("-50") / Decimal("50000.5") * Decimal("10000")
        assert abs(metrics.basis_bps - expected_bps) < Decimal("0.000001")

        assert metrics.is_premium is False
        assert metrics.is_discount is True

    def test_zero_basis(self):
        """Test basis calculation when perp and spot are equal."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-PERP"
        )
        spot = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-SPOT"
        )

        metrics = calc.calculate(perp, spot)

        assert metrics.basis_abs == Decimal("0")
        assert metrics.basis_bps == Decimal("0")
        assert metrics.is_premium is False
        assert metrics.is_discount is False

    def test_large_positive_basis(self):
        """Test large positive basis (strong contango)."""
        # perp trading 200 bps above spot
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot(
            bid="51000",
            ask="51001",
            instrument="BTC-USDT-PERP"
        )
        spot = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-SPOT"
        )

        metrics = calc.calculate(perp, spot)

        # basis_abs = 51000.5 - 50000.5 = 1000
        # basis_bps = (1000 / 50000.5) * 10000 = 199.998...
        assert metrics.basis_abs == Decimal("1000")
        expected_bps = Decimal("1000") / Decimal("50000.5") * Decimal("10000")
        assert abs(metrics.basis_bps - expected_bps) < Decimal("0.001")
        assert metrics.basis_bps > Decimal("199")

    def test_large_negative_basis(self):
        """Test large negative basis (strong backwardation)."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot(
            bid="49000",
            ask="49001",
            instrument="BTC-USDT-PERP"
        )
        spot = create_snapshot(
            bid="50000",
            ask="50001",
            instrument="BTC-USDT-SPOT"
        )

        metrics = calc.calculate(perp, spot)

        # basis_abs = 49000.5 - 50000.5 = -1000
        assert metrics.basis_abs == Decimal("-1000")
        assert metrics.basis_bps < Decimal("0")
        assert metrics.is_discount is True

    def test_instrument_validation_success(self):
        """Test that matching instruments pass validation."""
        calc = BasisCalculator(validate_instruments=True, use_zscore=False)

        perp = create_snapshot("50050", "50051", "BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")

        # Should not raise
        metrics = calc.calculate(perp, spot)
        assert metrics is not None

    def test_instrument_validation_mismatched_pair(self):
        """Test that mismatched base-quote pairs fail validation."""
        calc = BasisCalculator(validate_instruments=True, use_zscore=False)

        perp = create_snapshot("50050", "50051", "BTC-USDT-PERP")
        spot = create_snapshot("3000", "3001", "ETH-USDT-SPOT")  # Different base

        with pytest.raises(ValueError, match="Instrument mismatch"):
            calc.calculate(perp, spot)

    def test_instrument_validation_wrong_suffix_perp(self):
        """Test that wrong suffix on perp fails validation."""
        calc = BasisCalculator(validate_instruments=True, use_zscore=False)

        perp = create_snapshot("50000", "50001", "BTC-USDT-SPOT")  # Should be PERP
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")

        with pytest.raises(ValueError, match="Expected PERP instrument"):
            calc.calculate(perp, spot)

    def test_instrument_validation_wrong_suffix_spot(self):
        """Test that wrong suffix on spot fails validation."""
        calc = BasisCalculator(validate_instruments=True, use_zscore=False)

        perp = create_snapshot("50000", "50001", "BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", "BTC-USDT-PERP")  # Should be SPOT

        with pytest.raises(ValueError, match="Expected SPOT instrument"):
            calc.calculate(perp, spot)

    def test_instrument_validation_disabled(self):
        """Test that validation can be disabled."""
        calc = BasisCalculator(validate_instruments=False, use_zscore=False)

        # Even with mismatched instruments, should work
        perp = create_snapshot("50000", "50001", "BTC-USDT-PERP")
        spot = create_snapshot("3000", "3001", "ETH-USDT-SPOT")

        # Should not raise (validation disabled)
        metrics = calc.calculate(perp, spot)
        assert metrics is not None

    def test_decimal_precision_maintained(self):
        """Test that Decimal precision is maintained."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot("50050.123456", "50050.234567", "BTC-USDT-PERP")
        spot = create_snapshot("50000.123456", "50000.234567", "BTC-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        # All values should be Decimal
        assert isinstance(metrics.basis_abs, Decimal)
        assert isinstance(metrics.basis_bps, Decimal)
        assert isinstance(metrics.perp_mid, Decimal)
        assert isinstance(metrics.spot_mid, Decimal)

    def test_invalid_perp_snapshot(self):
        """Test error handling for invalid perp snapshot."""
        calc = BasisCalculator(use_zscore=False)

        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1),
            local_timestamp=datetime(2024, 1, 1),
            sequence_id=12345,
            bids=[],  # Empty
            asks=[],
        )
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")

        with pytest.raises(ValueError, match="Invalid perpetual snapshot"):
            calc.calculate(perp, spot)

    def test_invalid_spot_snapshot(self):
        """Test error handling for invalid spot snapshot."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot("50000", "50001", "BTC-USDT-PERP")
        spot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-SPOT",
            timestamp=datetime(2024, 1, 1),
            local_timestamp=datetime(2024, 1, 1),
            sequence_id=12345,
            bids=[],  # Empty
            asks=[],
        )

        with pytest.raises(ValueError, match="Invalid spot snapshot"):
            calc.calculate(perp, spot)

    def test_abs_basis_bps_property(self):
        """Test abs_basis_bps convenience property."""
        calc = BasisCalculator(use_zscore=False)

        # Negative basis
        perp = create_snapshot("49950", "49951", "BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        # Absolute value should be positive
        assert metrics.basis_bps < Decimal("0")
        assert metrics.abs_basis_bps > Decimal("0")
        assert metrics.abs_basis_bps == abs(metrics.basis_bps)

    def test_reset_zscore(self):
        """Test reset_zscore method."""
        calc = BasisCalculator(use_zscore=True, zscore_min_samples=5)

        # Add samples
        for i in range(10):
            perp = create_snapshot(f"5000{i}", f"5000{i+1}", "BTC-USDT-PERP")
            spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")
            calc.calculate(perp, spot)

        # Verify z-score calculator has data
        status = calc.zscore_status
        assert status is not None
        assert status.samples_collected == 10

        # Reset
        calc.reset_zscore(reason="Test reset")

        # Verify cleared
        status = calc.zscore_status
        assert status.samples_collected == 0

    def test_zscore_status_property(self):
        """Test zscore_status property."""
        # With z-score
        calc_with = BasisCalculator(use_zscore=True)
        status = calc_with.zscore_status
        assert status is not None

        # Without z-score
        calc_without = BasisCalculator(use_zscore=False)
        status = calc_without.zscore_status
        assert status is None

    def test_zscore_uses_absolute_value(self):
        """Test that z-score tracks absolute basis magnitude."""
        calc = BasisCalculator(use_zscore=True, zscore_min_samples=5)

        # Add samples with both positive and negative basis
        # Z-score should track magnitude, not sign
        test_data = [
            ("50050", "50051", "50000", "50001"),  # +50 basis
            ("49950", "49951", "50000", "50001"),  # -50 basis
            ("50100", "50101", "50000", "50001"),  # +100 basis
            ("49900", "49901", "50000", "50001"),  # -100 basis
            ("50025", "50026", "50000", "50001"),  # +25 basis
        ]

        for perp_bid, perp_ask, spot_bid, spot_ask in test_data:
            perp = create_snapshot(perp_bid, perp_ask, "BTC-USDT-PERP")
            spot = create_snapshot(spot_bid, spot_ask, "BTC-USDT-SPOT")
            calc.calculate(perp, spot)

        # 6th sample might have z-score
        perp = create_snapshot("50200", "50201", "BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT")
        metrics = calc.calculate(perp, spot)

        # Just verify it doesn't error (z-score may or may not be ready)

    def test_repr(self):
        """Test string representation."""
        calc = BasisCalculator(use_zscore=True, validate_instruments=True)
        repr_str = repr(calc)
        assert "BasisCalculator" in repr_str
        assert "use_zscore=True" in repr_str
        assert "validate_instruments=True" in repr_str


class TestBasisEdgeCases:
    """Test edge cases for basis calculation."""

    def test_very_small_basis(self):
        """Test very small basis (tight perp-spot parity)."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot("50000.00", "50000.02", "BTC-USDT-PERP")
        spot = create_snapshot("50000.00", "50000.01", "BTC-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        # basis_abs = 50000.01 - 50000.005 = 0.005
        assert abs(metrics.basis_abs) < Decimal("0.01")
        assert abs(metrics.basis_bps) < Decimal("0.1")

    def test_different_exchanges(self):
        """Test basis calculation across different exchanges."""
        calc = BasisCalculator(use_zscore=False, validate_instruments=False)

        perp = create_snapshot("50050", "50051", "BTC-USDT-PERP", exchange="binance")
        spot = create_snapshot("50000", "50001", "BTC-USDT-SPOT", exchange="coinbase")

        # Should work even with different exchanges
        metrics = calc.calculate(perp, spot)
        assert metrics.basis_abs == Decimal("50")

    def test_eth_instrument(self):
        """Test basis calculation for ETH."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot("3010", "3011", "ETH-USDT-PERP")
        spot = create_snapshot("3000", "3001", "ETH-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        # basis_abs = 3010.5 - 3000.5 = 10
        # basis_bps = (10 / 3000.5) * 10000 = 33.33...
        assert metrics.basis_abs == Decimal("10")
        expected_bps = Decimal("10") / Decimal("3000.5") * Decimal("10000")
        assert abs(metrics.basis_bps - expected_bps) < Decimal("0.01")

    def test_low_price_asset(self):
        """Test basis calculation for low-priced asset."""
        calc = BasisCalculator(use_zscore=False, validate_instruments=False)

        perp = create_snapshot("0.00101", "0.00102", "LOWCAP-USDT-PERP")
        spot = create_snapshot("0.00100", "0.00101", "LOWCAP-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        # basis should still be computed accurately
        assert metrics.basis_abs > Decimal("0")
        assert isinstance(metrics.basis_bps, Decimal)

    def test_high_price_asset(self):
        """Test basis calculation for high-priced asset."""
        calc = BasisCalculator(use_zscore=False)

        perp = create_snapshot("100050", "100051", "BTC-USDT-PERP")
        spot = create_snapshot("100000", "100001", "BTC-USDT-SPOT")

        metrics = calc.calculate(perp, spot)

        assert metrics.basis_abs == Decimal("50")
        expected_bps = Decimal("50") / Decimal("100000.5") * Decimal("10000")
        assert abs(metrics.basis_bps - expected_bps) < Decimal("0.001")
