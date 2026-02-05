"""
OKX exchange adapter.

This package provides OKX exchange integration with WebSocket streaming,
REST fallback, data normalization, and sequence gap detection.

Components:
    - OKXWebSocketClient: WebSocket connection manager
    - OKXNormalizer: Data format converter
    - OKXRestClient: REST API client for fallback
    - OKXAdapter: Main adapter implementing ExchangeAdapter interface

Example:
    >>> from src.adapters.okx import OKXAdapter
    >>> from src.config.loader import load_config
    >>>
    >>> config = load_config()
    >>> exchange_config = config.get_exchange("okx")
    >>> instruments = config.get_enabled_instruments()
    >>>
    >>> adapter = OKXAdapter(exchange_config, instruments)
    >>> await adapter.connect()
    >>> await adapter.subscribe(["BTC-USDT-PERP", "BTC-USDT-SPOT"])
    >>>
    >>> async for snapshot in adapter.stream_order_books():
    ...     print(f"{snapshot.instrument}: {snapshot.spread_bps} bps")

Note:
    This module is owned by the DATA-ENGINEER agent.
"""

from src.adapters.okx.adapter import OKXAdapter
from src.adapters.okx.normalizer import OKXNormalizer
from src.adapters.okx.rest import OKXRestClient
from src.adapters.okx.websocket import OKXWebSocketClient

__all__ = [
    "OKXAdapter",
    "OKXNormalizer",
    "OKXRestClient",
    "OKXWebSocketClient",
]
