"""
Ticker and trade data models for the surveillance system.

This module defines ticker snapshots and trade data structures used for
market monitoring. All financial values use Decimal for precision.

Models:
    TickerSnapshot: Complete ticker data for an instrument
    TradeSnapshot: Individual trade data
    TradeSide: Enum for trade side (buy/sell)
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class TradeSide(str, Enum):
    """
    Enumeration for trade side.

    Attributes:
        BUY: Buyer was the aggressor (taker bought)
        SELL: Seller was the aggressor (taker sold)
    """

    BUY = "buy"
    SELL = "sell"


class TickerSnapshot(BaseModel):
    """
    Ticker data for an instrument.

    Contains current prices, 24-hour statistics, and derivatives-specific
    data like funding rates and mark prices.

    Attributes:
        exchange: Exchange identifier (e.g., "binance", "okx").
        instrument: Normalized instrument ID (e.g., "BTC-USDT-PERP").
        timestamp: Exchange timestamp (UTC).
        last_price: Last traded price.
        mark_price: Mark price for perpetuals (used for liquidations).
        index_price: Index price for perpetuals (composite from spot exchanges).
        volume_24h: 24-hour volume in base currency.
        volume_24h_usd: 24-hour volume in USD notional.
        high_24h: 24-hour high price.
        low_24h: 24-hour low price.
        funding_rate: Current funding rate (perpetuals only).
        next_funding_time: Next funding settlement time (perpetuals only).

    Example:
        >>> ticker = TickerSnapshot(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     timestamp=datetime.utcnow(),
        ...     last_price=Decimal("50000.00"),
        ...     mark_price=Decimal("50001.50"),
        ...     index_price=Decimal("49999.00"),
        ...     volume_24h=Decimal("1000.5"),
        ...     volume_24h_usd=Decimal("50025000.00"),
        ...     high_24h=Decimal("51000.00"),
        ...     low_24h=Decimal("49000.00"),
        ...     funding_rate=Decimal("0.0001"),
        ...     next_funding_time=datetime(2025, 1, 26, 8, 0, 0),
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    # Identification
    exchange: str = Field(
        ...,
        description="Exchange identifier",
        min_length=1,
        max_length=50,
        examples=["binance", "okx"],
    )
    instrument: str = Field(
        ...,
        description="Normalized instrument identifier",
        min_length=1,
        max_length=50,
        examples=["BTC-USDT-PERP", "BTC-USDT-SPOT"],
    )
    timestamp: datetime = Field(
        ...,
        description="Exchange timestamp (UTC)",
    )

    # Core prices
    last_price: Decimal = Field(
        ...,
        description="Last traded price",
        ge=Decimal("0"),
    )

    # Derivatives-specific prices (optional for spot)
    mark_price: Optional[Decimal] = Field(
        default=None,
        description="Mark price for perpetuals (used for liquidations)",
        ge=Decimal("0"),
    )
    index_price: Optional[Decimal] = Field(
        default=None,
        description="Index price for perpetuals (composite from spot exchanges)",
        ge=Decimal("0"),
    )

    # 24-hour statistics
    volume_24h: Decimal = Field(
        ...,
        description="24-hour volume in base currency",
        ge=Decimal("0"),
    )
    volume_24h_usd: Decimal = Field(
        ...,
        description="24-hour volume in USD notional",
        ge=Decimal("0"),
    )
    high_24h: Decimal = Field(
        ...,
        description="24-hour high price",
        ge=Decimal("0"),
    )
    low_24h: Decimal = Field(
        ...,
        description="24-hour low price",
        ge=Decimal("0"),
    )

    # Funding (perpetuals only)
    funding_rate: Optional[Decimal] = Field(
        default=None,
        description="Current funding rate (8-hour rate)",
    )
    next_funding_time: Optional[datetime] = Field(
        default=None,
        description="Next funding settlement time (UTC)",
    )

    @model_validator(mode="after")
    def validate_ticker(self) -> "TickerSnapshot":
        """
        Validate ticker data consistency.

        Ensures:
            - High >= Low for 24h range
            - Last price is within reasonable range of high/low
        """
        if self.high_24h < self.low_24h:
            raise ValueError(
                f"24h high ({self.high_24h}) must be >= 24h low ({self.low_24h})"
            )
        return self

    @property
    def is_perpetual(self) -> bool:
        """
        Check if this is a perpetual instrument.

        Returns:
            bool: True if mark_price is set (indicating perpetual).
        """
        return self.mark_price is not None

    @property
    def mark_index_deviation_bps(self) -> Optional[Decimal]:
        """
        Calculate mark-index deviation in basis points.

        Formula: (mark_price - index_price) / index_price * 10000

        Returns:
            Optional[Decimal]: Deviation in bps, or None if not a perpetual.
        """
        if (
            self.mark_price is not None
            and self.index_price is not None
            and self.index_price > Decimal("0")
        ):
            return ((self.mark_price - self.index_price) / self.index_price) * Decimal("10000")
        return None

    @property
    def funding_rate_annualized(self) -> Optional[Decimal]:
        """
        Calculate annualized funding rate.

        Assumes 8-hour funding periods (3 per day).
        Formula: funding_rate * 3 * 365

        Returns:
            Optional[Decimal]: Annualized funding rate, or None if not available.
        """
        if self.funding_rate is not None:
            return self.funding_rate * Decimal("3") * Decimal("365")
        return None

    @property
    def price_range_24h_pct(self) -> Decimal:
        """
        Calculate 24-hour price range as a percentage.

        Formula: (high - low) / low * 100

        Returns:
            Decimal: Price range percentage, or 0 if low is 0.
        """
        if self.low_24h > Decimal("0"):
            return ((self.high_24h - self.low_24h) / self.low_24h) * Decimal("100")
        return Decimal("0")


class TradeSnapshot(BaseModel):
    """
    Recent trade data.

    Represents a single executed trade with all relevant details.

    Attributes:
        exchange: Exchange identifier.
        instrument: Normalized instrument ID.
        timestamp: Trade execution timestamp (UTC).
        price: Trade execution price.
        quantity: Trade quantity in base currency.
        side: Trade side (buy/sell) - indicates aggressor.
        trade_id: Exchange-specific trade identifier.

    Example:
        >>> trade = TradeSnapshot(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     timestamp=datetime.utcnow(),
        ...     price=Decimal("50000.00"),
        ...     quantity=Decimal("0.5"),
        ...     side=TradeSide.BUY,
        ...     trade_id="123456789",
        ... )
    """

    model_config = {"frozen": True, "extra": "forbid"}

    # Identification
    exchange: str = Field(
        ...,
        description="Exchange identifier",
        min_length=1,
        max_length=50,
    )
    instrument: str = Field(
        ...,
        description="Normalized instrument identifier",
        min_length=1,
        max_length=50,
    )
    timestamp: datetime = Field(
        ...,
        description="Trade execution timestamp (UTC)",
    )

    # Trade data
    price: Decimal = Field(
        ...,
        description="Trade execution price",
        ge=Decimal("0"),
    )
    quantity: Decimal = Field(
        ...,
        description="Trade quantity in base currency",
        ge=Decimal("0"),
    )
    side: TradeSide = Field(
        ...,
        description="Trade side - indicates which side was the aggressor",
    )
    trade_id: str = Field(
        ...,
        description="Exchange-specific trade identifier",
        min_length=1,
    )

    @property
    def notional(self) -> Decimal:
        """
        Calculate the notional value of this trade.

        Returns:
            Decimal: The product of price and quantity.
        """
        return self.price * self.quantity

    @property
    def is_buy(self) -> bool:
        """
        Check if this was a buy (taker bought).

        Returns:
            bool: True if the trade side was buy.
        """
        return self.side == TradeSide.BUY

    @property
    def is_sell(self) -> bool:
        """
        Check if this was a sell (taker sold).

        Returns:
            bool: True if the trade side was sell.
        """
        return self.side == TradeSide.SELL
