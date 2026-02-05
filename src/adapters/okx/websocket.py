"""
OKX WebSocket client.

Manages WebSocket connections to OKX public data endpoints.
Handles connection lifecycle, automatic reconnection, heartbeat, and
streaming message delivery.

Connection Management:
    - Auto-reconnect with exponential backoff and jitter
    - String-based ping/pong heartbeat every 30 seconds
    - Connection state tracking
    - Graceful disconnect handling

OKX-Specific Details:
    - Single WebSocket endpoint for all instruments
    - Subscription via JSON messages: {"op": "subscribe", "args": [...]}
    - Ping format: string "ping" (not binary frame)
    - Pong response: string "pong"

Example:
    >>> client = OKXWebSocketClient(
    ...     url="wss://ws.okx.com:8443/ws/v5/public",
    ...     ping_interval=30,
    ...     max_reconnect_attempts=10
    ... )
    >>> await client.connect()
    >>> await client.subscribe([
    ...     {"channel": "books", "instId": "BTC-USDT-SWAP"},
    ...     {"channel": "books", "instId": "BTC-USDT"}
    ... ])
    >>> async for message in client.stream_messages():
    ...     print(message)
"""

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = structlog.get_logger(__name__)


class OKXWebSocketClient:
    """
    Async WebSocket client for OKX.

    Manages WebSocket connections with automatic reconnection, heartbeat,
    and multi-channel subscription support.

    Attributes:
        url: WebSocket endpoint URL.
        ping_interval: Seconds between ping messages.
        max_reconnect_attempts: Maximum reconnection attempts.
        reconnect_delay: Base delay for reconnection backoff.

    Example:
        >>> client = OKXWebSocketClient(
        ...     url="wss://ws.okx.com:8443/ws/v5/public",
        ...     ping_interval=30,
        ...     max_reconnect_attempts=10
        ... )
        >>> await client.connect()
        >>> await client.subscribe([
        ...     {"channel": "books", "instId": "BTC-USDT-SWAP"}
        ... ])
        >>> async for message in client.stream_messages():
        ...     print(message)
    """

    def __init__(
        self,
        url: str,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        max_reconnect_attempts: int = 10,
        reconnect_delay: int = 5,
    ):
        """
        Initialize WebSocket client.

        Args:
            url: WebSocket endpoint URL.
            ping_interval: Seconds between ping messages.
            ping_timeout: Seconds to wait for pong response.
            max_reconnect_attempts: Maximum reconnection attempts.
            reconnect_delay: Base delay in seconds for reconnection backoff.
        """
        self.url = url
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: List[Dict[str, Any]] = []
        self._connected = False
        self._reconnect_count = 0
        self._last_message_at: Optional[datetime] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._should_reconnect = True

        logger.info(
            "websocket_client_initialized",
            exchange="okx",
            url=url,
            ping_interval=ping_interval,
            max_attempts=max_reconnect_attempts,
        )

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected and self._ws is not None

    @property
    def reconnect_count(self) -> int:
        """Get the number of reconnections in this session."""
        return self._reconnect_count

    @property
    def last_message_at(self) -> Optional[datetime]:
        """Get timestamp of last received message."""
        return self._last_message_at

    async def connect(self) -> None:
        """
        Establish WebSocket connection.

        Opens the WebSocket connection and starts the ping task.
        Idempotent - does nothing if already connected.

        Raises:
            ConnectionError: If connection fails after max retries.
        """
        if self.is_connected:
            logger.debug("websocket_already_connected", exchange="okx", url=self.url)
            return

        try:
            self._ws = await websockets.connect(
                self.url,
                ping_interval=None,  # We handle pings manually
                ping_timeout=None,
                close_timeout=10,
                max_size=2**20,  # 1MB max message size
            )
            self._connected = True

            # Start ping task
            self._ping_task = asyncio.create_task(self._send_pings())

            logger.info(
                "websocket_connected",
                exchange="okx",
                url=self.url,
                reconnect_count=self._reconnect_count,
            )

        except Exception as e:
            logger.error(
                "websocket_connection_failed", exchange="okx", url=self.url, error=str(e)
            )
            raise ConnectionError(f"Failed to connect to OKX WebSocket: {e}")

    async def disconnect(self) -> None:
        """
        Gracefully close WebSocket connection.

        Cancels ping task, closes connection, and cleans up resources.
        Safe to call multiple times.
        """
        self._should_reconnect = False

        # Cancel ping task
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
                logger.info("websocket_disconnected", exchange="okx", url=self.url)
            except Exception as e:
                logger.warning(
                    "websocket_close_error", exchange="okx", url=self.url, error=str(e)
                )

        self._connected = False
        self._ws = None

    async def subscribe(self, channels: List[Dict[str, Any]]) -> None:
        """
        Subscribe to data channels.

        OKX uses JSON subscription messages with format:
        {"op": "subscribe", "args": [{"channel": "books", "instId": "BTC-USDT-SWAP"}]}

        Args:
            channels: List of channel subscription objects.
                Each object should have "channel" and "instId" fields.

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If subscription fails.

        Example:
            >>> await client.subscribe([
            ...     {"channel": "books", "instId": "BTC-USDT-SWAP"},
            ...     {"channel": "tickers", "instId": "BTC-USDT-SWAP"}
            ... ])
        """
        if not self.is_connected:
            raise ConnectionError("Cannot subscribe: not connected")

        self._subscriptions = channels

        # Build subscription message
        subscription_msg = {"op": "subscribe", "args": channels}

        try:
            await self._ws.send(json.dumps(subscription_msg))
            logger.info(
                "websocket_subscribed",
                exchange="okx",
                url=self.url,
                channels=channels,
            )
        except Exception as e:
            logger.error(
                "websocket_subscription_failed",
                exchange="okx",
                url=self.url,
                error=str(e),
            )
            raise RuntimeError(f"Failed to subscribe: {e}")

    async def stream_messages(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream messages from WebSocket.

        Yields parsed JSON messages as they arrive. Automatically handles
        reconnection on connection loss.

        Yields:
            Dict[str, Any]: Parsed JSON message.

        Raises:
            ConnectionError: If connection fails and cannot be recovered.

        Example:
            >>> async for message in client.stream_messages():
            ...     if message.get("arg", {}).get("channel") == "books":
            ...         print(f"Order book update: {message}")
        """
        while self._should_reconnect:
            try:
                if not self.is_connected:
                    await self._reconnect()

                if not self._ws:
                    await asyncio.sleep(1)
                    continue

                # Receive message
                raw_message = await self._ws.recv()
                self._last_message_at = datetime.now(timezone.utc)

                # Parse JSON
                try:
                    message = json.loads(raw_message)

                    # Handle pong responses
                    if message == "pong":
                        logger.debug("websocket_pong_received", exchange="okx")
                        continue

                    # Handle subscription confirmations
                    if message.get("event") == "subscribe":
                        logger.info(
                            "websocket_subscription_confirmed",
                            exchange="okx",
                            channel=message.get("arg"),
                        )
                        continue

                    # Handle errors
                    if message.get("event") == "error":
                        logger.error(
                            "websocket_error_message",
                            exchange="okx",
                            error=message.get("msg"),
                            code=message.get("code"),
                        )
                        continue

                    # Yield data messages
                    if "data" in message:
                        yield message

                except json.JSONDecodeError as e:
                    logger.warning(
                        "websocket_invalid_json",
                        exchange="okx",
                        url=self.url,
                        error=str(e),
                        message=raw_message[:100],
                    )
                    continue

            except ConnectionClosed:
                logger.warning("websocket_connection_closed", exchange="okx", url=self.url)
                self._connected = False
                if self._should_reconnect:
                    await self._reconnect()
                else:
                    break

            except WebSocketException as e:
                logger.error("websocket_error", exchange="okx", url=self.url, error=str(e))
                self._connected = False
                if self._should_reconnect:
                    await self._reconnect()
                else:
                    break

            except asyncio.CancelledError:
                logger.info("websocket_stream_cancelled", exchange="okx", url=self.url)
                break

            except Exception as e:
                logger.error(
                    "websocket_unexpected_error",
                    exchange="okx",
                    url=self.url,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(1)

    async def _reconnect(self) -> None:
        """
        Reconnect to WebSocket with exponential backoff.

        Uses exponential backoff with jitter to avoid thundering herd.
        Logs each reconnection attempt and failure.

        Raises:
            ConnectionError: If max reconnection attempts exceeded.
        """
        if self._reconnect_count >= self.max_reconnect_attempts:
            logger.error(
                "websocket_max_reconnect_exceeded",
                exchange="okx",
                url=self.url,
                max_attempts=self.max_reconnect_attempts,
            )
            raise ConnectionError(
                f"Max reconnection attempts ({self.max_reconnect_attempts}) exceeded"
            )

        # Exponential backoff with jitter
        base_delay = self.reconnect_delay
        max_delay = 60.0
        attempt = self._reconnect_count

        delay = min(base_delay * (2**attempt), max_delay)
        jitter = random.uniform(0, delay * 0.1)
        total_delay = delay + jitter

        logger.info(
            "websocket_reconnecting",
            exchange="okx",
            url=self.url,
            attempt=attempt + 1,
            max_attempts=self.max_reconnect_attempts,
            delay_seconds=total_delay,
        )

        await asyncio.sleep(total_delay)

        try:
            await self.connect()
            self._reconnect_count += 1

            # Re-subscribe to channels
            if self._subscriptions:
                await self.subscribe(self._subscriptions)

            logger.info(
                "websocket_reconnected",
                exchange="okx",
                url=self.url,
                reconnect_count=self._reconnect_count,
            )

        except Exception as e:
            logger.error(
                "websocket_reconnect_failed",
                exchange="okx",
                url=self.url,
                attempt=attempt + 1,
                error=str(e),
            )
            self._reconnect_count += 1
            # Will retry on next iteration

    async def _send_pings(self) -> None:
        """
        Send periodic ping messages.

        OKX expects string "ping" messages, responds with string "pong".
        This task sends pings at the configured interval to keep the connection alive.
        """
        try:
            while self.is_connected:
                await asyncio.sleep(self.ping_interval)

                if self._ws:
                    try:
                        # OKX uses string "ping", not binary ping frames
                        await self._ws.send("ping")
                        logger.debug("websocket_ping_sent", exchange="okx", url=self.url)
                    except Exception as e:
                        logger.error(
                            "websocket_ping_error",
                            exchange="okx",
                            url=self.url,
                            error=str(e),
                        )
                        self._connected = False
                        break

        except asyncio.CancelledError:
            logger.debug("websocket_ping_task_cancelled", exchange="okx", url=self.url)
        except Exception as e:
            logger.error(
                "websocket_ping_task_error", exchange="okx", url=self.url, error=str(e)
            )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"OKXWebSocketClient(url={self.url}, "
            f"connected={self.is_connected}, "
            f"reconnects={self._reconnect_count})"
        )
