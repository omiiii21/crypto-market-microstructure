"""
Spread Calculator for bid-ask spread metrics.

This module calculates spread-related metrics with full Decimal precision
to avoid floating-point errors in financial calculations.

Key Formulas:
    mid_price = (best_bid + best_ask) / 2
    spread_absolute = best_ask - best_bid
    spread_bps = (spread_absolute / mid_price) * 10000

Classes:
    SpreadCalculator: Calculator for spread metrics from order book snapshots
"""

from decimal import Decimal
from typing import Optional

from src.models.metrics import SpreadMetrics
from src.models.orderbook import OrderBookSnapshot
from src.metrics.zscore import ZScoreCalculator


class SpreadCalculator:
    """
    Calculator for bid-ask spread metrics.

    Computes spread metrics from order book snapshots with full Decimal
    precision. Optionally tracks z-scores for statistical anomaly detection.

    Formulas:
        mid_price = (best_bid + best_ask) / 2
        spread_abs = best_ask - best_bid
        spread_bps = (spread_abs / mid_price) * 10000

    Edge Cases Handled:
        - Empty order book: Raises ValueError
        - Missing bid or ask: Raises ValueError
        - Zero mid price: Raises ValueError (should never occur)
        - Crossed book: Handled by OrderBookSnapshot validation

    Example:
        >>> calc = SpreadCalculator(use_zscore=True, zscore_window=100)
        >>> snapshot = OrderBookSnapshot(...)
        >>> metrics = calc.calculate(snapshot)
        >>> print(f"Spread: {metrics.spread_bps} bps")
        >>> if metrics.zscore is not None:
        ...     print(f"Z-score: {metrics.zscore}")

    Attributes:
        use_zscore: Whether to compute z-scores for spread_bps.
        zscore_calculator: Optional z-score calculator instance.
    """

    def __init__(
        self,
        use_zscore: bool = True,
        zscore_window: int = 100,
        zscore_min_samples: Optional[int] = None,
    ) -> None:
        """
        Initialize the spread calculator.

        Args:
            use_zscore: Whether to compute z-scores (default: True).
            zscore_window: Rolling window size for z-score (default: 100).
            zscore_min_samples: Minimum samples for z-score (default: 30).
        """
        self.use_zscore = use_zscore
        self.zscore_calculator: Optional[ZScoreCalculator] = None

        if self.use_zscore:
            self.zscore_calculator = ZScoreCalculator(
                window_size=zscore_window,
                min_samples=zscore_min_samples,
            )

    def calculate(self, snapshot: OrderBookSnapshot) -> SpreadMetrics:
        """
        Calculate spread metrics from an order book snapshot.

        Args:
            snapshot: Order book snapshot to analyze.

        Returns:
            SpreadMetrics: Computed spread metrics including absolute spread,
                          basis points spread, mid price, and optional z-score.

        Raises:
            ValueError: If order book is invalid (empty or missing sides).

        Example:
            >>> snapshot = OrderBookSnapshot(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     timestamp=datetime.utcnow(),
            ...     local_timestamp=datetime.utcnow(),
            ...     sequence_id=12345,
            ...     bids=[PriceLevel(price=Decimal("50000.50"), quantity=Decimal("1.0"))],
            ...     asks=[PriceLevel(price=Decimal("50000.75"), quantity=Decimal("0.5"))],
            ... )
            >>> calc = SpreadCalculator()
            >>> metrics = calc.calculate(snapshot)
            >>> # spread_abs = 50000.75 - 50000.50 = 0.25
            >>> # mid_price = (50000.50 + 50000.75) / 2 = 50000.625
            >>> # spread_bps = (0.25 / 50000.625) * 10000 = 0.04999...
        """
        # Validate snapshot has valid data
        if not snapshot.is_valid:
            raise ValueError(
                f"Invalid order book snapshot: exchange={snapshot.exchange}, "
                f"instrument={snapshot.instrument}, bids={len(snapshot.bids)}, "
                f"asks={len(snapshot.asks)}"
            )

        # Extract best bid and ask (guaranteed to exist due to is_valid check)
        best_bid = snapshot.best_bid
        best_ask = snapshot.best_ask

        if best_bid is None or best_ask is None:
            raise ValueError(
                f"Missing best bid or ask: bid={best_bid}, ask={best_ask}"
            )

        # Calculate mid price
        mid_price = (best_bid + best_ask) / Decimal("2")

        # Validate mid price is positive (should never be zero or negative)
        if mid_price <= Decimal("0"):
            raise ValueError(f"Invalid mid price: {mid_price}")

        # Calculate absolute spread
        spread_abs = best_ask - best_bid

        # Calculate spread in basis points
        spread_bps = (spread_abs / mid_price) * Decimal("10000")

        # Calculate z-score if enabled
        zscore: Optional[Decimal] = None
        if self.use_zscore and self.zscore_calculator is not None:
            zscore = self.zscore_calculator.add_sample(spread_bps, snapshot.timestamp)

        # Return metrics
        return SpreadMetrics(
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            mid_price=mid_price,
            zscore=zscore,
        )

    def reset_zscore(self, reason: Optional[str] = None) -> None:
        """
        Reset the z-score calculator.

        Should be called on gap detection or regime changes.

        Args:
            reason: Optional reason for reset (for logging).

        Example:
            >>> calc = SpreadCalculator(use_zscore=True)
            >>> calc.reset_zscore(reason="Data gap detected")
        """
        if self.zscore_calculator is not None:
            self.zscore_calculator.reset(reason)

    @property
    def zscore_status(self):
        """
        Get z-score calculator status.

        Returns:
            ZScoreStatus: Current status, or None if z-score disabled.
        """
        if self.zscore_calculator is not None:
            return self.zscore_calculator.status
        return None

    def __repr__(self) -> str:
        """String representation of the calculator."""
        return f"SpreadCalculator(use_zscore={self.use_zscore}, status={self.zscore_status})"
