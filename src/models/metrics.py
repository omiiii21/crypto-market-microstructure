"""
Metrics data models for the surveillance system.

This module defines the computed metrics structures used for market quality
monitoring and anomaly detection. All values use Decimal for precision.

Models:
    SpreadMetrics: Bid-ask spread related metrics
    DepthMetrics: Order book depth at various price levels
    BasisMetrics: Perpetual-spot basis metrics
    ImbalanceMetrics: Order book imbalance metrics
    CrossExchangeMetrics: Cross-exchange comparison metrics
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class SpreadMetrics(BaseModel):
    """
    Bid-ask spread related metrics.

    Contains absolute spread, basis points spread, mid price, and
    optional z-score for statistical anomaly detection.

    Attributes:
        spread_abs: Absolute spread (best_ask - best_bid) in quote currency.
        spread_bps: Spread in basis points (spread / mid_price * 10000).
        mid_price: Mid price ((best_bid + best_ask) / 2).
        zscore: Z-score of spread relative to rolling window, None during warmup.

    Note:
        zscore will be None during warmup period (first ~30 samples) or
        when there is insufficient data for statistical calculation.

    Example:
        >>> metrics = SpreadMetrics(
        ...     spread_abs=Decimal("5.00"),
        ...     spread_bps=Decimal("1.0"),
        ...     mid_price=Decimal("50000.00"),
        ...     zscore=Decimal("0.5"),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    spread_abs: Decimal = Field(
        ...,
        description="Absolute spread in quote currency (best_ask - best_bid)",
        ge=Decimal("0"),
    )
    spread_bps: Decimal = Field(
        ...,
        description="Spread in basis points",
        ge=Decimal("0"),
    )
    mid_price: Decimal = Field(
        ...,
        description="Mid price ((best_bid + best_ask) / 2)",
        ge=Decimal("0"),
    )
    zscore: Optional[Decimal] = Field(
        default=None,
        description="Z-score of spread relative to rolling window (None during warmup)",
    )

    @property
    def is_zscore_available(self) -> bool:
        """
        Check if z-score is available (not in warmup).

        Returns:
            bool: True if z-score is computed and available.
        """
        return self.zscore is not None


class DepthMetrics(BaseModel):
    """
    Order book depth metrics at various price levels.

    Captures the notional value available at 5, 10, and 25 basis points
    from the mid price on both bid and ask sides, plus imbalance.

    Attributes:
        depth_5bps_bid: Bid depth within 5 bps of mid (USD).
        depth_5bps_ask: Ask depth within 5 bps of mid (USD).
        depth_5bps_total: Total depth within 5 bps (bid + ask).
        depth_10bps_bid: Bid depth within 10 bps of mid (USD).
        depth_10bps_ask: Ask depth within 10 bps of mid (USD).
        depth_10bps_total: Total depth within 10 bps (bid + ask).
        depth_25bps_bid: Bid depth within 25 bps of mid (USD).
        depth_25bps_ask: Ask depth within 25 bps of mid (USD).
        depth_25bps_total: Total depth within 25 bps (bid + ask).
        imbalance: Order book imbalance ratio [-1, 1].

    Example:
        >>> metrics = DepthMetrics(
        ...     depth_5bps_bid=Decimal("250000.00"),
        ...     depth_5bps_ask=Decimal("200000.00"),
        ...     depth_5bps_total=Decimal("450000.00"),
        ...     depth_10bps_bid=Decimal("500000.00"),
        ...     depth_10bps_ask=Decimal("450000.00"),
        ...     depth_10bps_total=Decimal("950000.00"),
        ...     depth_25bps_bid=Decimal("1000000.00"),
        ...     depth_25bps_ask=Decimal("900000.00"),
        ...     depth_25bps_total=Decimal("1900000.00"),
        ...     imbalance=Decimal("0.05"),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    # 5 bps depth
    depth_5bps_bid: Decimal = Field(
        ...,
        description="Bid depth within 5 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_5bps_ask: Decimal = Field(
        ...,
        description="Ask depth within 5 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_5bps_total: Decimal = Field(
        ...,
        description="Total depth within 5 bps (bid + ask)",
        ge=Decimal("0"),
    )

    # 10 bps depth
    depth_10bps_bid: Decimal = Field(
        ...,
        description="Bid depth within 10 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_10bps_ask: Decimal = Field(
        ...,
        description="Ask depth within 10 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_10bps_total: Decimal = Field(
        ...,
        description="Total depth within 10 bps (bid + ask)",
        ge=Decimal("0"),
    )

    # 25 bps depth
    depth_25bps_bid: Decimal = Field(
        ...,
        description="Bid depth within 25 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_25bps_ask: Decimal = Field(
        ...,
        description="Ask depth within 25 bps of mid (USD notional)",
        ge=Decimal("0"),
    )
    depth_25bps_total: Decimal = Field(
        ...,
        description="Total depth within 25 bps (bid + ask)",
        ge=Decimal("0"),
    )

    # Imbalance
    imbalance: Decimal = Field(
        ...,
        description="Order book imbalance: (bid - ask) / (bid + ask), range [-1, 1]",
        ge=Decimal("-1"),
        le=Decimal("1"),
    )

    @property
    def is_bid_heavy(self) -> bool:
        """
        Check if the order book is bid-heavy (more buy support).

        Returns:
            bool: True if imbalance > 0.
        """
        return self.imbalance > Decimal("0")

    @property
    def is_ask_heavy(self) -> bool:
        """
        Check if the order book is ask-heavy (more sell pressure).

        Returns:
            bool: True if imbalance < 0.
        """
        return self.imbalance < Decimal("0")

    def depth_at_level(self, bps: int, side: str) -> Decimal:
        """
        Get depth at a specific bps level.

        Args:
            bps: Basis points level (5, 10, or 25).
            side: Either "bid", "ask", or "total".

        Returns:
            Decimal: The depth value.

        Raises:
            ValueError: If bps or side is invalid.
        """
        key = f"depth_{bps}bps_{side}"
        if hasattr(self, key):
            return getattr(self, key)  # type: ignore[no-any-return]
        raise ValueError(f"Invalid depth level: {bps}bps_{side}")


class BasisMetrics(BaseModel):
    """
    Perpetual-spot basis metrics.

    Tracks the difference between perpetual and spot prices, both in
    absolute terms and basis points.

    Attributes:
        basis_abs: Absolute basis (perp_mid - spot_mid) in USD.
        basis_bps: Basis in basis points ((perp_mid - spot_mid) / spot_mid * 10000).
        perp_mid: Perpetual mid price.
        spot_mid: Spot mid price.
        zscore: Z-score of basis relative to rolling window, None during warmup.

    Note:
        Positive basis means perp is trading at a premium to spot.
        Negative basis means perp is trading at a discount.

    Example:
        >>> metrics = BasisMetrics(
        ...     basis_abs=Decimal("50.00"),
        ...     basis_bps=Decimal("10.0"),
        ...     perp_mid=Decimal("50050.00"),
        ...     spot_mid=Decimal("50000.00"),
        ...     zscore=Decimal("1.5"),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    basis_abs: Decimal = Field(
        ...,
        description="Absolute basis (perp_mid - spot_mid) in USD",
    )
    basis_bps: Decimal = Field(
        ...,
        description="Basis in basis points",
    )
    perp_mid: Decimal = Field(
        ...,
        description="Perpetual mid price",
        ge=Decimal("0"),
    )
    spot_mid: Decimal = Field(
        ...,
        description="Spot mid price",
        ge=Decimal("0"),
    )
    zscore: Optional[Decimal] = Field(
        default=None,
        description="Z-score of basis relative to rolling window (None during warmup)",
    )

    @property
    def is_premium(self) -> bool:
        """
        Check if perpetual is trading at a premium to spot.

        Returns:
            bool: True if basis is positive.
        """
        return self.basis_abs > Decimal("0")

    @property
    def is_discount(self) -> bool:
        """
        Check if perpetual is trading at a discount to spot.

        Returns:
            bool: True if basis is negative.
        """
        return self.basis_abs < Decimal("0")

    @property
    def is_zscore_available(self) -> bool:
        """
        Check if z-score is available (not in warmup).

        Returns:
            bool: True if z-score is computed and available.
        """
        return self.zscore is not None

    @property
    def abs_basis_bps(self) -> Decimal:
        """
        Get the absolute value of basis in basis points.

        Returns:
            Decimal: Absolute basis in bps.
        """
        return abs(self.basis_bps)


class ImbalanceMetrics(BaseModel):
    """
    Order book imbalance metrics at different levels.

    Attributes:
        top_of_book_imbalance: Imbalance at best bid/ask only.
        weighted_imbalance_5: Volume-weighted imbalance across top 5 levels.
        weighted_imbalance_10: Volume-weighted imbalance across top 10 levels.

    Example:
        >>> metrics = ImbalanceMetrics(
        ...     top_of_book_imbalance=Decimal("0.15"),
        ...     weighted_imbalance_5=Decimal("0.10"),
        ...     weighted_imbalance_10=Decimal("0.08"),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    top_of_book_imbalance: Decimal = Field(
        ...,
        description="Imbalance at best bid/ask: (bid_qty - ask_qty) / (bid_qty + ask_qty)",
        ge=Decimal("-1"),
        le=Decimal("1"),
    )
    weighted_imbalance_5: Decimal = Field(
        ...,
        description="Volume-weighted imbalance across top 5 levels",
        ge=Decimal("-1"),
        le=Decimal("1"),
    )
    weighted_imbalance_10: Decimal = Field(
        ...,
        description="Volume-weighted imbalance across top 10 levels",
        ge=Decimal("-1"),
        le=Decimal("1"),
    )


class CrossExchangeMetrics(BaseModel):
    """
    Cross-exchange comparison metrics.

    Tracks price differences between exchanges for the same instrument.

    Attributes:
        exchange_a: First exchange identifier.
        exchange_b: Second exchange identifier.
        instrument: Instrument being compared.
        timestamp: Timestamp of comparison.
        mid_price_a: Mid price on exchange A.
        mid_price_b: Mid price on exchange B.
        price_divergence_bps: Price divergence in basis points.
        cross_exchange_spread: Cross-exchange spread (max bid - min ask).
        arbitrage_opportunity: True if positive cross-exchange spread exists.

    Example:
        >>> metrics = CrossExchangeMetrics(
        ...     exchange_a="binance",
        ...     exchange_b="okx",
        ...     instrument="BTC-USDT-PERP",
        ...     timestamp=datetime.utcnow(),
        ...     mid_price_a=Decimal("50000.00"),
        ...     mid_price_b=Decimal("50005.00"),
        ...     price_divergence_bps=Decimal("1.0"),
        ...     cross_exchange_spread=Decimal("-3.00"),
        ...     arbitrage_opportunity=False,
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    exchange_a: str = Field(
        ...,
        description="First exchange identifier",
    )
    exchange_b: str = Field(
        ...,
        description="Second exchange identifier",
    )
    instrument: str = Field(
        ...,
        description="Instrument being compared",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp of comparison",
    )

    # Prices
    mid_price_a: Decimal = Field(
        ...,
        description="Mid price on exchange A",
        ge=Decimal("0"),
    )
    mid_price_b: Decimal = Field(
        ...,
        description="Mid price on exchange B",
        ge=Decimal("0"),
    )

    # Divergence
    price_divergence_bps: Decimal = Field(
        ...,
        description="Price divergence in basis points",
    )

    # Cross-exchange spread
    cross_exchange_spread: Decimal = Field(
        ...,
        description="Cross-exchange spread: max(bids) - min(asks) across exchanges",
    )

    # Arbitrage flag
    arbitrage_opportunity: bool = Field(
        ...,
        description="True if positive cross-exchange spread exists (arb opportunity)",
    )

    @property
    def abs_divergence_bps(self) -> Decimal:
        """
        Get absolute price divergence in basis points.

        Returns:
            Decimal: Absolute divergence.
        """
        return abs(self.price_divergence_bps)


class AggregatedMetrics(BaseModel):
    """
    Aggregated metrics combining all metric types for a single snapshot.

    This is the complete metrics package computed for each order book update.

    Attributes:
        exchange: Exchange identifier.
        instrument: Instrument identifier.
        timestamp: Timestamp of the underlying data.
        spread: Spread metrics.
        depth: Depth metrics.
        basis: Basis metrics (None for spot instruments).
        imbalance: Imbalance metrics.

    Example:
        >>> agg = AggregatedMetrics(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     timestamp=datetime.utcnow(),
        ...     spread=spread_metrics,
        ...     depth=depth_metrics,
        ...     basis=basis_metrics,
        ...     imbalance=imbalance_metrics,
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    exchange: str = Field(
        ...,
        description="Exchange identifier",
    )
    instrument: str = Field(
        ...,
        description="Instrument identifier",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp of the underlying data",
    )

    spread: SpreadMetrics = Field(
        ...,
        description="Spread metrics",
    )
    depth: DepthMetrics = Field(
        ...,
        description="Depth metrics",
    )
    basis: Optional[BasisMetrics] = Field(
        default=None,
        description="Basis metrics (None for spot instruments)",
    )
    imbalance: ImbalanceMetrics = Field(
        ...,
        description="Imbalance metrics",
    )

    @property
    def has_basis(self) -> bool:
        """
        Check if basis metrics are available (i.e., this is a perpetual).

        Returns:
            bool: True if basis metrics are present.
        """
        return self.basis is not None
