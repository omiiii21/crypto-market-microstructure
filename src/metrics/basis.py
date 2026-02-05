"""
Basis Calculator for perpetual-spot basis metrics.

This module calculates the basis (price difference) between perpetual
futures and spot markets, a critical indicator of funding rate expectations
and market sentiment.

Key Formulas:
    basis_absolute = perp_mid - spot_mid
    basis_bps = (basis_absolute / spot_mid) * 10000

Classes:
    BasisCalculator: Calculator for perp-spot basis metrics
"""

from decimal import Decimal
from typing import Optional

from src.models.metrics import BasisMetrics
from src.models.orderbook import OrderBookSnapshot
from src.metrics.zscore import ZScoreCalculator


class BasisCalculator:
    """
    Calculator for perpetual-spot basis metrics.

    Computes the basis between perpetual futures and spot markets with full
    Decimal precision. Optionally tracks z-scores for anomaly detection.

    Formulas:
        basis_abs = perp_mid - spot_mid
        basis_bps = (basis_abs / spot_mid) * 10000

    Interpretation:
        Positive basis: Perp trading at premium (expect positive funding)
        Negative basis: Perp trading at discount (expect negative funding)

    Edge Cases Handled:
        - Invalid snapshots: Raises ValueError
        - Mismatched instruments: Warns but proceeds (validation optional)
        - Zero spot mid: Raises ValueError (cannot compute bps)
        - Missing data: Raises ValueError

    Example:
        >>> calc = BasisCalculator(use_zscore=True)
        >>> perp_snapshot = OrderBookSnapshot(...)  # BTC-USDT-PERP
        >>> spot_snapshot = OrderBookSnapshot(...)  # BTC-USDT-SPOT
        >>> metrics = calc.calculate(perp_snapshot, spot_snapshot)
        >>> print(f"Basis: {metrics.basis_bps} bps")
        >>> if metrics.is_premium:
        ...     print("Perp trading at premium to spot")

    Attributes:
        use_zscore: Whether to compute z-scores for basis_bps.
        zscore_calculator: Optional z-score calculator instance.
        validate_instruments: Whether to validate instrument names match.
    """

    def __init__(
        self,
        use_zscore: bool = True,
        zscore_window: int = 100,
        zscore_min_samples: Optional[int] = None,
        validate_instruments: bool = True,
    ) -> None:
        """
        Initialize the basis calculator.

        Args:
            use_zscore: Whether to compute z-scores (default: True).
            zscore_window: Rolling window size for z-score (default: 100).
            zscore_min_samples: Minimum samples for z-score (default: 30).
            validate_instruments: Validate perp/spot names match (default: True).
        """
        self.use_zscore = use_zscore
        self.validate_instruments = validate_instruments
        self.zscore_calculator: Optional[ZScoreCalculator] = None

        if self.use_zscore:
            self.zscore_calculator = ZScoreCalculator(
                window_size=zscore_window,
                min_samples=zscore_min_samples,
            )

    def calculate(
        self,
        perp_snapshot: OrderBookSnapshot,
        spot_snapshot: OrderBookSnapshot,
    ) -> BasisMetrics:
        """
        Calculate basis metrics from perpetual and spot snapshots.

        Args:
            perp_snapshot: Perpetual futures order book snapshot.
            spot_snapshot: Spot order book snapshot.

        Returns:
            BasisMetrics: Computed basis metrics including absolute basis,
                         basis points, mid prices, and optional z-score.

        Raises:
            ValueError: If snapshots are invalid or incompatible.

        Example:
            >>> perp = OrderBookSnapshot(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     bids=[PriceLevel(Decimal("50050"), Decimal("1.0"))],
            ...     asks=[PriceLevel(Decimal("50051"), Decimal("0.5"))],
            ...     ...
            ... )
            >>> spot = OrderBookSnapshot(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-SPOT",
            ...     bids=[PriceLevel(Decimal("50000"), Decimal("1.0"))],
            ...     asks=[PriceLevel(Decimal("50001"), Decimal("0.5"))],
            ...     ...
            ... )
            >>> calc = BasisCalculator()
            >>> metrics = calc.calculate(perp, spot)
            >>> # perp_mid = 50050.5, spot_mid = 50000.5
            >>> # basis_abs = 50050.5 - 50000.5 = 50
            >>> # basis_bps = (50 / 50000.5) * 10000 = 9.9999...
        """
        # Validate both snapshots
        if not perp_snapshot.is_valid:
            raise ValueError(
                f"Invalid perpetual snapshot: exchange={perp_snapshot.exchange}, "
                f"instrument={perp_snapshot.instrument}"
            )

        if not spot_snapshot.is_valid:
            raise ValueError(
                f"Invalid spot snapshot: exchange={spot_snapshot.exchange}, "
                f"instrument={spot_snapshot.instrument}"
            )

        # Optional: Validate instruments match base asset
        if self.validate_instruments:
            self._validate_instruments(perp_snapshot.instrument, spot_snapshot.instrument)

        # Extract mid prices
        perp_mid = perp_snapshot.mid_price
        spot_mid = spot_snapshot.mid_price

        if perp_mid is None or spot_mid is None:
            raise ValueError(
                f"Missing mid price: perp={perp_mid}, spot={spot_mid}"
            )

        # Validate spot mid is positive (needed for bps calculation)
        if spot_mid <= Decimal("0"):
            raise ValueError(f"Invalid spot mid price: {spot_mid}")

        # Calculate absolute basis
        basis_abs = perp_mid - spot_mid

        # Calculate basis in basis points
        basis_bps = (basis_abs / spot_mid) * Decimal("10000")

        # Calculate z-score if enabled
        zscore: Optional[Decimal] = None
        if self.use_zscore and self.zscore_calculator is not None:
            # Use absolute value of basis for z-score (care about magnitude)
            zscore = self.zscore_calculator.add_sample(
                abs(basis_bps), perp_snapshot.timestamp
            )

        # Return metrics
        return BasisMetrics(
            basis_abs=basis_abs,
            basis_bps=basis_bps,
            perp_mid=perp_mid,
            spot_mid=spot_mid,
            zscore=zscore,
        )

    def _validate_instruments(self, perp_instrument: str, spot_instrument: str) -> None:
        """
        Validate that perpetual and spot instruments match base asset.

        Args:
            perp_instrument: Perpetual instrument name (e.g., "BTC-USDT-PERP").
            spot_instrument: Spot instrument name (e.g., "BTC-USDT-SPOT").

        Raises:
            ValueError: If instruments don't appear to match.

        Note:
            This is a basic validation that checks if base-quote pairs match.
            More sophisticated validation could be added if needed.
        """
        # Extract base-quote from instrument names
        # Expected format: BASE-QUOTE-TYPE (e.g., BTC-USDT-PERP, BTC-USDT-SPOT)
        try:
            perp_parts = perp_instrument.split("-")
            spot_parts = spot_instrument.split("-")

            if len(perp_parts) < 3 or len(spot_parts) < 3:
                raise ValueError(f"Invalid instrument format")

            perp_base_quote = f"{perp_parts[0]}-{perp_parts[1]}"
            spot_base_quote = f"{spot_parts[0]}-{spot_parts[1]}"

            if perp_base_quote != spot_base_quote:
                raise ValueError(
                    f"Instrument mismatch: perp={perp_base_quote}, spot={spot_base_quote}"
                )

            # Validate PERP and SPOT suffixes
            if not perp_instrument.endswith("PERP"):
                raise ValueError(f"Expected PERP instrument, got: {perp_instrument}")

            if not spot_instrument.endswith("SPOT"):
                raise ValueError(f"Expected SPOT instrument, got: {spot_instrument}")

        except (IndexError, ValueError) as e:
            raise ValueError(
                f"Failed to validate instruments: perp={perp_instrument}, "
                f"spot={spot_instrument}, error={e}"
            )

    def reset_zscore(self, reason: Optional[str] = None) -> None:
        """
        Reset the z-score calculator.

        Should be called on gap detection or regime changes.

        Args:
            reason: Optional reason for reset (for logging).

        Example:
            >>> calc = BasisCalculator(use_zscore=True)
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
        return (
            f"BasisCalculator(use_zscore={self.use_zscore}, "
            f"validate_instruments={self.validate_instruments}, "
            f"status={self.zscore_status})"
        )
