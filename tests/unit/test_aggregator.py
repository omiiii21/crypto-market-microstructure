"""
Unit tests for MetricsAggregator.

Tests integration of all calculators through the aggregator.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.metrics.aggregator import MetricsAggregator
from src.models.orderbook import OrderBookSnapshot, PriceLevel
from src.models.metrics import AggregatedMetrics


def create_snapshot(
    bid: str,
    ask: str,
    bid_qty: str = "10.0",
    ask_qty: str = "8.0",
    instrument: str = "BTC-USDT-PERP",
    exchange: str = "binance",
) -> OrderBookSnapshot:
    """Helper to create order book snapshot."""
    return OrderBookSnapshot(
        exchange=exchange,
        instrument=instrument,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
        sequence_id=12345,
        bids=[
            PriceLevel(price=Decimal(bid), quantity=Decimal(bid_qty)),
            PriceLevel(price=Decimal(str(float(bid) * 0.9995)), quantity=Decimal("5.0")),
        ],
        asks=[
            PriceLevel(price=Decimal(ask), quantity=Decimal(ask_qty)),
            PriceLevel(price=Decimal(str(float(ask) * 1.0005)), quantity=Decimal("4.0")),
        ],
    )


class TestMetricsAggregator:
    """Test suite for MetricsAggregator."""

    def test_initialization_default(self):
        """Test default initialization."""
        agg = MetricsAggregator()
        assert agg.use_zscore is True
        assert agg.spread_calc is not None
        assert agg.depth_calc is not None
        assert agg.basis_calc is not None

    def test_initialization_custom(self):
        """Test custom initialization."""
        agg = MetricsAggregator(
            use_zscore=False,
            zscore_window=50,
            bps_levels=[10, 25],
            depth_reference_level=10,
        )
        assert agg.use_zscore is False
        assert agg.depth_calc.bps_levels == [10, 25]

    def test_calculate_all_perp_with_spot(self):
        """Test calculate_all with both perp and spot (full metrics)."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("50050", "50051", instrument="BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")

        metrics = agg.calculate_all(perp=perp, spot=spot)

        assert isinstance(metrics, AggregatedMetrics)
        assert metrics.exchange == "binance"
        assert metrics.instrument == "BTC-USDT-PERP"

        # Verify all metric components present
        assert metrics.spread is not None
        assert metrics.depth is not None
        assert metrics.basis is not None
        assert metrics.imbalance is not None

        # Verify has_basis property
        assert metrics.has_basis is True

        # Spot check some values
        assert metrics.spread.spread_abs > Decimal("0")
        assert metrics.depth.depth_10bps_total > Decimal("0")
        assert metrics.basis.basis_abs != Decimal("0")  # Perp and spot differ

    def test_calculate_all_spot_only(self):
        """Test calculate_all with spot only (no basis)."""
        agg = MetricsAggregator(use_zscore=False)

        spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")

        metrics = agg.calculate_all(perp=spot)

        assert isinstance(metrics, AggregatedMetrics)
        assert metrics.exchange == "binance"
        assert metrics.instrument == "BTC-USDT-SPOT"

        # Verify metrics present
        assert metrics.spread is not None
        assert metrics.depth is not None
        assert metrics.imbalance is not None

        # Basis should be None (no spot reference provided)
        assert metrics.basis is None
        assert metrics.has_basis is False

    def test_spread_metrics_component(self):
        """Test that spread metrics are correctly calculated."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("50000.50", "50000.75")

        metrics = agg.calculate_all(perp=perp)

        # Hand calculation:
        # spread_abs = 0.25, mid = 50000.625
        assert metrics.spread.spread_abs == Decimal("0.25")
        assert metrics.spread.mid_price == Decimal("50000.625")

    def test_depth_metrics_component(self):
        """Test that depth metrics are correctly calculated."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("49999", "50001", bid_qty="10", ask_qty="8")

        metrics = agg.calculate_all(perp=perp)

        # Should have depth at all levels
        assert metrics.depth.depth_5bps_total > Decimal("0")
        assert metrics.depth.depth_10bps_total > Decimal("0")
        assert metrics.depth.depth_25bps_total > Decimal("0")

    def test_basis_metrics_component(self):
        """Test that basis metrics are correctly calculated."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("50050", "50051", instrument="BTC-USDT-PERP")
        spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")

        metrics = agg.calculate_all(perp=perp, spot=spot)

        # Basis should be positive (perp at premium)
        assert metrics.basis is not None
        assert metrics.basis.basis_abs == Decimal("50")
        assert metrics.basis.is_premium is True

    def test_imbalance_metrics_component(self):
        """Test that imbalance metrics are correctly calculated."""
        agg = MetricsAggregator(use_zscore=False)

        # Bid-heavy order book
        perp = create_snapshot("49999", "50001", bid_qty="100", ask_qty="50")

        metrics = agg.calculate_all(perp=perp)

        # Should show bid-heavy imbalance
        assert metrics.imbalance.top_of_book_imbalance > Decimal("0")
        assert metrics.imbalance.weighted_imbalance_5 > Decimal("0")
        assert metrics.imbalance.weighted_imbalance_10 > Decimal("0")

    def test_imbalance_top_of_book(self):
        """Test top of book imbalance calculation."""
        agg = MetricsAggregator(use_zscore=False)

        # Create snapshot with known top-of-book quantities
        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("10"))],
            asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("6"))],
        )

        metrics = agg.calculate_all(perp=perp)

        # Imbalance = (10 - 6) / (10 + 6) = 4/16 = 0.25
        expected = (Decimal("10") - Decimal("6")) / (Decimal("10") + Decimal("6"))
        assert metrics.imbalance.top_of_book_imbalance == expected

    def test_imbalance_weighted_5_levels(self):
        """Test weighted imbalance across top 5 levels."""
        agg = MetricsAggregator(use_zscore=False)

        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[
                PriceLevel(price=Decimal("50000"), quantity=Decimal("10")),
                PriceLevel(price=Decimal("49995"), quantity=Decimal("8")),
                PriceLevel(price=Decimal("49990"), quantity=Decimal("6")),
                PriceLevel(price=Decimal("49985"), quantity=Decimal("4")),
                PriceLevel(price=Decimal("49980"), quantity=Decimal("2")),
            ],
            asks=[
                PriceLevel(price=Decimal("50001"), quantity=Decimal("5")),
                PriceLevel(price=Decimal("50006"), quantity=Decimal("4")),
                PriceLevel(price=Decimal("50011"), quantity=Decimal("3")),
                PriceLevel(price=Decimal("50016"), quantity=Decimal("2")),
                PriceLevel(price=Decimal("50021"), quantity=Decimal("1")),
            ],
        )

        metrics = agg.calculate_all(perp=perp)

        # Should be positive (more bid volume)
        assert metrics.imbalance.weighted_imbalance_5 > Decimal("0")

    def test_reset_all_zscores(self):
        """Test reset_all_zscores method."""
        agg = MetricsAggregator(use_zscore=True, zscore_min_samples=5)

        # Add samples
        for i in range(10):
            perp = create_snapshot(f"5000{i}", f"5000{i+1}", instrument="BTC-USDT-PERP")
            spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")
            agg.calculate_all(perp=perp, spot=spot)

        # Verify z-score calculators have data
        statuses = agg.zscore_statuses
        assert statuses["spread"].samples_collected > 0
        assert statuses["basis"].samples_collected > 0

        # Reset all
        agg.reset_all_zscores(reason="Test reset")

        # Verify all cleared
        statuses = agg.zscore_statuses
        assert statuses["spread"].samples_collected == 0
        assert statuses["basis"].samples_collected == 0

    def test_zscore_statuses_property(self):
        """Test zscore_statuses property."""
        agg = MetricsAggregator(use_zscore=True)

        statuses = agg.zscore_statuses
        assert isinstance(statuses, dict)
        assert "spread" in statuses
        assert "basis" in statuses
        assert statuses["spread"] is not None
        assert statuses["basis"] is not None

    def test_zscore_statuses_disabled(self):
        """Test zscore_statuses when z-score disabled."""
        agg = MetricsAggregator(use_zscore=False)

        statuses = agg.zscore_statuses
        assert statuses["spread"] is None
        assert statuses["basis"] is None

    def test_invalid_perp_snapshot(self):
        """Test error handling for invalid perp snapshot."""
        agg = MetricsAggregator(use_zscore=False)

        invalid_perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1),
            local_timestamp=datetime(2024, 1, 1),
            sequence_id=12345,
            bids=[],
            asks=[],
        )

        with pytest.raises(ValueError, match="Invalid primary snapshot"):
            agg.calculate_all(perp=invalid_perp)

    def test_multiple_sequential_calculations(self):
        """Test multiple sequential calculations."""
        agg = MetricsAggregator(use_zscore=False)

        for i in range(5):
            perp = create_snapshot(
                f"5000{i}",
                f"5000{i+1}",
                instrument="BTC-USDT-PERP"
            )
            spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")

            metrics = agg.calculate_all(perp=perp, spot=spot)

            assert isinstance(metrics, AggregatedMetrics)
            assert metrics.spread is not None
            assert metrics.depth is not None
            assert metrics.basis is not None

    def test_different_exchanges(self):
        """Test aggregator works with different exchanges."""
        agg = MetricsAggregator(use_zscore=False)

        for exchange in ["binance", "okx"]:
            perp = create_snapshot(
                "50000",
                "50001",
                exchange=exchange,
                instrument="BTC-USDT-PERP"
            )

            metrics = agg.calculate_all(perp=perp)

            assert metrics.exchange == exchange

    def test_different_instruments(self):
        """Test aggregator works with different instruments."""
        agg = MetricsAggregator(use_zscore=False)

        for instrument in ["BTC-USDT-PERP", "ETH-USDT-PERP"]:
            perp = create_snapshot("50000", "50001", instrument=instrument)

            metrics = agg.calculate_all(perp=perp)

            assert metrics.instrument == instrument

    def test_timestamp_preservation(self):
        """Test that timestamp is preserved from input snapshot."""
        agg = MetricsAggregator(use_zscore=False)

        test_time = datetime(2024, 6, 15, 12, 30, 45)
        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=test_time,
            local_timestamp=datetime(2024, 6, 15, 12, 30, 46),
            sequence_id=12345,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("10"))],
            asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("8"))],
        )

        metrics = agg.calculate_all(perp=perp)

        assert metrics.timestamp == test_time

    def test_repr(self):
        """Test string representation."""
        agg = MetricsAggregator(use_zscore=True)
        repr_str = repr(agg)
        assert "MetricsAggregator" in repr_str
        assert "use_zscore=True" in repr_str


class TestMetricsAggregatorEdgeCases:
    """Test edge cases for the aggregator."""

    def test_minimal_order_book(self):
        """Test with minimal order book (1 level each side)."""
        agg = MetricsAggregator(use_zscore=False)

        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("10"))],
            asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("8"))],
        )

        metrics = agg.calculate_all(perp=perp)

        # Should still produce valid metrics
        assert metrics.spread.spread_abs == Decimal("1")
        assert metrics.depth.depth_10bps_total > Decimal("0")

    def test_deep_order_book(self):
        """Test with deep order book (many levels)."""
        agg = MetricsAggregator(use_zscore=False)

        # Create 50 levels each side
        bids = [
            PriceLevel(price=Decimal(str(50000 - i)), quantity=Decimal("1"))
            for i in range(50)
        ]
        asks = [
            PriceLevel(price=Decimal(str(50001 + i)), quantity=Decimal("1"))
            for i in range(50)
        ]

        perp = OrderBookSnapshot(
            exchange="binance",
            instrument="BTC-USDT-PERP",
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            local_timestamp=datetime(2024, 1, 1, 0, 0, 0),
            sequence_id=12345,
            bids=bids,
            asks=asks,
        )

        metrics = agg.calculate_all(perp=perp)

        # Should handle deep book correctly
        assert metrics.spread.spread_abs == Decimal("1")
        assert metrics.depth.depth_10bps_total > Decimal("0")

    def test_imbalance_extreme_bid_heavy(self):
        """Test imbalance with extremely bid-heavy book."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("50000", "50001", bid_qty="1000", ask_qty="1")

        metrics = agg.calculate_all(perp=perp)

        # Should show strong bid imbalance
        assert metrics.imbalance.top_of_book_imbalance > Decimal("0.9")

    def test_imbalance_extreme_ask_heavy(self):
        """Test imbalance with extremely ask-heavy book."""
        agg = MetricsAggregator(use_zscore=False)

        perp = create_snapshot("50000", "50001", bid_qty="1", ask_qty="1000")

        metrics = agg.calculate_all(perp=perp)

        # Should show strong ask imbalance
        assert metrics.imbalance.top_of_book_imbalance < Decimal("-0.9")

    def test_zscore_warmup_period(self):
        """Test that z-scores are None during warmup."""
        agg = MetricsAggregator(use_zscore=True, zscore_min_samples=30)

        # Add fewer than min_samples
        for i in range(20):
            perp = create_snapshot(f"5000{i}", f"5000{i+1}", instrument="BTC-USDT-PERP")
            spot = create_snapshot("50000", "50001", instrument="BTC-USDT-SPOT")
            metrics = agg.calculate_all(perp=perp, spot=spot)

            # Z-scores should be None during warmup
            assert metrics.spread.zscore is None
            assert metrics.basis.zscore is None
