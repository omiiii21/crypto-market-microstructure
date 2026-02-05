"""
Order book data models for the surveillance system.

This module defines the core order book data structures used throughout the system.
All financial values use Decimal for precision to avoid floating-point errors.

Models:
    PriceLevel: Single price level in an order book (price, quantity)
    OrderBookSnapshot: Complete normalized order book snapshot from any exchange
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class PriceLevel(BaseModel):
    """
    Single price level in an order book.

    Represents a single bid or ask level with price and quantity.
    The notional value (price * quantity) is computed automatically.

    Attributes:
        price: Price at this level in quote currency (e.g., USDT).
        quantity: Quantity available at this level in base currency (e.g., BTC).

    Example:
        >>> level = PriceLevel(price=Decimal("50000.00"), quantity=Decimal("1.5"))
        >>> level.notional
        Decimal('75000.00')
    """

    model_config = {"frozen": True, "extra": "ignore"}

    price: Decimal = Field(
        ...,
        description="Price at this level in quote currency",
        ge=Decimal("0"),
    )
    quantity: Decimal = Field(
        ...,
        description="Quantity available at this level in base currency",
        ge=Decimal("0"),
    )

    @computed_field  # type: ignore[misc]
    @property
    def notional(self) -> Decimal:
        """
        Calculate the notional value (USD equivalent) at this level.

        Returns:
            Decimal: The product of price and quantity.
        """
        return self.price * self.quantity


class OrderBookSnapshot(BaseModel):
    """
    Normalized order book snapshot from any exchange.

    This is the unified internal schema. Each exchange adapter converts
    raw exchange data to this format for consistent processing.

    Attributes:
        exchange: Exchange identifier (e.g., "binance", "okx").
        instrument: Normalized instrument ID (e.g., "BTC-USDT-PERP", "BTC-USDT-SPOT").
        timestamp: Exchange-provided timestamp in UTC.
        local_timestamp: When we received the data (UTC).
        sequence_id: Exchange-specific sequence number for gap detection.
        bids: List of bid levels, sorted best (highest price) to worst.
        asks: List of ask levels, sorted best (lowest price) to worst.
        depth_levels: Number of levels captured (default: 20).

    Example:
        >>> snapshot = OrderBookSnapshot(
        ...     exchange="binance",
        ...     instrument="BTC-USDT-PERP",
        ...     timestamp=datetime.utcnow(),
        ...     local_timestamp=datetime.utcnow(),
        ...     sequence_id=12345678,
        ...     bids=[PriceLevel(price=Decimal("50000"), quantity=Decimal("1.0"))],
        ...     asks=[PriceLevel(price=Decimal("50001"), quantity=Decimal("0.5"))],
        ... )
    """

    model_config = {"frozen": True, "extra": "ignore"}

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

    # Timestamps
    timestamp: datetime = Field(
        ...,
        description="Exchange timestamp (UTC)",
    )
    local_timestamp: datetime = Field(
        ...,
        description="Local receipt timestamp (UTC)",
    )

    # Sequence tracking for gap detection
    sequence_id: int = Field(
        ...,
        description="Exchange sequence ID (Binance: lastUpdateId, OKX: seqId)",
        ge=0,
    )

    # Order book data
    bids: List[PriceLevel] = Field(
        default_factory=list,
        description="Bid levels, sorted best (highest) to worst",
    )
    asks: List[PriceLevel] = Field(
        default_factory=list,
        description="Ask levels, sorted best (lowest) to worst",
    )

    # Metadata
    depth_levels: int = Field(
        default=20,
        description="Number of depth levels requested/received",
        ge=1,
        le=100,
    )

    @model_validator(mode="after")
    def validate_order_book(self) -> "OrderBookSnapshot":
        """
        Validate order book invariants.

        Ensures:
            - Bids are sorted in descending order (best first)
            - Asks are sorted in ascending order (best first)
            - No crossed book (best bid < best ask)
        """
        # Validate bid ordering (descending)
        if len(self.bids) > 1:
            for i in range(len(self.bids) - 1):
                if self.bids[i].price < self.bids[i + 1].price:
                    raise ValueError(
                        f"Bids must be sorted descending: {self.bids[i].price} < {self.bids[i + 1].price}"
                    )

        # Validate ask ordering (ascending)
        if len(self.asks) > 1:
            for i in range(len(self.asks) - 1):
                if self.asks[i].price > self.asks[i + 1].price:
                    raise ValueError(
                        f"Asks must be sorted ascending: {self.asks[i].price} > {self.asks[i + 1].price}"
                    )

        # Validate no crossed book
        if self.bids and self.asks:
            if self.bids[0].price >= self.asks[0].price:
                raise ValueError(
                    f"Crossed order book: best bid ({self.bids[0].price}) >= best ask ({self.asks[0].price})"
                )

        return self

    @computed_field  # type: ignore[misc]
    @property
    def best_bid(self) -> Optional[Decimal]:
        """
        Get the best (highest) bid price.

        Returns:
            Optional[Decimal]: Best bid price, or None if no bids.
        """
        return self.bids[0].price if self.bids else None

    @computed_field  # type: ignore[misc]
    @property
    def best_ask(self) -> Optional[Decimal]:
        """
        Get the best (lowest) ask price.

        Returns:
            Optional[Decimal]: Best ask price, or None if no asks.
        """
        return self.asks[0].price if self.asks else None

    @computed_field  # type: ignore[misc]
    @property
    def mid_price(self) -> Optional[Decimal]:
        """
        Calculate the mid price.

        Mid price is the average of best bid and best ask.

        Returns:
            Optional[Decimal]: Mid price, or None if either side is empty.
        """
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / Decimal("2")
        return None

    @computed_field  # type: ignore[misc]
    @property
    def spread(self) -> Optional[Decimal]:
        """
        Calculate the absolute spread (best_ask - best_bid).

        Returns:
            Optional[Decimal]: Absolute spread, or None if either side is empty.
        """
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @computed_field  # type: ignore[misc]
    @property
    def spread_bps(self) -> Optional[Decimal]:
        """
        Calculate the spread in basis points.

        Formula: (best_ask - best_bid) / mid_price * 10000

        Returns:
            Optional[Decimal]: Spread in basis points, or None if cannot be calculated.
        """
        if self.spread is not None and self.mid_price is not None and self.mid_price > 0:
            return (self.spread / self.mid_price) * Decimal("10000")
        return None

    @computed_field  # type: ignore[misc]
    @property
    def best_bid_quantity(self) -> Optional[Decimal]:
        """
        Get the quantity at the best bid level.

        Returns:
            Optional[Decimal]: Best bid quantity, or None if no bids.
        """
        return self.bids[0].quantity if self.bids else None

    @computed_field  # type: ignore[misc]
    @property
    def best_ask_quantity(self) -> Optional[Decimal]:
        """
        Get the quantity at the best ask level.

        Returns:
            Optional[Decimal]: Best ask quantity, or None if no asks.
        """
        return self.asks[0].quantity if self.asks else None

    @property
    def is_valid(self) -> bool:
        """
        Check if the order book has valid data for metric calculation.

        Returns:
            bool: True if both sides have at least one level.
        """
        return bool(self.bids and self.asks)

    def total_bid_notional(self) -> Decimal:
        """
        Calculate the total notional value of all bid levels.

        Returns:
            Decimal: Sum of notional values across all bid levels.
        """
        return sum((level.notional for level in self.bids), Decimal("0"))

    def total_ask_notional(self) -> Decimal:
        """
        Calculate the total notional value of all ask levels.

        Returns:
            Decimal: Sum of notional values across all ask levels.
        """
        return sum((level.notional for level in self.asks), Decimal("0"))

    def depth_at_bps(self, bps: int, side: str) -> Decimal:
        """
        Calculate the depth (notional) within a given number of basis points from mid.

        Args:
            bps: Number of basis points from mid price.
            side: Either "bid" or "ask".

        Returns:
            Decimal: Total notional within the specified range.

        Raises:
            ValueError: If side is not "bid" or "ask".
        """
        if side not in ("bid", "ask"):
            raise ValueError(f"side must be 'bid' or 'ask', got '{side}'")

        if self.mid_price is None:
            return Decimal("0")

        bps_decimal = Decimal(bps) / Decimal("10000")
        total = Decimal("0")

        if side == "bid":
            threshold = self.mid_price * (Decimal("1") - bps_decimal)
            for level in self.bids:
                if level.price >= threshold:
                    total += level.notional
                else:
                    break
        else:  # ask
            threshold = self.mid_price * (Decimal("1") + bps_decimal)
            for level in self.asks:
                if level.price <= threshold:
                    total += level.notional
                else:
                    break

        return total
