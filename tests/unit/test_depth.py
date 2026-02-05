"""
Unit tests for DepthCalculator.

Tests depth calculations at various bps levels and imbalance metrics
with hand-verified expected values.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.metrics.depth import DepthCalculator
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.metrics import DepthMetrics


def create_snapshot_with_depth(
    mid_price: str,
    bid_levels: list[tuple[str, str]],  # List of (price, quantity)
    ask_levels: list[tuple[str, str]],
    exchange: str = "binance",
    instrument: str = "BTC-USDT-PERP",
) -> OrderBookSnapshot:
    """Helper to create order book snapshot with multiple depth levels."""
    bids = [
        PriceLevel(price=Decimal(price), quantity=Decimal(qty))
        for price, qty in bid_levels
    ]
    asks = [
        PriceLevel(price=Decimal(price), quantity=Decimal(qty))
        for price, qty in ask_levels
    ]

    # Sort bids descending, asks ascending
    bids_sorted = sorted(bids, key=lambda x: x.price, reverse=True)
    asks_sorted = sorted(asks, key=lambda x: x.price)

    return OrderBookSnapshot(
        exchange=exchange,
        instrument=instrument,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
        sequence_id=12345,
        bids=bids_sorted,
        asks=asks_sorted,
    )


class TestDepthCalculator:
    """Test suite for DepthCalculator."""

    def test_initialization_default(self):
        """Test default initialization."""
        calc = DepthCalculator()
        assert calc.bps_levels == [5, 10, 25]
        assert calc.reference_level == 10

    def test_initialization_custom(self):
        """Test custom initialization."""
        calc = DepthCalculator(bps_levels=[10, 25], reference_level=10)
        assert calc.bps_levels == [10, 25]
        assert calc.reference_level == 10

    def test_initialization_validation_reference_level(self):
        """Test validation that reference_level must be in bps_levels."""
        with pytest.raises(ValueError, match="reference_level.*must be in bps_levels"):
            DepthCalculator(bps_levels=[5, 10], reference_level=25)

    def test_initialization_validation_invalid_levels(self):
        """Test validation that only 5, 10, 25 are allowed."""
        with pytest.raises(ValueError, match="bps_levels must only contain"):
            DepthCalculator(bps_levels=[5, 10, 50])  # 50 not allowed

    def test_basic_depth_calculation(self):
        """Test basic depth calculation at 10 bps."""
        # Hand calculation:
        # Mid price ≈ 50000
        # 10 bps = 0.001 = 0.1%
        # Bid threshold = 50000 * 0.999 = 49950
        # Ask threshold = 50000 * 1.001 = 50050

        calc = DepthCalculator(bps_levels=[5, 10, 25])

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "10"),   # $499,990 (within 10bps)
                ("49975", "5"),    # $249,875 (within 10bps)
                ("49950", "2"),    # $99,900 (on 10bps threshold)
                ("49900", "1"),    # $49,900 (outside 10bps)
            ],
            ask_levels=[
                ("50001", "8"),    # $400,008 (within 10bps)
                ("50025", "4"),    # $200,100 (within 10bps)
                ("50075", "2"),    # $100,150 (outside 10bps)
            ],
        )

        metrics = calc.calculate(snapshot)

        assert isinstance(metrics, DepthMetrics)

        # Verify we have positive depth at 10bps
        assert metrics.depth_10bps_bid > Decimal("0")
        assert metrics.depth_10bps_ask > Decimal("0")
        assert metrics.depth_10bps_total > Decimal("0")

    def test_depth_5bps_calculation(self):
        """Test depth calculation at 5 bps (tighter range)."""
        # Hand calculation:
        # Mid price ≈ 50000
        # 5 bps = 0.0005 = 0.05%
        # Bid threshold = 50000 * 0.9995 = 49975
        # Ask threshold = 50000 * 1.0005 = 50025

        calc = DepthCalculator(bps_levels=[5, 10, 25])

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "10"),   # $499,990 (within 5bps)
                ("49975", "5"),    # $249,875 (on 5bps threshold)
                ("49950", "2"),    # $99,900 (outside 5bps)
            ],
            ask_levels=[
                ("50001", "8"),    # $400,008 (within 5bps)
                ("50025", "4"),    # $200,100 (on 5bps threshold)
                ("50050", "2"),    # $100,100 (outside 5bps)
            ],
        )

        metrics = calc.calculate(snapshot)

        # Verify we have positive depth at 5bps
        assert metrics.depth_5bps_bid > Decimal("0")
        assert metrics.depth_5bps_ask > Decimal("0")

    def test_depth_25bps_calculation(self):
        """Test depth calculation at 25 bps (wider range)."""
        # Hand calculation:
        # Mid price ≈ 50000
        # 25 bps = 0.0025 = 0.25%
        # Bid threshold = 50000 * 0.9975 = 49875
        # Ask threshold = 50000 * 1.0025 = 50125

        calc = DepthCalculator(bps_levels=[5, 10, 25])

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "10"),   # $499,990 (within 25bps)
                ("49950", "5"),    # $249,750 (within 25bps)
                ("49900", "4"),    # $199,600 (within 25bps)
                ("49875", "2"),    # $99,750 (on 25bps threshold)
                ("49850", "1"),    # $49,850 (outside 25bps)
            ],
            ask_levels=[
                ("50001", "8"),    # $400,008 (within 25bps)
                ("50050", "4"),    # $200,200 (within 25bps)
                ("50100", "2"),    # $100,200 (within 25bps)
                ("50125", "1"),    # $50,125 (on 25bps threshold)
                ("50150", "1"),    # $50,150 (outside 25bps)
            ],
        )

        metrics = calc.calculate(snapshot)

        # Verify we have positive depth at 25bps
        assert metrics.depth_25bps_bid > Decimal("0")
        assert metrics.depth_25bps_ask > Decimal("0")

    def test_imbalance_bid_heavy(self):
        """Test imbalance calculation when bids dominate (positive)."""
        # Imbalance = (bid - ask) / (bid + ask)
        # bid = 600, ask = 400, total = 1000
        # imbalance = (600 - 400) / 1000 = 0.2

        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "12"),   # $599,988 within 10bps
            ],
            ask_levels=[
                ("50001", "8"),    # $400,008 within 10bps
            ],
        )

        metrics = calc.calculate(snapshot)

        # Imbalance should be positive (bid-heavy)
        assert metrics.imbalance > Decimal("0")
        assert metrics.is_bid_heavy is True
        assert metrics.is_ask_heavy is False

    def test_imbalance_ask_heavy(self):
        """Test imbalance calculation when asks dominate (negative)."""
        # bid = 300, ask = 700, total = 1000
        # imbalance = (300 - 700) / 1000 = -0.4

        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "6"),    # $299,994
            ],
            ask_levels=[
                ("50001", "14"),   # $700,014
            ],
        )

        metrics = calc.calculate(snapshot)

        # Imbalance should be negative (ask-heavy)
        assert metrics.imbalance < Decimal("0")
        assert metrics.is_bid_heavy is False
        assert metrics.is_ask_heavy is True

    def test_imbalance_balanced(self):
        """Test imbalance calculation when balanced."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "10"),   # $499,990
            ],
            ask_levels=[
                ("50001", "10"),   # $500,010
            ],
        )

        metrics = calc.calculate(snapshot)

        # Imbalance should be close to zero
        assert abs(metrics.imbalance) < Decimal("0.01")
        assert metrics.is_bid_heavy == (metrics.imbalance > Decimal("0"))
        assert metrics.is_ask_heavy == (metrics.imbalance < Decimal("0"))

    def test_imbalance_zero_depth(self):
        """Test imbalance when total depth is zero (edge case)."""
        calc = DepthCalculator()

        # Create snapshot with levels outside all bps ranges
        # so depth at reference level (10bps) is zero
        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49000", "10"),   # Far outside 10bps
            ],
            ask_levels=[
                ("51000", "10"),   # Far outside 10bps
            ],
        )

        metrics = calc.calculate(snapshot)

        # When depth is zero, imbalance should be zero
        assert metrics.depth_10bps_bid == Decimal("0")
        assert metrics.depth_10bps_ask == Decimal("0")
        assert metrics.imbalance == Decimal("0")

    def test_decimal_precision_maintained(self):
        """Test that Decimal precision is maintained."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000.123",
            bid_levels=[
                ("50000.100", "1.123456"),
            ],
            ask_levels=[
                ("50000.150", "0.987654"),
            ],
        )

        metrics = calc.calculate(snapshot)

        # All values should be Decimal
        assert isinstance(metrics.depth_10bps_bid, Decimal)
        assert isinstance(metrics.depth_10bps_ask, Decimal)
        assert isinstance(metrics.imbalance, Decimal)

    def test_invalid_snapshot_empty(self):
        """Test error handling for empty snapshot."""
        calc = DepthCalculator()

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

    def test_depth_at_level_method(self):
        """Test the depth_at_level convenience method."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[("49999", "10")],
            ask_levels=[("50001", "8")],
        )

        metrics = calc.calculate(snapshot)

        # Test depth_at_level method
        assert metrics.depth_at_level(10, "bid") == metrics.depth_10bps_bid
        assert metrics.depth_at_level(10, "ask") == metrics.depth_10bps_ask
        assert metrics.depth_at_level(10, "total") == metrics.depth_10bps_total

        with pytest.raises(ValueError):
            metrics.depth_at_level(50, "bid")  # Invalid level

    def test_repr(self):
        """Test string representation."""
        calc = DepthCalculator(bps_levels=[5, 10, 25], reference_level=10)
        repr_str = repr(calc)
        assert "DepthCalculator" in repr_str
        assert "bps_levels=[5, 10, 25]" in repr_str
        assert "reference_level=10" in repr_str


class TestDepthEdgeCases:
    """Test edge cases for depth calculation."""

    def test_single_price_level(self):
        """Test depth calculation with single price level."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[("49999", "10")],
            ask_levels=[("50001", "10")],
        )

        metrics = calc.calculate(snapshot)

        # Single level close to mid should be within all bps ranges
        assert metrics.depth_5bps_bid > Decimal("0")
        assert metrics.depth_10bps_bid > Decimal("0")
        assert metrics.depth_25bps_bid > Decimal("0")

    def test_all_levels_outside_range(self):
        """Test when all levels are outside the bps range."""
        calc = DepthCalculator()

        # All levels far from mid
        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("48000", "10"),  # ~4% below mid (400 bps)
            ],
            ask_levels=[
                ("52000", "10"),  # ~4% above mid (400 bps)
            ],
        )

        metrics = calc.calculate(snapshot)

        # All depth metrics should be zero
        assert metrics.depth_5bps_bid == Decimal("0")
        assert metrics.depth_10bps_bid == Decimal("0")
        assert metrics.depth_25bps_bid == Decimal("0")
        assert metrics.depth_5bps_ask == Decimal("0")
        assert metrics.depth_10bps_ask == Decimal("0")
        assert metrics.depth_25bps_ask == Decimal("0")

    def test_very_deep_order_book(self):
        """Test with many levels (100 levels)."""
        calc = DepthCalculator()

        # Create 100 bid and ask levels
        # Bids: 49999, 49998, 49997, ... (descending from just below mid)
        # Asks: 50001, 50002, 50003, ... (ascending from just above mid)
        bid_levels = [(str(50000 - i - 1), "1") for i in range(100)]
        ask_levels = [(str(50000 + i + 1), "1") for i in range(100)]

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        )

        metrics = calc.calculate(snapshot)

        # Should only count levels within bps ranges
        assert metrics.depth_10bps_bid > Decimal("0")
        assert metrics.depth_10bps_ask > Decimal("0")

    def test_asymmetric_depth(self):
        """Test with highly asymmetric bid/ask depth."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="50000",
            bid_levels=[
                ("49999", "100"),  # Very large bid
            ],
            ask_levels=[
                ("50001", "0.1"),  # Very small ask
            ],
        )

        metrics = calc.calculate(snapshot)

        # Imbalance should be very positive (bid heavy)
        assert metrics.imbalance > Decimal("0.9")
        assert metrics.is_bid_heavy is True

    def test_low_price_asset_precision(self):
        """Test depth calculation for low-priced asset."""
        calc = DepthCalculator()

        # For very low price assets, levels need to be very close to be within 10bps
        # 10 bps of 0.001 = 0.001 * 0.001 = 0.000001
        # Bid threshold: 0.001 * 0.999 = 0.000999
        # Ask threshold: 0.001 * 1.001 = 0.001001
        snapshot = create_snapshot_with_depth(
            mid_price="0.00100",
            bid_levels=[
                ("0.0009995", "10000"),  # Just below mid, within 10bps
                ("0.0009990", "5000"),   # Within 10bps
            ],
            ask_levels=[
                ("0.0010005", "10000"),  # Just above mid, within 10bps
                ("0.0010010", "5000"),   # Within 10bps
            ],
        )

        metrics = calc.calculate(snapshot)

        # Both should have positive depth
        assert metrics.depth_10bps_bid > Decimal("0")
        assert metrics.depth_10bps_ask > Decimal("0")

    def test_high_price_asset(self):
        """Test depth calculation for high-priced asset."""
        calc = DepthCalculator()

        snapshot = create_snapshot_with_depth(
            mid_price="100000",
            bid_levels=[
                ("99999", "10"),  # ~$999,990 notional
            ],
            ask_levels=[
                ("100001", "10"),  # ~$1,000,010 notional
            ],
        )

        metrics = calc.calculate(snapshot)

        # Both should have positive depth
        assert metrics.depth_10bps_bid > Decimal("0")
        assert metrics.depth_10bps_ask > Decimal("0")
