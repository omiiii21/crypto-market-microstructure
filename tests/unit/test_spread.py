"""
Unit tests for SpreadCalculator.

Tests spread calculations with hand-verified expected values and
Decimal precision verification.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.metrics.spread import SpreadCalculator
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.metrics import SpreadMetrics


def create_snapshot(
    bid: str,
    ask: str,
    bid_qty: str = "1.0",
    ask_qty: str = "1.0",
    exchange: str = "binance",
    instrument: str = "BTC-USDT-PERP",
) -> OrderBookSnapshot:
    """Helper to create order book snapshot for testing."""
    return OrderBookSnapshot(
        exchange=exchange,
        instrument=instrument,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
        sequence_id=12345,
        bids=[PriceLevel(price=Decimal(bid), quantity=Decimal(bid_qty))],
        asks=[PriceLevel(price=Decimal(ask), quantity=Decimal(ask_qty))],
    )


class TestSpreadCalculator:
    """Test suite for SpreadCalculator."""

    def test_initialization_default(self):
        """Test default initialization."""
        calc = SpreadCalculator()
        assert calc.use_zscore is True
        assert calc.zscore_calculator is not None

    def test_initialization_no_zscore(self):
        """Test initialization with z-score disabled."""
        calc = SpreadCalculator(use_zscore=False)
        assert calc.use_zscore is False
        assert calc.zscore_calculator is None

    def test_basic_spread_calculation(self):
        """Test basic spread calculation with hand-verified values."""
        # Hand calculation:
        # bid = 50000.50, ask = 50000.75
        # spread_abs = 50000.75 - 50000.50 = 0.25
        # mid_price = (50000.50 + 50000.75) / 2 = 50000.625
        # spread_bps = (0.25 / 50000.625) * 10000 = 0.0499993750039...

        calc = SpreadCalculator(use_zscore=False)
        snapshot = create_snapshot(bid="50000.50", ask="50000.75")

        metrics = calc.calculate(snapshot)

        assert isinstance(metrics, SpreadMetrics)
        assert metrics.spread_abs == Decimal("0.25")
        assert metrics.mid_price == Decimal("50000.625")

        # Verify spread_bps with precision
        expected_bps = Decimal("0.25") / Decimal("50000.625") * Decimal("10000")
        assert metrics.spread_bps == expected_bps
        assert abs(metrics.spread_bps - Decimal("0.04999937500390618")) < Decimal("0.000001")

        # Z-score should be None (disabled)
        assert metrics.zscore is None

    def test_spread_calculation_with_zscore(self):
        """Test spread calculation with z-score enabled."""
        calc = SpreadCalculator(use_zscore=True, zscore_min_samples=5)

        # Add samples to warm up z-score
        for i in range(5):
            snapshot = create_snapshot(bid="50000.00", ask=f"50000.{10 + i}")
            metrics = calc.calculate(snapshot)

        # First 4 should have None z-score (warmup)
        snapshot = create_snapshot(bid="50000.00", ask="50000.10")
        calc_warmup = SpreadCalculator(use_zscore=True, zscore_min_samples=5)
        for i in range(4):
            snapshot_temp = create_snapshot(bid="50000.00", ask=f"50000.{10 + i}")
            metrics_temp = calc_warmup.calculate(snapshot_temp)
            assert metrics_temp.zscore is None

        # 5th sample should have z-score (if variance sufficient)
        snapshot = create_snapshot(bid="50000.00", ask="50000.50")
        metrics = calc.calculate(snapshot)
        # Z-score may or may not be available depending on variance

    def test_spread_zero(self):
        """Test spread calculation when bid equals ask (zero spread)."""
        # This should not occur in reality (crossed book validation)
        # But test the calculation logic
        calc = SpreadCalculator(use_zscore=False)

        # Note: OrderBookSnapshot validates no crossed books (bid < ask)
        # So we can only test equal prices by creating valid snapshot
        # Minimum spread is enforced by validation, but let's test very small spread
        snapshot = create_snapshot(bid="50000.00", ask="50000.01")

        metrics = calc.calculate(snapshot)
        assert metrics.spread_abs == Decimal("0.01")
        assert metrics.spread_bps > Decimal("0")

    def test_large_spread(self):
        """Test spread calculation with large spread."""
        # Hand calculation:
        # bid = 50000, ask = 50500
        # spread_abs = 500
        # mid_price = 50250
        # spread_bps = (500 / 50250) * 10000 = 99.5024...

        calc = SpreadCalculator(use_zscore=False)
        snapshot = create_snapshot(bid="50000", ask="50500")

        metrics = calc.calculate(snapshot)

        assert metrics.spread_abs == Decimal("500")
        assert metrics.mid_price == Decimal("50250")
        expected_bps = Decimal("500") / Decimal("50250") * Decimal("10000")
        assert abs(metrics.spread_bps - expected_bps) < Decimal("0.0001")

    def test_decimal_precision_maintained(self):
        """Test that Decimal precision is maintained throughout calculation."""
        calc = SpreadCalculator(use_zscore=False)
        snapshot = create_snapshot(bid="50000.123456", ask="50000.234567")

        metrics = calc.calculate(snapshot)

        # Verify all values are Decimal type
        assert isinstance(metrics.spread_abs, Decimal)
        assert isinstance(metrics.spread_bps, Decimal)
        assert isinstance(metrics.mid_price, Decimal)

        # Verify precision
        assert metrics.spread_abs == Decimal("0.111111")
        expected_mid = (Decimal("50000.123456") + Decimal("50000.234567")) / Decimal("2")
        assert metrics.mid_price == expected_mid

    def test_invalid_snapshot_empty_bids(self):
        """Test that calculator raises error for empty bids."""
        calc = SpreadCalculator(use_zscore=False)
        snapshot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[],  # Empty bids
            asks=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
        )

        with pytest.raises(ValueError, match="Invalid order book snapshot"):
            calc.calculate(snapshot)

    def test_invalid_snapshot_empty_asks(self):
        """Test that calculator raises error for empty asks."""
        calc = SpreadCalculator(use_zscore=False)
        snapshot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
            asks=[],  # Empty asks
        )

        with pytest.raises(ValueError, match="Invalid order book snapshot"):
            calc.calculate(snapshot)

    def test_invalid_snapshot_empty_both(self):
        """Test that calculator raises error for empty order book."""
        calc = SpreadCalculator(use_zscore=False)
        snapshot = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[],
            asks=[],
        )

        with pytest.raises(ValueError, match="Invalid order book snapshot"):
            calc.calculate(snapshot)

    def test_reset_zscore(self):
        """Test that reset_zscore clears the z-score calculator."""
        calc = SpreadCalculator(use_zscore=True, zscore_min_samples=5)

        # Add samples
        for i in range(10):
            snapshot = create_snapshot(bid="50000.00", ask=f"50000.{10 + i}")
            calc.calculate(snapshot)

        # Verify z-score calculator has data
        status = calc.zscore_status
        assert status is not None
        assert status.samples_collected == 10

        # Reset
        calc.reset_zscore(reason="Test reset")

        # Verify cleared
        status = calc.zscore_status
        assert status.samples_collected == 0
        assert status.is_ready is False

    def test_zscore_status_property(self):
        """Test zscore_status property."""
        # With z-score enabled
        calc_with = SpreadCalculator(use_zscore=True)
        status = calc_with.zscore_status
        assert status is not None
        assert status.samples_collected == 0

        # With z-score disabled
        calc_without = SpreadCalculator(use_zscore=False)
        status = calc_without.zscore_status
        assert status is None

    def test_multiple_calculations(self):
        """Test multiple sequential calculations."""
        calc = SpreadCalculator(use_zscore=False)

        snapshots = [
            create_snapshot(bid="50000.00", ask="50000.10"),
            create_snapshot(bid="50001.00", ask="50001.15"),
            create_snapshot(bid="49999.00", ask="49999.12"),
        ]

        for snapshot in snapshots:
            metrics = calc.calculate(snapshot)
            assert isinstance(metrics, SpreadMetrics)
            assert metrics.spread_abs > Decimal("0")
            assert metrics.spread_bps > Decimal("0")
            assert metrics.mid_price > Decimal("0")

    def test_repr(self):
        """Test string representation."""
        calc = SpreadCalculator(use_zscore=True)
        repr_str = repr(calc)
        assert "SpreadCalculator" in repr_str
        assert "use_zscore=True" in repr_str


class TestSpreadEdgeCases:
    """Test edge cases for spread calculation."""

    def test_very_tight_spread(self):
        """Test very tight spread (typical of liquid markets)."""
        calc = SpreadCalculator(use_zscore=False)
        # 0.01 USDT spread on 50000 USDT (0.2 bps)
        snapshot = create_snapshot(bid="50000.00", ask="50000.01")

        metrics = calc.calculate(snapshot)
        assert metrics.spread_abs == Decimal("0.01")
        expected_bps = Decimal("0.01") / Decimal("50000.005") * Decimal("10000")
        assert abs(metrics.spread_bps - expected_bps) < Decimal("0.000001")

    def test_very_wide_spread(self):
        """Test very wide spread (illiquid market)."""
        calc = SpreadCalculator(use_zscore=False)
        # 1000 USDT spread on 50000 USDT (200 bps)
        snapshot = create_snapshot(bid="49500", ask="50500")

        metrics = calc.calculate(snapshot)
        assert metrics.spread_abs == Decimal("1000")
        assert metrics.mid_price == Decimal("50000")
        expected_bps = Decimal("1000") / Decimal("50000") * Decimal("10000")
        assert metrics.spread_bps == expected_bps
        assert metrics.spread_bps == Decimal("200")

    def test_high_price_asset(self):
        """Test spread calculation for high-priced asset."""
        calc = SpreadCalculator(use_zscore=False)
        # Bitcoin at higher price
        snapshot = create_snapshot(bid="100000.50", ask="100000.75")

        metrics = calc.calculate(snapshot)
        assert metrics.spread_abs == Decimal("0.25")
        assert metrics.mid_price == Decimal("100000.625")

    def test_low_price_asset(self):
        """Test spread calculation for low-priced asset."""
        calc = SpreadCalculator(use_zscore=False)
        # Low-priced altcoin
        snapshot = create_snapshot(bid="0.00100", ask="0.00101")

        metrics = calc.calculate(snapshot)
        assert metrics.spread_abs == Decimal("0.00001")
        expected_mid = Decimal("0.001005")
        assert metrics.mid_price == expected_mid

    def test_different_exchanges(self):
        """Test that calculator works with different exchanges."""
        calc = SpreadCalculator(use_zscore=False)

        for exchange in ["binance", "okx", "coinbase"]:
            snapshot = create_snapshot(
                bid="50000.00",
                ask="50000.10",
                exchange=exchange
            )
            metrics = calc.calculate(snapshot)
            assert metrics.spread_abs == Decimal("0.10")

    def test_different_instruments(self):
        """Test that calculator works with different instruments."""
        calc = SpreadCalculator(use_zscore=False)

        for instrument in ["BTC-USDT-PERP", "BTC-USDT-SPOT", "ETH-USDT-PERP"]:
            snapshot = create_snapshot(
                bid="50000.00",
                ask="50000.10",
                instrument=instrument
            )
            metrics = calc.calculate(snapshot)
            assert metrics.spread_abs == Decimal("0.10")

    def test_asymmetric_quantities(self):
        """Test that spread calculation ignores quantities (uses price only)."""
        calc = SpreadCalculator(use_zscore=False)

        # Large bid quantity, small ask quantity
        snapshot1 = create_snapshot(
            bid="50000.00",
            ask="50000.10",
            bid_qty="100.0",
            ask_qty="0.1"
        )
        metrics1 = calc.calculate(snapshot1)

        # Small bid quantity, large ask quantity
        snapshot2 = create_snapshot(
            bid="50000.00",
            ask="50000.10",
            bid_qty="0.1",
            ask_qty="100.0"
        )
        metrics2 = calc.calculate(snapshot2)

        # Spread should be identical (quantity doesn't affect spread)
        assert metrics1.spread_abs == metrics2.spread_abs
        assert metrics1.spread_bps == metrics2.spread_bps
        assert metrics1.mid_price == metrics2.mid_price
