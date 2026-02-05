"""
Alerts API endpoint for active and historical alerts.

Provides:
    GET /api/alerts - Active alerts from Redis with optional filters

Note:
    This module is owned by the VIZ agent.
    All alert data is READ from Redis/PostgreSQL - never generated here.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter()


class AlertItem(BaseModel):
    """Model for a single alert."""

    alert_id: str
    alert_type: str
    priority: str
    severity: Optional[str] = None
    exchange: Optional[str] = None
    instrument: Optional[str] = None
    trigger_metric: Optional[str] = None
    trigger_value: Optional[str] = None
    trigger_threshold: Optional[str] = None
    zscore_value: Optional[str] = None
    zscore_threshold: Optional[str] = None
    triggered_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    resolved_at: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "alert_id": "alert_abc123",
                "alert_type": "basis_warning",
                "priority": "P2",
                "severity": "warning",
                "exchange": "binance",
                "instrument": "BTC-USDT-PERP",
                "trigger_metric": "basis_bps",
                "trigger_value": "12.3",
                "trigger_threshold": "10.0",
                "zscore_value": "2.3",
                "zscore_threshold": "2.0",
                "triggered_at": "2025-01-26T12:32:41Z",
                "duration_seconds": 135,
            }
        }


class AlertCountsModel(BaseModel):
    """Model for alert counts by priority."""

    P1: int = 0
    P2: int = 0
    P3: int = 0
    total: int = 0


class AlertsResponse(BaseModel):
    """Response model for alerts endpoint."""

    alerts: List[AlertItem]
    counts: AlertCountsModel

    class Config:
        json_schema_extra = {
            "example": {
                "alerts": [
                    {
                        "alert_id": "alert_abc123",
                        "alert_type": "basis_warning",
                        "priority": "P2",
                        "exchange": "binance",
                        "instrument": "BTC-USDT-PERP",
                        "trigger_metric": "basis_bps",
                        "trigger_value": "12.3",
                        "trigger_threshold": "10.0",
                        "zscore_value": "2.3",
                        "triggered_at": "2025-01-26T12:32:41Z",
                        "duration_seconds": 135,
                    }
                ],
                "counts": {"P1": 0, "P2": 1, "P3": 0, "total": 1},
            }
        }


@router.get(
    "/alerts",
    response_model=AlertsResponse,
    summary="Get active alerts",
    description="Retrieves active alerts from Redis with optional priority and instrument filters.",
)
async def get_alerts(
    status: Optional[str] = Query(
        "active",
        description="Alert status filter: 'active' or 'all'",
    ),
    priority: Optional[str] = Query(
        None,
        description="Priority filter: 'P1', 'P2', 'P3', or comma-separated list",
    ),
    exchange: Optional[str] = Query(
        None,
        description="Exchange filter: 'binance' or 'okx'",
    ),
    instrument: Optional[str] = Query(
        None,
        description="Instrument filter: e.g., 'BTC-USDT-PERP'",
    ),
) -> AlertsResponse:
    """
    Get active alerts from Redis.

    Args:
        status: Alert status filter ('active' or 'all').
        priority: Priority filter (P1, P2, P3, or comma-separated).
        exchange: Exchange filter.
        instrument: Instrument filter.

    Returns:
        AlertsResponse: List of alerts with counts.
    """
    from services.dashboard.app import app_state

    redis_client = app_state.redis_client

    if not redis_client:
        return AlertsResponse(
            alerts=[],
            counts=AlertCountsModel(),
        )

    try:
        # Get all active alerts from Redis
        alerts_data = await redis_client.get_active_alerts()

        # Apply filters
        filtered_alerts = []
        priority_filter = None
        if priority:
            priority_filter = [p.strip().upper() for p in priority.split(",")]

        for alert in alerts_data:
            # Priority filter
            if priority_filter and alert.get("priority") not in priority_filter:
                continue

            # Exchange filter
            if exchange and alert.get("exchange") != exchange:
                continue

            # Instrument filter
            if instrument and alert.get("instrument") != instrument:
                continue

            filtered_alerts.append(
                AlertItem(
                    alert_id=alert.get("alert_id", ""),
                    alert_type=alert.get("alert_type", ""),
                    priority=alert.get("priority", "P3"),
                    severity=alert.get("severity"),
                    exchange=alert.get("exchange"),
                    instrument=alert.get("instrument"),
                    trigger_metric=alert.get("trigger_metric"),
                    trigger_value=str(alert.get("trigger_value")) if alert.get("trigger_value") else None,
                    trigger_threshold=str(alert.get("trigger_threshold")) if alert.get("trigger_threshold") else None,
                    zscore_value=str(alert.get("zscore_value")) if alert.get("zscore_value") else None,
                    zscore_threshold=str(alert.get("zscore_threshold")) if alert.get("zscore_threshold") else None,
                    triggered_at=alert.get("triggered_at"),
                    duration_seconds=alert.get("duration_seconds"),
                    resolved_at=alert.get("resolved_at"),
                )
            )

        # Calculate counts
        counts = AlertCountsModel(
            P1=sum(1 for a in filtered_alerts if a.priority == "P1"),
            P2=sum(1 for a in filtered_alerts if a.priority == "P2"),
            P3=sum(1 for a in filtered_alerts if a.priority == "P3"),
            total=len(filtered_alerts),
        )

        return AlertsResponse(
            alerts=filtered_alerts,
            counts=counts,
        )

    except Exception as e:
        logger.error("get_alerts_error", error=str(e))
        return AlertsResponse(
            alerts=[],
            counts=AlertCountsModel(),
        )


@router.get(
    "/alerts/history",
    response_model=AlertsResponse,
    summary="Get alert history",
    description="Retrieves historical alerts from PostgreSQL.",
)
async def get_alert_history(
    time_range: str = Query(
        "24h",
        description="Time range: '1h', '4h', '24h'",
    ),
    priority: Optional[str] = Query(
        None,
        description="Priority filter: 'P1', 'P2', 'P3'",
    ),
    exchange: Optional[str] = Query(
        None,
        description="Exchange filter",
    ),
    instrument: Optional[str] = Query(
        None,
        description="Instrument filter",
    ),
    status: Optional[str] = Query(
        None,
        description="Status filter: 'active' or 'resolved'",
    ),
    limit: int = Query(
        100,
        description="Maximum number of results",
        ge=1,
        le=1000,
    ),
) -> AlertsResponse:
    """
    Get alert history from PostgreSQL.

    Args:
        time_range: Time range to query.
        priority: Priority filter.
        exchange: Exchange filter.
        instrument: Instrument filter.
        status: Status filter.
        limit: Maximum results.

    Returns:
        AlertsResponse: List of historical alerts with counts.
    """
    from services.dashboard.app import app_state

    postgres_client = app_state.postgres_client

    if not postgres_client:
        return AlertsResponse(
            alerts=[],
            counts=AlertCountsModel(),
        )

    try:
        alerts_data = await postgres_client.get_alert_history(
            time_range=time_range,
            exchange=exchange,
            instrument=instrument,
            priority=priority,
            status=status,
            limit=limit,
        )

        alerts = [
            AlertItem(
                alert_id=a.get("alert_id", ""),
                alert_type=a.get("alert_type", ""),
                priority=a.get("priority", "P3"),
                severity=a.get("severity"),
                exchange=a.get("exchange"),
                instrument=a.get("instrument"),
                trigger_metric=a.get("trigger_metric"),
                trigger_value=a.get("trigger_value"),
                trigger_threshold=a.get("trigger_threshold"),
                zscore_value=a.get("zscore_value"),
                zscore_threshold=a.get("zscore_threshold"),
                triggered_at=a.get("triggered_at"),
                duration_seconds=a.get("duration_seconds"),
                resolved_at=a.get("resolved_at"),
            )
            for a in alerts_data
        ]

        counts = AlertCountsModel(
            P1=sum(1 for a in alerts if a.priority == "P1"),
            P2=sum(1 for a in alerts if a.priority == "P2"),
            P3=sum(1 for a in alerts if a.priority == "P3"),
            total=len(alerts),
        )

        return AlertsResponse(alerts=alerts, counts=counts)

    except Exception as e:
        logger.error("get_alert_history_error", error=str(e))
        return AlertsResponse(
            alerts=[],
            counts=AlertCountsModel(),
        )
