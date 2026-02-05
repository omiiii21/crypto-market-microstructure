"""
Metrics Aggregator for combining all metric calculators.

This module provides a unified interface to compute all metrics from
order book snapshots in a single call, managing separate z-score
calculators for each metric type.

Classes:
    MetricsAggregator: Aggregates all metric calculators
"""

from typing import List, Optional

from src.models.metrics import (
    AggregatedMetrics,
    BasisMetrics,
    DepthMetrics,
    ImbalanceMetrics,
    SpreadMetrics,
)
from src.models.orderbook import OrderBookSnapshot
from src.metrics.basis import BasisCalculator
from src.metrics.depth import DepthCalculator
from src.metrics.spread import SpreadCalculator
from decimal import Decimal


class MetricsAggregator:
    """
    Aggregates all metric calculators into a unified interface.

    This class manages individual calculators (spread, depth, basis) and
    combines their outputs into a comprehensive AggregatedMetrics package.
    Each calculator maintains its own z-score tracker for independent
    statistical analysis.

    Example:
        >>> aggregator = MetricsAggregator(
        ...     use_zscore=True,
        ...     zscore_window=100,
        ...     bps_levels=[5, 10, 25]
        ... )
        >>>
        >>> # For perpetual futures (with spot reference)
        >>> perp_snapshot = OrderBookSnapshot(...)
        >>> spot_snapshot = OrderBookSnapshot(...)
        >>> metrics = aggregator.calculate_all(
        ...     perp=perp_snapshot,
        ...     spot=spot_snapshot
        ... )
        >>> print(f"Spread: {metrics.spread.spread_bps} bps")
        >>> print(f"Basis: {metrics.basis.basis_bps} bps")
        >>> print(f"Depth at 10bps: ${metrics.depth.depth_10bps_total:,.0f}")
        >>>
        >>> # For spot only (no basis calculation)
        >>> spot_metrics = aggregator.calculate_all(perp=spot_snapshot)

    Attributes:
        spread_calc: SpreadCalculator instance.
        depth_calc: DepthCalculator instance.
        basis_calc: BasisCalculator instance.
        use_zscore: Whether z-score tracking is enabled.
    """

    def __init__(
        self,
        use_zscore: bool = True,
        zscore_window: int = 100,
        zscore_min_samples: Optional[int] = None,
        bps_levels: List[int] | None = None,
        depth_reference_level: int = 10,
        validate_basis_instruments: bool = True,
    ) -> None:
        """
        Initialize the metrics aggregator.

        Args:
            use_zscore: Enable z-score tracking for all calculators (default: True).
            zscore_window: Rolling window size for z-scores (default: 100).
            zscore_min_samples: Minimum samples for z-score (default: 30).
            bps_levels: Depth levels to calculate (default: [5, 10, 25]).
            depth_reference_level: Depth level for imbalance (default: 10).
            validate_basis_instruments: Validate perp/spot match (default: True).
        """
        self.use_zscore = use_zscore

        # Initialize individual calculators
        self.spread_calc = SpreadCalculator(
            use_zscore=use_zscore,
            zscore_window=zscore_window,
            zscore_min_samples=zscore_min_samples,
        )

        self.depth_calc = DepthCalculator(
            bps_levels=bps_levels,
            reference_level=depth_reference_level,
        )

        self.basis_calc = BasisCalculator(
            use_zscore=use_zscore,
            zscore_window=zscore_window,
            zscore_min_samples=zscore_min_samples,
            validate_instruments=validate_basis_instruments,
        )

    def calculate_all(
        self,
        perp: OrderBookSnapshot,
        spot: Optional[OrderBookSnapshot] = None,
    ) -> AggregatedMetrics:
        """
        Calculate all metrics from order book snapshots.

        This is the main entry point for computing comprehensive metrics.
        For perpetual futures, provide both perp and spot snapshots to
        calculate basis metrics. For spot only, omit the spot parameter.

        Args:
            perp: Primary order book snapshot (perpetual or spot).
            spot: Optional spot snapshot for basis calculation.

        Returns:
            AggregatedMetrics: Complete metrics package including spread,
                              depth, basis (if applicable), and imbalance.

        Raises:
            ValueError: If snapshots are invalid.

        Example:
            >>> # Perpetual with basis
            >>> metrics = aggregator.calculate_all(perp=perp_snap, spot=spot_snap)
            >>> assert metrics.has_basis
            >>>
            >>> # Spot only (no basis)
            >>> metrics = aggregator.calculate_all(perp=spot_snap)
            >>> assert not metrics.has_basis
        """
        # Validate primary snapshot
        if not perp.is_valid:
            raise ValueError(
                f"Invalid primary snapshot: exchange={perp.exchange}, "
                f"instrument={perp.instrument}"
            )

        # Calculate spread metrics
        spread = self.spread_calc.calculate(perp)

        # Calculate depth metrics
        depth = self.depth_calc.calculate(perp)

        # Calculate imbalance metrics
        imbalance = self._calculate_imbalance_metrics(perp)

        # Calculate basis metrics if spot provided
        basis: Optional[BasisMetrics] = None
        if spot is not None:
            basis = self.basis_calc.calculate(perp, spot)

        # Construct aggregated metrics
        return AggregatedMetrics(
            exchange=perp.exchange,
            instrument=perp.instrument,
            timestamp=perp.timestamp,
            spread=spread,
            depth=depth,
            basis=basis,
            imbalance=imbalance,
        )

    def _calculate_imbalance_metrics(
        self, snapshot: OrderBookSnapshot
    ) -> ImbalanceMetrics:
        """
        Calculate imbalance metrics at different levels.

        This computes multiple imbalance ratios:
        - Top of book: Best bid/ask only
        - Weighted 5: Volume-weighted across top 5 levels
        - Weighted 10: Volume-weighted across top 10 levels

        Args:
            snapshot: Order book snapshot.

        Returns:
            ImbalanceMetrics: Imbalance ratios at different depths.
        """
        # Top of book imbalance (best bid/ask quantity)
        best_bid_qty = snapshot.best_bid_quantity or Decimal("0")
        best_ask_qty = snapshot.best_ask_quantity or Decimal("0")
        top_of_book = self._calc_imbalance_ratio(best_bid_qty, best_ask_qty)

        # Weighted imbalance across top N levels
        weighted_5 = self._calc_weighted_imbalance(snapshot, levels=5)
        weighted_10 = self._calc_weighted_imbalance(snapshot, levels=10)

        return ImbalanceMetrics(
            top_of_book_imbalance=top_of_book,
            weighted_imbalance_5=weighted_5,
            weighted_imbalance_10=weighted_10,
        )

    def _calc_imbalance_ratio(
        self, bid_value: Decimal, ask_value: Decimal
    ) -> Decimal:
        """
        Calculate imbalance ratio from bid and ask values.

        Formula: (bid - ask) / (bid + ask)

        Args:
            bid_value: Bid side value (quantity or notional).
            ask_value: Ask side value (quantity or notional).

        Returns:
            Decimal: Imbalance ratio in [-1, 1].
        """
        total = bid_value + ask_value
        if total == Decimal("0"):
            return Decimal("0")
        return (bid_value - ask_value) / total

    def _calc_weighted_imbalance(
        self, snapshot: OrderBookSnapshot, levels: int
    ) -> Decimal:
        """
        Calculate volume-weighted imbalance across top N levels.

        Args:
            snapshot: Order book snapshot.
            levels: Number of levels to include.

        Returns:
            Decimal: Weighted imbalance ratio.
        """
        # Sum notional across top N levels
        bid_notional = sum(
            (level.notional for level in snapshot.bids[:levels]),
            Decimal("0")
        )
        ask_notional = sum(
            (level.notional for level in snapshot.asks[:levels]),
            Decimal("0")
        )

        return self._calc_imbalance_ratio(bid_notional, ask_notional)

    def reset_all_zscores(self, reason: Optional[str] = None) -> None:
        """
        Reset all z-score calculators.

        Should be called on gap detection or regime changes to prevent
        stale data from affecting calculations.

        Args:
            reason: Optional reason for reset (for logging).

        Example:
            >>> aggregator.reset_all_zscores(reason="Data gap detected")
        """
        self.spread_calc.reset_zscore(reason)
        self.basis_calc.reset_zscore(reason)

    @property
    def zscore_statuses(self) -> dict:
        """
        Get z-score status for all calculators.

        Returns:
            dict: Dictionary with calculator names and their z-score statuses.

        Example:
            >>> statuses = aggregator.zscore_statuses
            >>> if statuses['spread'] and statuses['spread'].is_ready:
            ...     print("Spread z-score ready")
        """
        return {
            "spread": self.spread_calc.zscore_status,
            "basis": self.basis_calc.zscore_status,
        }

    def __repr__(self) -> str:
        """String representation of the aggregator."""
        return (
            f"MetricsAggregator(use_zscore={self.use_zscore}, "
            f"spread_status={self.spread_calc.zscore_status}, "
            f"basis_status={self.basis_calc.zscore_status})"
        )
