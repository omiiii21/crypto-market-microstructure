"""
Depth Calculator for order book depth metrics.

This module calculates order book depth at various basis point levels from
mid price, along with imbalance ratios to detect order book pressure.

Key Formulas:
    depth_at_bps = sum(notional) for levels within N bps of mid
    imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)

Classes:
    DepthCalculator: Calculator for depth metrics from order book snapshots
"""

from decimal import Decimal
from typing import List

from src.models.metrics import DepthMetrics
from src.models.orderbook import OrderBookSnapshot


class DepthCalculator:
    """
    Calculator for order book depth metrics.

    Computes depth (notional value) at configurable basis point levels from
    mid price, for both bid and ask sides. Also calculates imbalance ratio.

    Formulas:
        For each bps level:
            upper_bound = mid_price * (1 + bps/10000)  # for asks
            lower_bound = mid_price * (1 - bps/10000)  # for bids
            depth = sum(price * quantity) for levels within bounds

        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
            Range: [-1, 1]
            Positive: bid-heavy (more buy support)
            Negative: ask-heavy (more sell pressure)

    Edge Cases Handled:
        - Empty order book: Raises ValueError
        - Zero mid price: Returns zero depth
        - Zero total depth: Returns zero imbalance (no division by zero)
        - Single price level: Calculates depth if within range

    Example:
        >>> calc = DepthCalculator(bps_levels=[5, 10, 25])
        >>> snapshot = OrderBookSnapshot(...)
        >>> metrics = calc.calculate(snapshot)
        >>> print(f"Depth at 10bps: ${metrics.depth_10bps_total:,.2f}")
        >>> print(f"Imbalance: {metrics.imbalance}")

    Attributes:
        bps_levels: List of basis point levels to calculate (default: [5, 10, 25]).
        reference_level: The bps level used for imbalance calculation (default: 10).
    """

    def __init__(
        self,
        bps_levels: List[int] | None = None,
        reference_level: int = 10,
    ) -> None:
        """
        Initialize the depth calculator.

        Args:
            bps_levels: Basis point levels to calculate (default: [5, 10, 25]).
            reference_level: bps level for imbalance calculation (default: 10).

        Raises:
            ValueError: If reference_level not in bps_levels.
        """
        self.bps_levels = bps_levels if bps_levels is not None else [5, 10, 25]
        self.reference_level = reference_level

        # Validate reference level is in bps_levels
        if self.reference_level not in self.bps_levels:
            raise ValueError(
                f"reference_level ({self.reference_level}) must be in bps_levels ({self.bps_levels})"
            )

        # Validate only 5, 10, 25 are in bps_levels (matching DepthMetrics model)
        valid_levels = {5, 10, 25}
        if not set(self.bps_levels).issubset(valid_levels):
            raise ValueError(
                f"bps_levels must only contain {valid_levels}, got {self.bps_levels}"
            )

    def calculate(self, snapshot: OrderBookSnapshot) -> DepthMetrics:
        """
        Calculate depth metrics from an order book snapshot.

        Args:
            snapshot: Order book snapshot to analyze.

        Returns:
            DepthMetrics: Computed depth metrics for all configured levels
                         plus imbalance ratio.

        Raises:
            ValueError: If order book is invalid (empty or missing sides).

        Example:
            >>> snapshot = OrderBookSnapshot(
            ...     exchange="binance",
            ...     instrument="BTC-USDT-PERP",
            ...     timestamp=datetime.utcnow(),
            ...     local_timestamp=datetime.utcnow(),
            ...     sequence_id=12345,
            ...     bids=[
            ...         PriceLevel(price=Decimal("50000"), quantity=Decimal("10")),  # $500K
            ...         PriceLevel(price=Decimal("49975"), quantity=Decimal("5")),   # $249.875K
            ...     ],
            ...     asks=[
            ...         PriceLevel(price=Decimal("50001"), quantity=Decimal("8")),   # $400.008K
            ...         PriceLevel(price=Decimal("50025"), quantity=Decimal("4")),   # $200.1K
            ...     ],
            ... )
            >>> calc = DepthCalculator()
            >>> metrics = calc.calculate(snapshot)
        """
        # Validate snapshot has valid data
        if not snapshot.is_valid:
            raise ValueError(
                f"Invalid order book snapshot: exchange={snapshot.exchange}, "
                f"instrument={snapshot.instrument}, bids={len(snapshot.bids)}, "
                f"asks={len(snapshot.asks)}"
            )

        # Get mid price
        mid_price = snapshot.mid_price
        if mid_price is None or mid_price <= Decimal("0"):
            raise ValueError(f"Invalid mid price: {mid_price}")

        # Calculate depth at each level
        depth_values = {}
        for bps in self.bps_levels:
            bid_depth = self._calculate_depth_at_bps(snapshot, mid_price, bps, "bid")
            ask_depth = self._calculate_depth_at_bps(snapshot, mid_price, bps, "ask")
            total_depth = bid_depth + ask_depth

            depth_values[f"depth_{bps}bps_bid"] = bid_depth
            depth_values[f"depth_{bps}bps_ask"] = ask_depth
            depth_values[f"depth_{bps}bps_total"] = total_depth

        # Calculate imbalance at reference level
        ref_bid = depth_values[f"depth_{self.reference_level}bps_bid"]
        ref_ask = depth_values[f"depth_{self.reference_level}bps_ask"]
        imbalance = self._calculate_imbalance(ref_bid, ref_ask)

        # Construct and return DepthMetrics
        return DepthMetrics(
            depth_5bps_bid=depth_values["depth_5bps_bid"],
            depth_5bps_ask=depth_values["depth_5bps_ask"],
            depth_5bps_total=depth_values["depth_5bps_total"],
            depth_10bps_bid=depth_values["depth_10bps_bid"],
            depth_10bps_ask=depth_values["depth_10bps_ask"],
            depth_10bps_total=depth_values["depth_10bps_total"],
            depth_25bps_bid=depth_values["depth_25bps_bid"],
            depth_25bps_ask=depth_values["depth_25bps_ask"],
            depth_25bps_total=depth_values["depth_25bps_total"],
            imbalance=imbalance,
        )

    def _calculate_depth_at_bps(
        self,
        snapshot: OrderBookSnapshot,
        mid_price: Decimal,
        bps: int,
        side: str,
    ) -> Decimal:
        """
        Calculate depth at a specific bps level for one side.

        Args:
            snapshot: Order book snapshot.
            mid_price: Mid price to calculate bounds from.
            bps: Basis points from mid.
            side: Either "bid" or "ask".

        Returns:
            Decimal: Total notional value within the bps range.

        Raises:
            ValueError: If side is invalid.
        """
        if side not in ("bid", "ask"):
            raise ValueError(f"side must be 'bid' or 'ask', got '{side}'")

        bps_decimal = Decimal(bps) / Decimal("10000")
        total = Decimal("0")

        if side == "bid":
            # For bids: include all levels >= mid * (1 - bps/10000)
            threshold = mid_price * (Decimal("1") - bps_decimal)
            for level in snapshot.bids:
                if level.price >= threshold:
                    total += level.notional
                else:
                    # Bids are sorted descending, so we can break early
                    break
        else:  # ask
            # For asks: include all levels <= mid * (1 + bps/10000)
            threshold = mid_price * (Decimal("1") + bps_decimal)
            for level in snapshot.asks:
                if level.price <= threshold:
                    total += level.notional
                else:
                    # Asks are sorted ascending, so we can break early
                    break

        return total

    def _calculate_imbalance(self, bid_depth: Decimal, ask_depth: Decimal) -> Decimal:
        """
        Calculate order book imbalance.

        Formula: (bid_depth - ask_depth) / (bid_depth + ask_depth)

        Args:
            bid_depth: Total bid depth (notional).
            ask_depth: Total ask depth (notional).

        Returns:
            Decimal: Imbalance ratio in range [-1, 1].
                    Positive = bid-heavy (more buy support)
                    Negative = ask-heavy (more sell pressure)
                    Zero = balanced or zero depth

        Note:
            Returns 0 if total depth is zero (avoids division by zero).
        """
        total_depth = bid_depth + ask_depth

        # Edge case: zero total depth
        if total_depth == Decimal("0"):
            return Decimal("0")

        imbalance = (bid_depth - ask_depth) / total_depth
        return imbalance

    def __repr__(self) -> str:
        """String representation of the calculator."""
        return f"DepthCalculator(bps_levels={self.bps_levels}, reference_level={self.reference_level})"
