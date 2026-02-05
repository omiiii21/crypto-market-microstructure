"""
Abstract interfaces for the surveillance system.

This module defines the abstract base classes that all implementations
must follow. These interfaces ensure consistent behavior across
different exchanges and components.

The key interface is ExchangeAdapter, which defines the contract for
all exchange-specific implementations (Binance, OKX, etc.).

Example:
    >>> from src.interfaces import ExchangeAdapter
    >>> class BinanceAdapter(ExchangeAdapter):
    ...     @property
    ...     def exchange_name(self) -> str:
    ...         return "binance"
    ...     # ... implement other abstract methods

Modules:
    exchange_adapter: ExchangeAdapter ABC for exchange integrations
"""

from src.interfaces.exchange_adapter import ExchangeAdapter

__all__: list[str] = [
    "ExchangeAdapter",
]
