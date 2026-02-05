"""
Metrics API endpoint for historical time series data.

Provides:
    GET /api/metrics/{metric_type}/{exchange}/{instrument} - Historical metrics from PostgreSQL

Metric types:
    - spread: Spread in basis points with z-score
    - basis: Perpetual-spot basis in basis points
    - depth: Order book depth at various levels

Note:
    This module is owned by the VIZ agent.
    All metric values are READ from PostgreSQL - never calculated here.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter()


class MetricDataPoint(BaseModel):
    """Model for a single metric data point."""

    timestamp: Optional[str] = None
    value: Optional[str] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    zscore: Optional[str] = None


class MetricsResponse(BaseModel):
    """Response model for metrics endpoint."""

    metric: str
    exchange: str
    instrument: str
    time_range: str
    data: List[MetricDataPoint]

    class Config:
        json_schema_extra = {
            "example": {
                "metric": "spread",
                "exchange": "binance",
                "instrument": "BTC-USDT-PERP",
                "time_range": "1h",
                "data": [
                    {
                        "timestamp": "2025-01-26T11:00:00Z",
                        "value": "2.1",
                        "zscore": "0.8",
                    },
                    {
                        "timestamp": "2025-01-26T11:01:00Z",
                        "value": "2.3",
                        "zscore": "1.1",
                    },
                ],
            }
        }


class MultiExchangeMetricsResponse(BaseModel):
    """Response model for multi-exchange metrics endpoint."""

    metric: str
    instrument: str
    time_range: str
    binance: List[MetricDataPoint]
    okx: List[MetricDataPoint]


@router.get(
    "/metrics/{metric_type}/all/{instrument}",
    response_model=MultiExchangeMetricsResponse,
    summary="Get historical metrics from all exchanges",
    description="Retrieves historical time series metrics from all exchanges for comparison.",
)
async def get_all_exchange_metrics(
    metric_type: str,
    instrument: str,
    time_range: str = Query(
        "1h",
        description="Time range: '5m', '15m', '1h', '4h', '24h'",
    ),
) -> MultiExchangeMetricsResponse:
    """
    Get historical metrics from all exchanges.

    Args:
        metric_type: Type of metric ('spread', 'basis', 'depth').
        instrument: Instrument identifier.
        time_range: Time range for the query.

    Returns:
        MultiExchangeMetricsResponse: Metrics from both exchanges.
    """
    from services.dashboard.app import app_state

    postgres_client = app_state.postgres_client

    empty_response = MultiExchangeMetricsResponse(
        metric=metric_type,
        instrument=instrument,
        time_range=time_range,
        binance=[],
        okx=[],
    )

    if not postgres_client:
        return empty_response

    try:
        # Fetch from both exchanges
        binance_data = []
        okx_data = []

        if metric_type == "spread":
            binance_raw = await postgres_client.get_spread_metrics(
                exchange="binance",
                instrument=instrument,
                time_range=time_range,
            )
            okx_raw = await postgres_client.get_spread_metrics(
                exchange="okx",
                instrument=instrument,
                time_range=time_range,
            )

            binance_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("spread_bps"),
                    zscore=d.get("zscore"),
                )
                for d in binance_raw
            ]
            okx_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("spread_bps"),
                    zscore=d.get("zscore"),
                )
                for d in okx_raw
            ]

        elif metric_type == "basis":
            binance_raw = await postgres_client.get_basis_metrics(
                exchange="binance",
                perp_instrument=instrument,
                time_range=time_range,
            )
            okx_raw = await postgres_client.get_basis_metrics(
                exchange="okx",
                perp_instrument=instrument,
                time_range=time_range,
            )

            binance_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("basis_bps"),
                    zscore=d.get("zscore"),
                )
                for d in binance_raw
            ]
            okx_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("basis_bps"),
                    zscore=d.get("zscore"),
                )
                for d in okx_raw
            ]

        elif metric_type == "depth":
            binance_raw = await postgres_client.get_depth_metrics(
                exchange="binance",
                instrument=instrument,
                time_range=time_range,
            )
            okx_raw = await postgres_client.get_depth_metrics(
                exchange="okx",
                instrument=instrument,
                time_range=time_range,
            )

            binance_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("depth"),
                )
                for d in binance_raw
            ]
            okx_data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("depth"),
                )
                for d in okx_raw
            ]

        return MultiExchangeMetricsResponse(
            metric=metric_type,
            instrument=instrument,
            time_range=time_range,
            binance=binance_data,
            okx=okx_data,
        )

    except Exception as e:
        logger.error(
            "get_all_exchange_metrics_error",
            metric_type=metric_type,
            instrument=instrument,
            error=str(e),
        )
        return empty_response


@router.get(
    "/metrics/{metric_type}/{exchange}/{instrument}",
    response_model=MetricsResponse,
    summary="Get historical metrics",
    description="Retrieves historical time series metrics from PostgreSQL with optional time range and aggregation.",
)
async def get_metrics(
    metric_type: str,
    exchange: str,
    instrument: str,
    time_range: str = Query(
        "1h",
        description="Time range: '5m', '15m', '1h', '4h', '24h'",
    ),
) -> MetricsResponse:
    """
    Get historical metrics from PostgreSQL.

    Args:
        metric_type: Type of metric ('spread', 'basis', 'depth').
        exchange: Exchange identifier (e.g., "binance", "okx").
        instrument: Instrument identifier (e.g., "BTC-USDT-PERP").
        time_range: Time range for the query.

    Returns:
        MetricsResponse: Historical metric data points.

    Raises:
        HTTPException: If metric type is invalid or data is unavailable.
    """
    from services.dashboard.app import app_state

    postgres_client = app_state.postgres_client

    if not postgres_client:
        return MetricsResponse(
            metric=metric_type,
            exchange=exchange,
            instrument=instrument,
            time_range=time_range,
            data=[],
        )

    try:
        if metric_type == "spread":
            raw_data = await postgres_client.get_spread_metrics(
                exchange=exchange,
                instrument=instrument,
                time_range=time_range,
            )
            data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("spread_bps"),
                    min_value=d.get("min_spread_bps"),
                    max_value=d.get("max_spread_bps"),
                    zscore=d.get("zscore"),
                )
                for d in raw_data
            ]

        elif metric_type == "basis":
            raw_data = await postgres_client.get_basis_metrics(
                exchange=exchange,
                perp_instrument=instrument,
                time_range=time_range,
            )
            data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("basis_bps"),
                    min_value=d.get("min_basis_bps"),
                    max_value=d.get("max_basis_bps"),
                    zscore=d.get("zscore"),
                )
                for d in raw_data
            ]

        elif metric_type == "depth":
            raw_data = await postgres_client.get_depth_metrics(
                exchange=exchange,
                instrument=instrument,
                time_range=time_range,
                bps_level=10,  # Default to 10 bps
            )
            data = [
                MetricDataPoint(
                    timestamp=d.get("timestamp"),
                    value=d.get("depth"),
                    min_value=d.get("min_depth"),
                    max_value=d.get("max_depth"),
                )
                for d in raw_data
            ]

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid metric type: {metric_type}. Valid types: spread, basis, depth",
            )

        return MetricsResponse(
            metric=metric_type,
            exchange=exchange,
            instrument=instrument,
            time_range=time_range,
            data=data,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_metrics_error",
            metric_type=metric_type,
            exchange=exchange,
            instrument=instrument,
            error=str(e),
        )
        return MetricsResponse(
            metric=metric_type,
            exchange=exchange,
            instrument=instrument,
            time_range=time_range,
            data=[],
        )
