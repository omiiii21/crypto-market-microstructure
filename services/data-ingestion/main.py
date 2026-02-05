"""
Data Ingestion Service entry point.

This service is responsible for:
- Connecting to exchange WebSocket streams
- Streaming order book and ticker data
- Writing snapshots to Redis (current state)
- Detecting data gaps and logging GapMarkers
- Publishing updates via Redis pub/sub
- Reporting health status every second

Usage:
    python -m services.data-ingestion.main

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DATABASE_URL: PostgreSQL connection URL
    LOG_LEVEL: Logging level (default: INFO)
    CONFIG_PATH: Path to config directory (default: config)

Note:
    This module is owned by the ARCHITECT agent for integration.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import structlog

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.adapters.binance.adapter import BinanceAdapter
from src.adapters.okx.adapter import OKXAdapter
from src.config.models import AppConfig, InstrumentConfig
from src.interfaces.exchange_adapter import ExchangeAdapter
from src.models.health import ConnectionStatus, GapMarker, HealthStatus
from src.models.orderbook import OrderBookSnapshot
from src.services import ServiceRunner, setup_logging
from src.storage.redis_client import RedisClient

logger = structlog.get_logger(__name__)


class DataIngestionService(ServiceRunner):
    """
    Data ingestion service for streaming exchange market data.

    Connects to exchange WebSocket streams, normalizes data, detects gaps,
    and publishes updates to Redis.

    Attributes:
        adapters: Dict mapping exchange name to adapter instance.
        instruments_by_exchange: Dict mapping exchange to list of instruments.
        last_snapshot_times: Dict tracking last snapshot per instrument.
    """

    def __init__(self, config_path: str = "config") -> None:
        """Initialize the data ingestion service."""
        super().__init__(config_path)
        self.adapters: Dict[str, ExchangeAdapter] = {}
        self.instruments_by_exchange: Dict[str, List[str]] = {}
        self.last_snapshot_times: Dict[str, datetime] = {}
        self._health_task: Optional[asyncio.Task] = None
        self._stream_tasks: List[asyncio.Task] = []

    @property
    def service_name(self) -> str:
        """Return service name."""
        return "data-ingestion"

    async def _initialize(self) -> None:
        """Initialize exchange adapters."""
        if self.config is None:
            raise RuntimeError("Configuration not loaded")

        # Group instruments by exchange
        enabled_instruments = self.config.get_enabled_instruments()
        enabled_exchanges = self.config.get_enabled_exchanges()

        for exchange_name in enabled_exchanges:
            exchange_config = self.config.get_exchange(exchange_name)
            if exchange_config is None or not exchange_config.enabled:
                continue

            # Get instruments for this exchange
            exchange_instruments = [
                inst for inst in enabled_instruments
                if inst.get_exchange_symbol(exchange_name) is not None
            ]

            if not exchange_instruments:
                self.logger.warning(
                    "no_instruments_for_exchange",
                    exchange=exchange_name,
                )
                continue

            self.instruments_by_exchange[exchange_name] = [
                inst.id for inst in exchange_instruments
            ]

            # Create adapter
            if exchange_name == "binance":
                adapter = BinanceAdapter(exchange_config, exchange_instruments)
            elif exchange_name == "okx":
                adapter = OKXAdapter(exchange_config, exchange_instruments)
            else:
                self.logger.warning(
                    "unknown_exchange",
                    exchange=exchange_name,
                )
                continue

            self.adapters[exchange_name] = adapter
            self.logger.info(
                "adapter_created",
                exchange=exchange_name,
                instruments=self.instruments_by_exchange[exchange_name],
            )

    async def _run(self) -> None:
        """Main service loop - connect and stream data."""
        if self.config is None or self.redis_client is None:
            raise RuntimeError("Service not properly initialized")

        # Connect all adapters
        for exchange_name, adapter in self.adapters.items():
            try:
                await adapter.connect()
                self.logger.info("adapter_connected", exchange=exchange_name)

                # Subscribe to instruments
                instruments = self.instruments_by_exchange.get(exchange_name, [])
                if instruments:
                    await adapter.subscribe(instruments)
                    self.logger.info(
                        "adapter_subscribed",
                        exchange=exchange_name,
                        instruments=instruments,
                    )

            except Exception as e:
                self.logger.error(
                    "adapter_connection_failed",
                    exchange=exchange_name,
                    error=str(e),
                )

        # Start health reporting task
        self._health_task = asyncio.create_task(self._health_loop())

        # Start streaming tasks for each adapter
        for exchange_name, adapter in self.adapters.items():
            if adapter.is_connected:
                task = asyncio.create_task(
                    self._stream_orderbooks(exchange_name, adapter)
                )
                self._stream_tasks.append(task)

        # Wait for shutdown signal
        await self.shutdown_event.wait()

        # Cancel all streaming tasks
        for task in self._stream_tasks:
            task.cancel()

        if self._health_task:
            self._health_task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self._stream_tasks, return_exceptions=True)

    async def _stream_orderbooks(
        self,
        exchange_name: str,
        adapter: ExchangeAdapter,
    ) -> None:
        """
        Stream order books from an adapter and write to Redis.

        Args:
            exchange_name: Exchange identifier.
            adapter: Exchange adapter instance.
        """
        gap_threshold = self.config.features.gap_handling.gap_threshold_seconds  # type: ignore

        try:
            async for snapshot in adapter.stream_order_books():
                if self.shutdown_event.is_set():
                    break

                try:
                    # Check for time-based gaps
                    gap = self._check_time_gap(
                        snapshot=snapshot,
                        gap_threshold=gap_threshold,
                    )

                    if gap is not None:
                        self.logger.warning(
                            "gap_detected",
                            exchange=exchange_name,
                            instrument=snapshot.instrument,
                            duration_seconds=float(gap.duration_seconds),
                            reason=gap.reason,
                        )

                        # Write gap marker to Redis and PostgreSQL
                        if self.postgres_client is not None:
                            await self.postgres_client.insert_gap_marker(gap)

                    # Write snapshot to Redis
                    await self.redis_client.set_orderbook(snapshot)  # type: ignore

                    # Publish update via pub/sub
                    await self.redis_client.publish_orderbook_update(snapshot)  # type: ignore

                    # Update last snapshot time
                    self.last_snapshot_times[snapshot.instrument] = snapshot.timestamp

                    self.logger.debug(
                        "orderbook_received",
                        exchange=exchange_name,
                        instrument=snapshot.instrument,
                        sequence_id=snapshot.sequence_id,
                        spread_bps=str(snapshot.spread_bps) if snapshot.spread_bps else None,
                    )

                except Exception as e:
                    self.logger.error(
                        "orderbook_processing_error",
                        exchange=exchange_name,
                        instrument=snapshot.instrument,
                        error=str(e),
                    )

        except asyncio.CancelledError:
            self.logger.info("stream_cancelled", exchange=exchange_name)
        except Exception as e:
            self.logger.error(
                "stream_error",
                exchange=exchange_name,
                error=str(e),
            )

    def _check_time_gap(
        self,
        snapshot: OrderBookSnapshot,
        gap_threshold: int,
    ) -> Optional[GapMarker]:
        """
        Check for time-based data gaps.

        Args:
            snapshot: Current order book snapshot.
            gap_threshold: Minimum gap duration in seconds.

        Returns:
            Optional[GapMarker]: Gap marker if gap detected.
        """
        last_time = self.last_snapshot_times.get(snapshot.instrument)
        if last_time is None:
            return None

        time_diff = (snapshot.timestamp - last_time).total_seconds()

        if time_diff >= gap_threshold:
            return GapMarker(
                exchange=snapshot.exchange,
                instrument=snapshot.instrument,
                gap_start=last_time,
                gap_end=snapshot.timestamp,
                duration_seconds=Decimal(str(time_diff)),
                reason="time_gap",
                sequence_id_before=None,
                sequence_id_after=snapshot.sequence_id,
            )

        return None

    async def _health_loop(self) -> None:
        """Periodically report health status."""
        try:
            while not self.shutdown_event.is_set():
                for exchange_name, adapter in self.adapters.items():
                    try:
                        health = await adapter.health_check()

                        # Write to Redis
                        await self.redis_client.set_health(health)  # type: ignore

                        # Publish health update
                        await self.redis_client.publish_health_update(health)  # type: ignore

                        self.logger.debug(
                            "health_reported",
                            exchange=exchange_name,
                            status=health.status.value,
                            lag_ms=health.lag_ms,
                            message_count=health.message_count,
                        )

                    except Exception as e:
                        self.logger.error(
                            "health_check_error",
                            exchange=exchange_name,
                            error=str(e),
                        )

                # Wait 1 second before next health check
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.logger.debug("health_loop_cancelled")

    async def _cleanup(self) -> None:
        """Disconnect all adapters."""
        for exchange_name, adapter in self.adapters.items():
            try:
                await adapter.disconnect()
                self.logger.info("adapter_disconnected", exchange=exchange_name)
            except Exception as e:
                self.logger.error(
                    "adapter_disconnect_error",
                    exchange=exchange_name,
                    error=str(e),
                )


async def main() -> None:
    """Main entry point."""
    # Set up initial logging
    setup_logging()

    config_path = os.getenv("CONFIG_PATH", "config")

    logger.info(
        "data_ingestion_service_starting",
        version="1.0.0",
        config_path=config_path,
    )

    service = DataIngestionService(config_path=config_path)

    try:
        await service.run()
    except Exception as e:
        logger.error("service_failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
