"""
Async Redis client for the dashboard service.

This module provides a lightweight async Redis client using redis.asyncio
for reading real-time data from Redis. It does NOT write data - only reads.

Key Patterns:
    - Order books: `orderbook:{exchange}:{instrument}`
    - Z-score buffers: `zscore:{exchange}:{instrument}:{metric}`
    - Active alerts: `alerts:active` (set), `alert:{alert_id}` (hash)
    - Health status: `health:{exchange}`

Note:
    This module is owned by the VIZ agent.
    All data is READ from Redis - the dashboard never writes metrics.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)


class DashboardRedisClient:
    """
    Async Redis client for dashboard data retrieval.

    Provides methods for reading order book snapshots, z-score warmup status,
    active alerts, and system health from Redis. Uses redis.asyncio for
    true async operation without event loop conflicts.

    Attributes:
        url: Redis connection URL.
        _pool: Connection pool for efficient connection reuse.
        _client: Redis client instance.
        _connected: Whether the client is connected.

    Example:
        >>> client = DashboardRedisClient("redis://localhost:6379")
        >>> await client.connect()
        >>> state = await client.get_current_state("binance", "BTC-USDT-PERP")
        >>> await client.disconnect()
    """

    # Key prefixes (must match src/storage/redis_client.py)
    KEY_ORDERBOOK = "orderbook"
    KEY_ZSCORE = "zscore"
    KEY_ALERT = "alert"
    KEY_ALERTS_ACTIVE = "alerts:active"
    KEY_HEALTH = "health"

    def __init__(self, url: str = "redis://localhost:6379"):
        """
        Initialize the Redis client.

        Args:
            url: Redis connection URL.
        """
        self.url = url
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._client is not None

    async def connect(self) -> None:
        """
        Establish connection to Redis.

        Raises:
            RedisConnectionError: If connection fails.
        """
        if self._connected:
            return

        try:
            self._pool = ConnectionPool.from_url(
                self.url,
                max_connections=10,
                decode_responses=True,
            )
            self._client = Redis(connection_pool=self._pool)
            await self._client.ping()
            self._connected = True
            logger.info("dashboard_redis_connected", url=self.url)
        except (RedisConnectionError, OSError) as e:
            self._connected = False
            logger.error("dashboard_redis_connection_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._pool:
            await self._pool.aclose()
            self._pool = None
        self._connected = False
        logger.info("dashboard_redis_disconnected")

    async def ping(self) -> bool:
        """Check Redis connection health."""
        if not self._client:
            return False
        try:
            await self._client.ping()
            return True
        except RedisError:
            return False

    # =========================================================================
    # CURRENT STATE
    # =========================================================================

    async def get_current_state(
        self,
        exchange: str,
        instrument: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get current market state from Redis.

        Args:
            exchange: Exchange identifier (e.g., "binance").
            instrument: Instrument identifier (e.g., "BTC-USDT-PERP").

        Returns:
            Optional[Dict[str, Any]]: Current state data or None if unavailable.
        """
        if not self._client:
            return None

        try:
            key = f"{self.KEY_ORDERBOOK}:{exchange}:{instrument}"
            data = await self._client.get(key)

            if not data:
                return None

            # Parse JSON data
            snapshot = json.loads(data)

            # Extract metrics from snapshot
            result = {
                "exchange": snapshot.get("exchange"),
                "instrument": snapshot.get("instrument"),
                "timestamp": snapshot.get("timestamp"),
                "sequence_id": snapshot.get("sequence_id"),
            }

            # Get bids and asks to compute metrics
            bids = snapshot.get("bids", [])
            asks = snapshot.get("asks", [])

            if bids and asks:
                best_bid = Decimal(str(bids[0]["price"])) if bids else None
                best_ask = Decimal(str(asks[0]["price"])) if asks else None

                if best_bid and best_ask:
                    mid_price = (best_bid + best_ask) / 2
                    spread = best_ask - best_bid
                    spread_bps = (spread / mid_price) * 10000

                    result["best_bid"] = str(best_bid)
                    result["best_ask"] = str(best_ask)
                    result["mid_price"] = str(mid_price)
                    result["spread_bps"] = str(spread_bps)

                # Calculate depth at various levels
                for bps_level in [5, 10, 25]:
                    bid_depth = self._calculate_depth_at_bps(bids, mid_price, bps_level, "bid")
                    ask_depth = self._calculate_depth_at_bps(asks, mid_price, bps_level, "ask")
                    result[f"depth_{bps_level}bps_bid"] = str(bid_depth)
                    result[f"depth_{bps_level}bps_ask"] = str(ask_depth)
                    result[f"depth_{bps_level}bps_total"] = str(bid_depth + ask_depth)

                # Calculate imbalance
                total_bid_notional = sum(
                    Decimal(str(level["price"])) * Decimal(str(level["quantity"]))
                    for level in bids
                )
                total_ask_notional = sum(
                    Decimal(str(level["price"])) * Decimal(str(level["quantity"]))
                    for level in asks
                )
                if total_bid_notional + total_ask_notional > 0:
                    imbalance = (total_bid_notional - total_ask_notional) / (
                        total_bid_notional + total_ask_notional
                    )
                    result["imbalance"] = str(imbalance)

            return result

        except Exception as e:
            logger.error(
                "get_current_state_error",
                exchange=exchange,
                instrument=instrument,
                error=str(e),
            )
            return None

    def _calculate_depth_at_bps(
        self,
        levels: List[Dict[str, Any]],
        mid_price: Decimal,
        bps: int,
        side: str,
    ) -> Decimal:
        """Calculate depth within N basis points of mid price.

        Args:
            levels: Price levels (bids or asks)
            mid_price: Current mid price
            bps: Basis points from mid (e.g., 5, 10, 25)
            side: "bid" or "ask" - determines threshold direction

        Returns:
            Total notional value within the BPS threshold
        """
        if not levels or not mid_price:
            return Decimal("0")

        bps_decimal = Decimal(str(bps)) / Decimal("10000")
        total_notional = Decimal("0")

        if side == "bid":
            # For bids: include prices >= mid * (1 - bps/10000)
            # e.g., at 5 bps with mid=$100,000: include bids >= $99,950
            threshold = mid_price * (Decimal("1") - bps_decimal)
            for level in levels:
                price = Decimal(str(level["price"]))
                quantity = Decimal(str(level["quantity"]))
                if price >= threshold:
                    total_notional += price * quantity
        else:
            # For asks: include prices <= mid * (1 + bps/10000)
            # e.g., at 5 bps with mid=$100,000: include asks <= $100,050
            threshold = mid_price * (Decimal("1") + bps_decimal)
            for level in levels:
                price = Decimal(str(level["price"]))
                quantity = Decimal(str(level["quantity"]))
                if price <= threshold:
                    total_notional += price * quantity

        return total_notional

    # =========================================================================
    # Z-SCORE WARMUP
    # =========================================================================

    async def get_zscore_warmup_status(
        self,
        exchange: str,
        instrument: str,
        metric: str,
        min_samples: int = 30,
    ) -> Dict[str, Any]:
        """
        Get z-score warmup status for a metric.

        Args:
            exchange: Exchange identifier.
            instrument: Instrument identifier.
            metric: Metric name (e.g., "spread_bps").
            min_samples: Minimum samples required for warmup.

        Returns:
            Dict[str, Any]: Warmup status with keys:
                is_warmed_up, sample_count, min_samples, zscore
        """
        if not self._client:
            return {
                "is_warmed_up": False,
                "sample_count": 0,
                "min_samples": min_samples,
                "zscore": None,
            }

        try:
            key = f"{self.KEY_ZSCORE}:{exchange}:{instrument}:{metric}"
            sample_count = await self._client.llen(key)

            is_warmed_up = sample_count >= min_samples

            result = {
                "is_warmed_up": is_warmed_up,
                "sample_count": sample_count,
                "min_samples": min_samples,
                "zscore": None,
            }

            # If warmed up, compute z-score from buffer
            if is_warmed_up:
                samples_str = await self._client.lrange(key, 0, -1)
                if samples_str:
                    samples = [float(s) for s in samples_str]
                    if len(samples) > 1:
                        import statistics
                        mean = statistics.mean(samples)
                        std = statistics.stdev(samples)
                        if std > 0.0001:  # min_std threshold
                            latest = samples[-1]
                            zscore = (latest - mean) / std
                            result["zscore"] = str(round(zscore, 2))

            return result

        except Exception as e:
            logger.error(
                "get_zscore_warmup_status_error",
                exchange=exchange,
                instrument=instrument,
                metric=metric,
                error=str(e),
            )
            return {
                "is_warmed_up": False,
                "sample_count": 0,
                "min_samples": min_samples,
                "zscore": None,
            }

    # =========================================================================
    # ALERTS
    # =========================================================================

    async def get_active_alerts(self) -> List[Dict[str, Any]]:
        """
        Get all active alerts from Redis.

        Returns:
            List[Dict[str, Any]]: List of active alerts.
        """
        if not self._client:
            return []

        try:
            # Get all active alert IDs
            alert_ids = await self._client.smembers(self.KEY_ALERTS_ACTIVE)

            if not alert_ids:
                return []

            # Fetch all alerts
            alerts = []
            for alert_id in alert_ids:
                key = f"{self.KEY_ALERT}:{alert_id}"
                data = await self._client.get(key)
                if data:
                    try:
                        alert = json.loads(data)
                        # Only include active alerts (not resolved)
                        if not alert.get("resolved_at"):
                            # Calculate duration
                            triggered_at = alert.get("triggered_at")
                            if triggered_at:
                                try:
                                    trigger_dt = datetime.fromisoformat(
                                        triggered_at.replace("Z", "+00:00")
                                    )
                                    duration = (datetime.utcnow() - trigger_dt.replace(tzinfo=None)).total_seconds()
                                    alert["duration_seconds"] = int(duration)
                                except Exception:
                                    pass
                            alerts.append(alert)
                    except json.JSONDecodeError:
                        pass

            # Sort by priority (P1 first) then by triggered_at
            priority_order = {"P1": 0, "P2": 1, "P3": 2}
            alerts.sort(
                key=lambda a: (
                    priority_order.get(a.get("priority", "P3"), 3),
                    a.get("triggered_at", ""),
                ),
                reverse=False,
            )

            return alerts

        except Exception as e:
            logger.error("get_active_alerts_error", error=str(e))
            return []

    # =========================================================================
    # HEALTH
    # =========================================================================

    async def get_health_status(self, exchange: str) -> Optional[Dict[str, Any]]:
        """
        Get health status for an exchange.

        Args:
            exchange: Exchange identifier.

        Returns:
            Optional[Dict[str, Any]]: Health status or None if unavailable.
        """
        if not self._client:
            return None

        try:
            key = f"{self.KEY_HEALTH}:{exchange}"
            data = await self._client.get(key)

            if not data:
                return None

            return json.loads(data)

        except Exception as e:
            logger.error(
                "get_health_status_error",
                exchange=exchange,
                error=str(e),
            )
            return None

    async def get_all_health(self) -> Dict[str, Dict[str, Any]]:
        """
        Get health status for all exchanges.

        Returns:
            Dict[str, Dict[str, Any]]: Health status keyed by exchange name.
        """
        if not self._client:
            return {}

        try:
            result = {}
            pattern = f"{self.KEY_HEALTH}:*"

            async for key in self._client.scan_iter(match=pattern, count=100):
                data = await self._client.get(key)
                if data:
                    try:
                        health = json.loads(data)
                        exchange = health.get("exchange")
                        if exchange:
                            result[exchange] = health
                    except json.JSONDecodeError:
                        pass

            return result

        except Exception as e:
            logger.error("get_all_health_error", error=str(e))
            return {}

    # =========================================================================
    # CROSS-EXCHANGE
    # =========================================================================

    async def get_cross_exchange_data(
        self,
        instrument: str,
    ) -> Dict[str, Any]:
        """
        Get cross-exchange comparison data.

        Args:
            instrument: Instrument identifier.

        Returns:
            Dict[str, Any]: Cross-exchange comparison data.
        """
        result = {
            "binance": None,
            "okx": None,
            "divergence_bps": None,
            "price_diff": None,
        }

        binance_state = await self.get_current_state("binance", instrument)
        okx_state = await self.get_current_state("okx", instrument)

        if binance_state and binance_state.get("mid_price"):
            result["binance"] = {
                "mid_price": binance_state.get("mid_price"),
                "spread_bps": binance_state.get("spread_bps"),
                "timestamp": binance_state.get("timestamp"),
            }

        if okx_state and okx_state.get("mid_price"):
            result["okx"] = {
                "mid_price": okx_state.get("mid_price"),
                "spread_bps": okx_state.get("spread_bps"),
                "timestamp": okx_state.get("timestamp"),
            }

        # Calculate divergence
        if result["binance"] and result["okx"]:
            try:
                binance_mid = Decimal(result["binance"]["mid_price"])
                okx_mid = Decimal(result["okx"]["mid_price"])
                avg_price = (binance_mid + okx_mid) / 2
                if avg_price > 0:
                    price_diff = binance_mid - okx_mid
                    divergence_bps = (price_diff / avg_price) * 10000
                    result["price_diff"] = str(price_diff)
                    result["divergence_bps"] = str(divergence_bps)
            except Exception:
                pass

        return result
