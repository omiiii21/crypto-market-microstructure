"""
Binance exchange adapter module.

This module provides complete integration with Binance exchange for both
perpetual futures and spot markets. It implements the ExchangeAdapter
interface and handles WebSocket streaming, REST fallback, and data normalization.

Components:
    BinanceAdapter: Main adapter implementing ExchangeAdapter interface
    BinanceWebSocketClient: WebSocket connection management
    BinanceRestClient: REST API client for fallback
    BinanceNormalizer: Data normalization utilities

Example:
    >>> from src.adapters.binance import BinanceAdapter
    >>> adapter = BinanceAdapter(exchange_config, instrument_config)
    >>> await adapter.connect()
    >>> async for orderbook in adapter.stream_order_books():
    ...     print(f"Spread: {orderbook.spread_bps} bps")
"""

from src.adapters.binance.adapter import BinanceAdapter
from src.adapters.binance.normalizer import BinanceNormalizer
from src.adapters.binance.rest import BinanceRestClient
from src.adapters.binance.websocket import BinanceWebSocketClient

__all__ = [
    "BinanceAdapter",
    "BinanceWebSocketClient",
    "BinanceRestClient",
    "BinanceNormalizer",
]
