"""
Health API endpoint for system status.

Provides:
    GET /api/health - System health status including exchange connections,
                      Redis, PostgreSQL, and overall system state

Note:
    This module is owned by the VIZ agent.
    All health data is READ from Redis - never generated here.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter()


class ExchangeHealthModel(BaseModel):
    """Model for exchange health status."""

    status: str = "unknown"
    lag_ms: Optional[int] = None
    message_rate: Optional[int] = None
    gaps_1h: int = 0
    last_message: Optional[str] = None
    reconnect_count: int = 0


class InfrastructureHealthModel(BaseModel):
    """Model for infrastructure health status."""

    redis: str = "unknown"
    postgres: str = "unknown"


class HealthResponse(BaseModel):
    """Response model for health endpoint."""

    status: str = "unknown"
    exchanges: Dict[str, ExchangeHealthModel]
    infrastructure: InfrastructureHealthModel
    uptime_seconds: int = 0
    timestamp: str

    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "exchanges": {
                    "binance": {
                        "status": "connected",
                        "lag_ms": 23,
                        "message_rate": 1234,
                        "gaps_1h": 0,
                        "last_message": "2025-01-26T12:34:56Z",
                    },
                    "okx": {
                        "status": "connected",
                        "lag_ms": 18,
                        "message_rate": 1189,
                        "gaps_1h": 0,
                        "last_message": "2025-01-26T12:34:56Z",
                    },
                },
                "infrastructure": {
                    "redis": "connected",
                    "postgres": "connected",
                },
                "uptime_seconds": 15780,
                "timestamp": "2025-01-26T12:34:57Z",
            }
        }


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Get system health status",
    description="Retrieves health status for exchanges, Redis, and PostgreSQL.",
)
async def get_health() -> HealthResponse:
    """
    Get system health status.

    Returns:
        HealthResponse: Complete system health status.
    """
    from services.dashboard.app import app_state

    redis_client = app_state.redis_client
    postgres_client = app_state.postgres_client

    now = datetime.utcnow()
    timestamp = now.isoformat() + "Z"

    # Calculate uptime
    uptime_seconds = int((now - app_state.start_time).total_seconds())

    # Check Redis connection
    redis_status = "disconnected"
    if redis_client:
        try:
            if await redis_client.ping():
                redis_status = "connected"
        except Exception:
            redis_status = "error"

    # Check PostgreSQL connection
    postgres_status = "disconnected"
    if postgres_client:
        try:
            if await postgres_client.ping():
                postgres_status = "connected"
        except Exception:
            postgres_status = "error"

    # Get exchange health from Redis
    exchanges: Dict[str, ExchangeHealthModel] = {}

    if redis_client and redis_status == "connected":
        try:
            all_health = await redis_client.get_all_health()

            for exchange_name, health_data in all_health.items():
                exchanges[exchange_name] = ExchangeHealthModel(
                    status=health_data.get("status", "unknown"),
                    lag_ms=health_data.get("lag_ms"),
                    message_rate=health_data.get("message_count"),
                    gaps_1h=health_data.get("gaps_last_hour", 0),
                    last_message=health_data.get("last_message_at"),
                    reconnect_count=health_data.get("reconnect_count", 0),
                )

        except Exception as e:
            logger.error("get_exchange_health_error", error=str(e))

    # Ensure we have entries for expected exchanges
    for exchange_name in ["binance", "okx"]:
        if exchange_name not in exchanges:
            exchanges[exchange_name] = ExchangeHealthModel(status="unknown")

    # Get gap counts from PostgreSQL
    if postgres_client and postgres_status == "connected":
        try:
            for exchange_name in exchanges:
                gaps = await postgres_client.get_gap_count(exchange_name, "1h")
                exchanges[exchange_name].gaps_1h = gaps
        except Exception as e:
            logger.error("get_gap_count_error", error=str(e))

    # Determine overall status
    overall_status = "healthy"

    # Check exchange statuses
    exchange_statuses = [e.status for e in exchanges.values()]
    if all(s == "disconnected" or s == "unknown" for s in exchange_statuses):
        overall_status = "degraded"
    elif any(s == "disconnected" or s == "error" for s in exchange_statuses):
        overall_status = "warning"

    # Check infrastructure
    if redis_status != "connected" or postgres_status != "connected":
        if overall_status == "healthy":
            overall_status = "warning"
        if redis_status != "connected" and postgres_status != "connected":
            overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        exchanges=exchanges,
        infrastructure=InfrastructureHealthModel(
            redis=redis_status,
            postgres=postgres_status,
        ),
        uptime_seconds=uptime_seconds,
        timestamp=timestamp,
    )


class DetailedHealthResponse(BaseModel):
    """Response model for detailed health check."""

    healthy: bool
    redis_connected: bool
    postgres_connected: bool
    redis_ping_ms: Optional[float] = None
    postgres_ping_ms: Optional[float] = None
    metrics_count: Optional[int] = None


@router.get(
    "/health/detailed",
    response_model=DetailedHealthResponse,
    summary="Get detailed health check",
    description="Performs detailed health checks including latency measurements.",
)
async def get_detailed_health() -> DetailedHealthResponse:
    """
    Get detailed health check with latency measurements.

    Returns:
        DetailedHealthResponse: Detailed health status with latency info.
    """
    import time

    from services.dashboard.app import app_state

    redis_client = app_state.redis_client
    postgres_client = app_state.postgres_client

    redis_connected = False
    postgres_connected = False
    redis_ping_ms = None
    postgres_ping_ms = None
    metrics_count = None

    # Test Redis
    if redis_client:
        try:
            start = time.monotonic()
            if await redis_client.ping():
                redis_connected = True
                redis_ping_ms = (time.monotonic() - start) * 1000
        except Exception:
            pass

    # Test PostgreSQL
    if postgres_client:
        try:
            start = time.monotonic()
            if await postgres_client.ping():
                postgres_connected = True
                postgres_ping_ms = (time.monotonic() - start) * 1000

                # Get metrics count
                metrics_count = await postgres_client.get_metrics_count()
        except Exception:
            pass

    healthy = redis_connected and postgres_connected

    return DetailedHealthResponse(
        healthy=healthy,
        redis_connected=redis_connected,
        postgres_connected=postgres_connected,
        redis_ping_ms=round(redis_ping_ms, 2) if redis_ping_ms else None,
        postgres_ping_ms=round(postgres_ping_ms, 2) if postgres_ping_ms else None,
        metrics_count=metrics_count,
    )
