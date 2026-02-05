"""
State API endpoint for current market data.

Provides:
    GET /api/state/{exchange}/{instrument} - Current metrics from Redis

Note:
    This module is owned by the VIZ agent.
    All metric values are READ from Redis - never calculated here.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter()


class CurrentStateResponse(BaseModel):
    """Response model for current state endpoint."""

    exchange: str
    instrument: str
    timestamp: Optional[str] = None
    spread_bps: Optional[str] = None
    spread_zscore: Optional[str] = None
    spread_zscore_status: str = "unavailable"
    spread_warmup_progress: Optional[str] = None
    mid_price: Optional[str] = None
    best_bid: Optional[str] = None
    best_ask: Optional[str] = None
    depth_5bps_total: Optional[str] = None
    depth_10bps_total: Optional[str] = None
    depth_25bps_total: Optional[str] = None
    depth_5bps_bid: Optional[str] = None
    depth_5bps_ask: Optional[str] = None
    depth_10bps_bid: Optional[str] = None
    depth_10bps_ask: Optional[str] = None
    depth_25bps_bid: Optional[str] = None
    depth_25bps_ask: Optional[str] = None
    imbalance: Optional[str] = None
    basis_bps: Optional[str] = None
    basis_zscore: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "exchange": "binance",
                "instrument": "BTC-USDT-PERP",
                "timestamp": "2025-01-26T12:34:56.789Z",
                "spread_bps": "2.1",
                "spread_zscore": "0.8",
                "spread_zscore_status": "active",
                "spread_warmup_progress": "30/30",
                "mid_price": "100234.56",
                "depth_5bps_total": "523000",
                "depth_10bps_total": "1234000",
                "depth_25bps_total": "3456000",
                "imbalance": "0.12",
            }
        }


class CrossExchangeResponse(BaseModel):
    """Response model for cross-exchange comparison."""

    instrument: str
    binance: Optional[Dict[str, Any]] = None
    okx: Optional[Dict[str, Any]] = None
    divergence_bps: Optional[str] = None
    price_diff: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "instrument": "BTC-USDT-PERP",
                "binance": {
                    "mid_price": "100234.56",
                    "spread_bps": "2.1",
                    "timestamp": "2025-01-26T12:34:56.789Z",
                },
                "okx": {
                    "mid_price": "100231.23",
                    "spread_bps": "2.3",
                    "timestamp": "2025-01-26T12:34:56.789Z",
                },
                "divergence_bps": "3.33",
                "price_diff": "3.33",
            }
        }


@router.get(
    "/state/cross-exchange/{instrument}",
    response_model=CrossExchangeResponse,
    summary="Get cross-exchange comparison",
    description="Retrieves current prices from both exchanges for comparison.",
)
async def get_cross_exchange_state(
    instrument: str,
) -> CrossExchangeResponse:
    """
    Get cross-exchange comparison data.

    Args:
        instrument: Instrument identifier (e.g., "BTC-USDT-PERP").

    Returns:
        CrossExchangeResponse: Cross-exchange comparison data.
    """
    from services.dashboard.app import app_state

    redis_client = app_state.redis_client

    if not redis_client:
        return CrossExchangeResponse(instrument=instrument)

    try:
        data = await redis_client.get_cross_exchange_data(instrument)

        return CrossExchangeResponse(
            instrument=instrument,
            binance=data.get("binance"),
            okx=data.get("okx"),
            divergence_bps=data.get("divergence_bps"),
            price_diff=data.get("price_diff"),
        )

    except Exception as e:
        logger.error(
            "get_cross_exchange_state_error",
            instrument=instrument,
            error=str(e),
        )
        return CrossExchangeResponse(instrument=instrument)


@router.get(
    "/state/{exchange}/{instrument}",
    response_model=CurrentStateResponse,
    summary="Get current market state",
    description="Retrieves the current market metrics from Redis for the specified exchange and instrument.",
)
async def get_current_state(
    exchange: str,
    instrument: str,
) -> CurrentStateResponse:
    """
    Get current market state from Redis.

    Args:
        exchange: Exchange identifier (e.g., "binance", "okx").
        instrument: Instrument identifier (e.g., "BTC-USDT-PERP").

    Returns:
        CurrentStateResponse: Current market metrics.

    Raises:
        HTTPException: If data is unavailable.
    """
    from services.dashboard.app import app_state

    redis_client = app_state.redis_client

    if not redis_client:
        return CurrentStateResponse(
            exchange=exchange,
            instrument=instrument,
            spread_zscore_status="unavailable",
        )

    try:
        # Get current state
        state = await redis_client.get_current_state(exchange, instrument)

        if not state:
            return CurrentStateResponse(
                exchange=exchange,
                instrument=instrument,
                spread_zscore_status="unavailable",
            )

        # Get z-score warmup status for spread
        warmup = await redis_client.get_zscore_warmup_status(
            exchange, instrument, "spread_bps"
        )

        # Determine z-score status
        if warmup["is_warmed_up"]:
            zscore_status = "active"
            warmup_progress = f"{warmup['sample_count']}/{warmup['min_samples']}"
        else:
            zscore_status = "warming"
            warmup_progress = f"{warmup['sample_count']}/{warmup['min_samples']}"

        return CurrentStateResponse(
            exchange=state.get("exchange", exchange),
            instrument=state.get("instrument", instrument),
            timestamp=state.get("timestamp"),
            spread_bps=state.get("spread_bps"),
            spread_zscore=warmup.get("zscore"),
            spread_zscore_status=zscore_status,
            spread_warmup_progress=warmup_progress,
            mid_price=state.get("mid_price"),
            best_bid=state.get("best_bid"),
            best_ask=state.get("best_ask"),
            depth_5bps_total=state.get("depth_5bps_total"),
            depth_10bps_total=state.get("depth_10bps_total"),
            depth_25bps_total=state.get("depth_25bps_total"),
            depth_5bps_bid=state.get("depth_5bps_bid"),
            depth_5bps_ask=state.get("depth_5bps_ask"),
            depth_10bps_bid=state.get("depth_10bps_bid"),
            depth_10bps_ask=state.get("depth_10bps_ask"),
            depth_25bps_bid=state.get("depth_25bps_bid"),
            depth_25bps_ask=state.get("depth_25bps_ask"),
            imbalance=state.get("imbalance"),
        )

    except Exception as e:
        logger.error(
            "get_current_state_error",
            exchange=exchange,
            instrument=instrument,
            error=str(e),
        )
        return CurrentStateResponse(
            exchange=exchange,
            instrument=instrument,
            spread_zscore_status="error",
        )
