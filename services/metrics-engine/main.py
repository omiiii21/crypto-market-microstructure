"""
Metrics Engine Service entry point.

This service is responsible for:
- Subscribing to Redis pub/sub for orderbook updates
- Calculating all metrics (spread, depth, basis)
- Maintaining z-score calculators with warmup guards
- Writing current metrics to Redis
- Batch writing historical metrics to PostgreSQL every 1 second
- Resetting z-scores on gap detection

Usage:
    python -m services.metrics-engine.main

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
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config.models import AppConfig
from src.metrics.aggregator import MetricsAggregator
from src.metrics.zscore import ZScoreCalculator
from src.models.metrics import AggregatedMetrics, BasisMetrics, DepthMetrics, SpreadMetrics
from src.models.orderbook import OrderBookSnapshot
from src.services import ServiceRunner, setup_logging
from src.storage.postgres_client import PostgresClient
from src.storage.redis_client import RedisClient

logger = structlog.get_logger(__name__)


# Redis pub/sub channel for metrics updates
CHANNEL_METRICS = "updates:metrics"


class MetricsEngineService(ServiceRunner):
    """
    Metrics computation service.

    Subscribes to orderbook updates, calculates all metrics, updates z-score
    buffers, and writes to both Redis and PostgreSQL.

    Attributes:
        aggregators: Dict mapping instrument to MetricsAggregator instance.
        zscore_calculators: Dict mapping (instrument, metric) to ZScoreCalculator.
        metrics_buffer: Buffer for batch writing to PostgreSQL.
        last_write_time: Last PostgreSQL batch write time.
        basis_pairs: Dict mapping perp instrument to spot instrument.
    """

    def __init__(self, config_path: str = "config") -> None:
        """Initialize the metrics engine service."""
        super().__init__(config_path)
        self.aggregators: Dict[str, MetricsAggregator] = {}
        self.zscore_calculators: Dict[Tuple[str, str], ZScoreCalculator] = {}
        self.metrics_buffer: Dict[str, List[Tuple[str, str, datetime, Any]]] = {
            "spread": [],
            "depth": [],
            "basis": [],
        }
        self.last_write_time = datetime.now(timezone.utc)
        self.basis_pairs: Dict[str, str] = {}  # perp -> spot
        self.last_snapshots: Dict[str, OrderBookSnapshot] = {}  # Latest snapshot per instrument
        self._pubsub_task: Optional[asyncio.Task] = None
        self._batch_write_task: Optional[asyncio.Task] = None
        self._warmup_progress: Dict[str, int] = defaultdict(int)
        self._last_warmup_log: Dict[str, datetime] = {}

    @property
    def service_name(self) -> str:
        """Return service name."""
        return "metrics-engine"

    async def _initialize(self) -> None:
        """Initialize aggregators and z-score calculators."""
        if self.config is None:
            raise RuntimeError("Configuration not loaded")

        zscore_config = self.config.features.zscore
        enabled_instruments = self.config.get_enabled_instruments()

        # Build basis pairs mapping
        for pair in self.config.basis_pairs:
            self.basis_pairs[pair.perp] = pair.spot

        # Create aggregators and z-score calculators for each instrument
        for instrument in enabled_instruments:
            # Create aggregator
            self.aggregators[instrument.id] = MetricsAggregator(
                use_zscore=zscore_config.enabled,
                zscore_window=zscore_config.window_size,
                zscore_min_samples=zscore_config.min_samples,
                bps_levels=[5, 10, 25],
                depth_reference_level=10,
            )

            # Create separate z-score calculators for metrics
            for metric in ["spread_bps", "basis_bps"]:
                key = (instrument.id, metric)
                self.zscore_calculators[key] = ZScoreCalculator(
                    window_size=zscore_config.window_size,
                    min_samples=zscore_config.min_samples,
                    min_std=float(zscore_config.min_std),
                )

            self.logger.info(
                "aggregator_created",
                instrument=instrument.id,
                use_zscore=zscore_config.enabled,
                zscore_window=zscore_config.window_size,
            )

    async def _run(self) -> None:
        """Main service loop - subscribe to updates and process."""
        if self.redis_client is None:
            raise RuntimeError("Redis client not initialized")

        # Start batch write task
        self._batch_write_task = asyncio.create_task(self._batch_write_loop())

        # Subscribe to orderbook updates
        try:
            async with self.redis_client.subscribe(
                [RedisClient.CHANNEL_ORDERBOOK]
            ) as messages:
                async for message in messages:
                    if self.shutdown_event.is_set():
                        break

                    try:
                        await self._process_update(message)
                    except Exception as e:
                        self.logger.error(
                            "update_processing_error",
                            error=str(e),
                        )

        except asyncio.CancelledError:
            self.logger.info("pubsub_cancelled")

        # Cancel batch write task
        if self._batch_write_task:
            self._batch_write_task.cancel()
            try:
                await self._batch_write_task
            except asyncio.CancelledError:
                pass

        # Final batch write
        await self._flush_buffer()

    async def _process_update(self, message: Dict[str, Any]) -> None:
        """
        Process an orderbook update message.

        Args:
            message: Pub/sub message containing update data.
        """
        if self.config is None:
            return

        channel = message.get("channel")
        data = message.get("data", {})

        if channel != RedisClient.CHANNEL_ORDERBOOK:
            return

        exchange = data.get("exchange")
        instrument = data.get("instrument")

        if not exchange or not instrument:
            return

        # Fetch full snapshot from Redis
        snapshot = await self.redis_client.get_orderbook(exchange, instrument)  # type: ignore
        if snapshot is None:
            self.logger.debug(
                "snapshot_not_found",
                exchange=exchange,
                instrument=instrument,
            )
            return

        # Store latest snapshot
        self.last_snapshots[instrument] = snapshot

        # Check for gap and reset z-scores if needed
        await self._check_gap_and_reset(exchange, instrument)

        # Get aggregator
        aggregator = self.aggregators.get(instrument)
        if aggregator is None:
            self.logger.debug(
                "no_aggregator_for_instrument",
                instrument=instrument,
            )
            return

        # Get spot snapshot for basis calculation (if applicable)
        spot_snapshot: Optional[OrderBookSnapshot] = None
        spot_instrument = self.basis_pairs.get(instrument)
        if spot_instrument:
            spot_snapshot = self.last_snapshots.get(spot_instrument)

        # Calculate all metrics
        try:
            metrics = aggregator.calculate_all(
                perp=snapshot,
                spot=spot_snapshot,
            )

            # Write current metrics to Redis
            await self._write_metrics_to_redis(metrics)

            # Buffer for batch write to PostgreSQL
            self._buffer_metrics(metrics)

            # Log warmup progress
            self._log_warmup_progress(instrument, aggregator)

            self.logger.debug(
                "metrics_calculated",
                exchange=exchange,
                instrument=instrument,
                spread_bps=str(metrics.spread.spread_bps),
                basis_bps=str(metrics.basis.basis_bps) if metrics.basis else None,
            )

        except Exception as e:
            self.logger.error(
                "metrics_calculation_error",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )

    async def _check_gap_and_reset(self, exchange: str, instrument: str) -> None:
        """
        Check for gaps and reset z-scores if needed.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
        """
        if self.config is None or self.redis_client is None:
            return

        gap_config = self.config.features.gap_handling
        zscore_config = self.config.features.zscore

        if not zscore_config.reset_on_gap:
            return

        # Check for recent gaps in Redis or from health status
        health = await self.redis_client.get_health(exchange)
        if health is None:
            return

        # If there are recent gaps, reset z-scores
        if health.gaps_last_hour > 0:
            # Reset the aggregator's z-scores
            aggregator = self.aggregators.get(instrument)
            if aggregator is not None:
                aggregator.reset_all_zscores(reason="gap_detected")
                self.logger.info(
                    "zscore_reset_on_gap",
                    instrument=instrument,
                    exchange=exchange,
                )

    async def _write_metrics_to_redis(self, metrics: AggregatedMetrics) -> None:
        """
        Write current metrics to Redis.

        Args:
            metrics: Aggregated metrics to write.
        """
        if self.redis_client is None:
            return

        # Publish metrics update
        update_data = {
            "exchange": metrics.exchange,
            "instrument": metrics.instrument,
            "timestamp": metrics.timestamp.isoformat(),
            "spread_bps": str(metrics.spread.spread_bps),
            "spread_zscore": str(metrics.spread.zscore) if metrics.spread.zscore else None,
            "depth_10bps_total": str(metrics.depth.depth_10bps_total),
        }

        if metrics.basis:
            update_data["basis_bps"] = str(metrics.basis.basis_bps)
            update_data["basis_zscore"] = str(metrics.basis.zscore) if metrics.basis.zscore else None

        await self.redis_client._require_connection().publish(
            CHANNEL_METRICS,
            json.dumps(update_data),
        )

    def _buffer_metrics(self, metrics: AggregatedMetrics) -> None:
        """
        Buffer metrics for batch writing to PostgreSQL.

        Args:
            metrics: Aggregated metrics to buffer.
        """
        # Buffer spread metrics
        self.metrics_buffer["spread"].append((
            metrics.exchange,
            metrics.instrument,
            metrics.timestamp,
            metrics.spread,
        ))

        # Buffer depth metrics
        self.metrics_buffer["depth"].append((
            metrics.exchange,
            metrics.instrument,
            metrics.timestamp,
            metrics.depth,
        ))

        # Buffer basis metrics (if available)
        if metrics.basis is not None:
            # For basis, we need perp_instrument, spot_instrument, exchange, timestamp, metrics
            spot_instrument = self.basis_pairs.get(metrics.instrument, "UNKNOWN")
            self.metrics_buffer["basis"].append((
                metrics.instrument,  # perp
                spot_instrument,     # spot
                metrics.exchange,
                metrics.timestamp,
                metrics.basis,
            ))

    async def _batch_write_loop(self) -> None:
        """Periodically batch write metrics to PostgreSQL."""
        try:
            while not self.shutdown_event.is_set():
                await asyncio.sleep(1.0)  # Write every 1 second
                await self._flush_buffer()

        except asyncio.CancelledError:
            self.logger.debug("batch_write_loop_cancelled")

    async def _flush_buffer(self) -> None:
        """Flush metrics buffer to PostgreSQL."""
        if self.postgres_client is None:
            return

        try:
            # Write spread metrics
            if self.metrics_buffer["spread"]:
                spread_data = [
                    (exchange, instrument, ts, spread)
                    for exchange, instrument, ts, spread in self.metrics_buffer["spread"]
                ]
                count = await self.postgres_client.insert_spread_metrics(spread_data)
                self.logger.debug(
                    "spread_metrics_written",
                    count=count,
                )
                self.metrics_buffer["spread"] = []

            # Write depth metrics
            if self.metrics_buffer["depth"]:
                depth_data = [
                    (exchange, instrument, ts, depth)
                    for exchange, instrument, ts, depth in self.metrics_buffer["depth"]
                ]
                count = await self.postgres_client.insert_depth_metrics(depth_data)
                self.logger.debug(
                    "depth_metrics_written",
                    count=count,
                )
                self.metrics_buffer["depth"] = []

            # Write basis metrics
            if self.metrics_buffer["basis"]:
                basis_data = [
                    (perp, spot, exchange, ts, basis)
                    for perp, spot, exchange, ts, basis in self.metrics_buffer["basis"]
                ]
                count = await self.postgres_client.insert_basis_metrics(basis_data)
                self.logger.debug(
                    "basis_metrics_written",
                    count=count,
                )
                self.metrics_buffer["basis"] = []

            self.last_write_time = datetime.now(timezone.utc)

        except Exception as e:
            self.logger.error(
                "batch_write_error",
                error=str(e),
            )

    def _log_warmup_progress(
        self,
        instrument: str,
        aggregator: MetricsAggregator,
    ) -> None:
        """
        Log z-score warmup progress periodically.

        Args:
            instrument: Instrument identifier.
            aggregator: Aggregator instance.
        """
        if self.config is None:
            return

        warmup_interval = self.config.features.zscore.warmup_log_interval
        now = datetime.now(timezone.utc)

        last_log = self._last_warmup_log.get(instrument)
        if last_log is not None:
            elapsed = (now - last_log).total_seconds()
            if elapsed < warmup_interval:
                return

        # Get z-score statuses
        statuses = aggregator.zscore_statuses

        for metric_name, status in statuses.items():
            if status is not None and not status.is_ready:
                self.logger.info(
                    "zscore_warmup_progress",
                    instrument=instrument,
                    metric=metric_name,
                    samples=status.samples_collected,
                    required=status.samples_required,
                    progress_pct=round(status.samples_collected / status.samples_required * 100, 1),
                )

        self._last_warmup_log[instrument] = now

    async def _cleanup(self) -> None:
        """Service-specific cleanup."""
        # Final buffer flush
        await self._flush_buffer()
        self.logger.info("final_buffer_flushed")


async def main() -> None:
    """Main entry point."""
    # Set up initial logging
    setup_logging()

    config_path = os.getenv("CONFIG_PATH", "config")

    logger.info(
        "metrics_engine_service_starting",
        version="1.0.0",
        config_path=config_path,
    )

    service = MetricsEngineService(config_path=config_path)

    try:
        await service.run()
    except Exception as e:
        logger.error("service_failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
