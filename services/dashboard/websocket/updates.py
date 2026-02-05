"""
WebSocket endpoint for real-time dashboard updates.

Provides:
    WS /ws/updates - Real-time push updates for state, alerts, and health

Protocol:
    Client sends subscribe message:
    {
        "action": "subscribe",
        "channels": ["state", "alerts", "health"],
        "exchanges": ["binance", "okx"],
        "instruments": ["BTC-USDT-PERP"]
    }

    Server pushes updates:
    {
        "channel": "state",
        "exchange": "binance",
        "instrument": "BTC-USDT-PERP",
        "timestamp": "2025-01-26T12:34:56.789Z",
        "data": {...}
    }

Note:
    This module is owned by the VIZ agent.
    All data is READ from Redis - the WebSocket only pushes updates.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter()


class ConnectionManager:
    """
    Manages WebSocket connections and subscriptions.

    Handles client connections, subscription management, and message broadcasting.
    Each client can subscribe to specific channels, exchanges, and instruments.
    """

    def __init__(self):
        """Initialize the connection manager."""
        self.active_connections: Dict[WebSocket, Dict[str, Any]] = {}
        self._update_task: Optional[asyncio.Task] = None
        self._running: bool = False

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection.

        Args:
            websocket: The WebSocket connection to accept.
        """
        await websocket.accept()
        self.active_connections[websocket] = {
            "channels": set(),
            "exchanges": set(),
            "instruments": set(),
            "connected_at": datetime.utcnow(),
        }
        logger.info(
            "websocket_connected",
            total_connections=len(self.active_connections),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection.

        Args:
            websocket: The WebSocket connection to remove.
        """
        if websocket in self.active_connections:
            del self.active_connections[websocket]
        logger.info(
            "websocket_disconnected",
            total_connections=len(self.active_connections),
        )

    def subscribe(
        self,
        websocket: WebSocket,
        channels: List[str],
        exchanges: List[str],
        instruments: List[str],
    ) -> None:
        """
        Update subscription for a WebSocket connection.

        Args:
            websocket: The WebSocket connection.
            channels: Channels to subscribe to (state, alerts, health).
            exchanges: Exchanges to receive updates for.
            instruments: Instruments to receive updates for.
        """
        if websocket in self.active_connections:
            self.active_connections[websocket]["channels"] = set(channels)
            self.active_connections[websocket]["exchanges"] = set(exchanges)
            self.active_connections[websocket]["instruments"] = set(instruments)
            logger.debug(
                "websocket_subscribed",
                channels=channels,
                exchanges=exchanges,
                instruments=instruments,
            )

    async def broadcast_to_subscribers(
        self,
        channel: str,
        data: Dict[str, Any],
        exchange: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> None:
        """
        Broadcast a message to all subscribed connections.

        Args:
            channel: The channel (state, alerts, health).
            data: The data to send.
            exchange: Optional exchange filter.
            instrument: Optional instrument filter.
        """
        message = {
            "channel": channel,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }
        if exchange:
            message["exchange"] = exchange
        if instrument:
            message["instrument"] = instrument

        disconnected = []

        for websocket, subscription in self.active_connections.items():
            # Check if client is subscribed to this channel
            if channel not in subscription["channels"]:
                continue

            # Check exchange filter
            if exchange and subscription["exchanges"]:
                if exchange not in subscription["exchanges"]:
                    continue

            # Check instrument filter
            if instrument and subscription["instruments"]:
                if instrument not in subscription["instruments"]:
                    continue

            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.warning(
                    "websocket_send_failed",
                    error=str(e),
                )
                disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws)

    def start_update_loop(self) -> None:
        """Start the background update loop."""
        if not self._running:
            self._running = True
            self._update_task = asyncio.create_task(self._update_loop())
            logger.info("websocket_update_loop_started")

    def stop_update_loop(self) -> None:
        """Stop the background update loop."""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            self._update_task = None
            logger.info("websocket_update_loop_stopped")

    async def _update_loop(self) -> None:
        """
        Background loop that pushes updates to subscribers.

        Runs every second and pushes state, alerts, and health updates.
        """
        from services.dashboard.app import app_state

        while self._running:
            try:
                if not self.active_connections:
                    await asyncio.sleep(1)
                    continue

                redis_client = app_state.redis_client

                if not redis_client:
                    await asyncio.sleep(1)
                    continue

                # Get all subscribed exchanges and instruments
                all_exchanges: Set[str] = set()
                all_instruments: Set[str] = set()
                has_state_subscribers = False
                has_alerts_subscribers = False
                has_health_subscribers = False

                for subscription in self.active_connections.values():
                    if "state" in subscription["channels"]:
                        has_state_subscribers = True
                        all_exchanges.update(subscription["exchanges"] or {"binance", "okx"})
                        all_instruments.update(subscription["instruments"] or {"BTC-USDT-PERP"})
                    if "alerts" in subscription["channels"]:
                        has_alerts_subscribers = True
                    if "health" in subscription["channels"]:
                        has_health_subscribers = True

                # Push state updates
                if has_state_subscribers:
                    for exchange in all_exchanges:
                        for instrument in all_instruments:
                            try:
                                state = await redis_client.get_current_state(
                                    exchange, instrument
                                )
                                if state:
                                    await self.broadcast_to_subscribers(
                                        channel="state",
                                        data=state,
                                        exchange=exchange,
                                        instrument=instrument,
                                    )
                            except Exception as e:
                                logger.debug(
                                    "state_fetch_error",
                                    exchange=exchange,
                                    instrument=instrument,
                                    error=str(e),
                                )

                # Push alerts updates
                if has_alerts_subscribers:
                    try:
                        alerts = await redis_client.get_active_alerts()
                        await self.broadcast_to_subscribers(
                            channel="alerts",
                            data={
                                "alerts": alerts,
                                "counts": {
                                    "P1": sum(1 for a in alerts if a.get("priority") == "P1"),
                                    "P2": sum(1 for a in alerts if a.get("priority") == "P2"),
                                    "P3": sum(1 for a in alerts if a.get("priority") == "P3"),
                                    "total": len(alerts),
                                },
                            },
                        )
                    except Exception as e:
                        logger.debug("alerts_fetch_error", error=str(e))

                # Push health updates
                if has_health_subscribers:
                    try:
                        all_health = await redis_client.get_all_health()
                        await self.broadcast_to_subscribers(
                            channel="health",
                            data={"exchanges": all_health},
                        )
                    except Exception as e:
                        logger.debug("health_fetch_error", error=str(e))

                await asyncio.sleep(1)  # 1-second update interval

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("websocket_update_loop_error", error=str(e))
                await asyncio.sleep(1)


# Global connection manager
manager = ConnectionManager()


@router.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time updates.

    Clients connect and send a subscribe message to specify which
    channels, exchanges, and instruments they want updates for.

    Protocol:
        1. Client connects to /ws/updates
        2. Client sends: {"action": "subscribe", "channels": [...], ...}
        3. Server pushes updates every ~1 second
        4. Client can send new subscribe messages to change subscriptions

    Args:
        websocket: The WebSocket connection.
    """
    await manager.connect(websocket)

    # Start update loop if not running
    manager.start_update_loop()

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    channels = message.get("channels", ["state"])
                    exchanges = message.get("exchanges", ["binance", "okx"])
                    instruments = message.get("instruments", ["BTC-USDT-PERP"])

                    manager.subscribe(
                        websocket,
                        channels=channels,
                        exchanges=exchanges,
                        instruments=instruments,
                    )

                    # Send confirmation
                    await websocket.send_json({
                        "type": "subscribed",
                        "channels": channels,
                        "exchanges": exchanges,
                        "instruments": instruments,
                    })

                elif action == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("websocket_error", error=str(e))
        manager.disconnect(websocket)
