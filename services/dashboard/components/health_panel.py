"""
System health panel component for the surveillance dashboard.

This module provides visualization of system health including:
- Connection status per exchange (Binance, OKX)
- Message lag in milliseconds
- Messages per second rate
- Data gaps with timestamps

Note:
    Health data is READ from Redis - never calculated here.
"""

from datetime import datetime
from typing import Dict, List, Optional

from dash import html
import dash_bootstrap_components as dbc

from src.models.health import ConnectionStatus, HealthStatus, GapMarker


def format_timestamp(ts: Optional[datetime]) -> str:
    """
    Format a timestamp for display.

    Args:
        ts: Datetime to format.

    Returns:
        str: Formatted time string.
    """
    if ts is None:
        return "--:--:--"
    return ts.strftime("%H:%M:%S")


def get_connection_status_class(status: ConnectionStatus) -> str:
    """
    Get CSS class for connection status.

    Args:
        status: Connection status enum.

    Returns:
        str: CSS class name.
    """
    status_classes = {
        ConnectionStatus.CONNECTED: "health-connected",
        ConnectionStatus.DISCONNECTED: "health-disconnected",
        ConnectionStatus.DEGRADED: "health-degraded",
        ConnectionStatus.RECONNECTING: "health-degraded",
    }
    return status_classes.get(status, "health-disconnected")


def get_connection_status_icon(status: ConnectionStatus) -> html.I:
    """
    Get Font Awesome icon for connection status.

    Args:
        status: Connection status enum.

    Returns:
        html.I: Icon element.
    """
    status_icons = {
        ConnectionStatus.CONNECTED: "fas fa-check-circle",
        ConnectionStatus.DISCONNECTED: "fas fa-times-circle",
        ConnectionStatus.DEGRADED: "fas fa-exclamation-circle",
        ConnectionStatus.RECONNECTING: "fas fa-sync-alt fa-spin",
    }
    icon_class = status_icons.get(status, "fas fa-question-circle")
    color_class = get_connection_status_class(status)

    return html.I(className=f"{icon_class} {color_class}")


def create_exchange_health_row(exchange: str) -> html.Div:
    """
    Create a health row for a single exchange.

    Args:
        exchange: Exchange name (e.g., "binance").

    Returns:
        html.Div: Health row component.
    """
    exchange_display = exchange.capitalize()
    exchange_id = exchange.lower()

    return html.Div(
        className="health-row",
        children=[
            dbc.Row([
                # Exchange name and status icon
                dbc.Col([
                    html.Span(
                        id=f"health-status-icon-{exchange_id}",
                        children=html.I(className="fas fa-circle text-muted"),
                    ),
                    html.Span(
                        f" {exchange_display}",
                        className="ms-2 fw-bold",
                    ),
                ], lg=2, md=3, sm=4),

                # Connection status
                dbc.Col([
                    html.Small("Status: ", className="text-muted"),
                    html.Span(
                        id=f"health-status-text-{exchange_id}",
                        children="--",
                    ),
                ], lg=2, md=3, sm=4),

                # Lag
                dbc.Col([
                    html.Small("Lag: ", className="text-muted"),
                    html.Span(
                        id=f"health-lag-{exchange_id}",
                        children="-- ms",
                    ),
                ], lg=2, md=2, sm=4),

                # Messages per minute
                dbc.Col([
                    html.Small("Msgs: ", className="text-muted"),
                    html.Span(
                        id=f"health-msgs-{exchange_id}",
                        children="--/min",
                    ),
                ], lg=2, md=2, sm=4),

                # Gaps
                dbc.Col([
                    html.Small("Gaps: ", className="text-muted"),
                    html.Span(
                        id=f"health-gaps-{exchange_id}",
                        children="--",
                    ),
                ], lg=2, md=2, sm=4),

                # Last message
                dbc.Col([
                    html.Small("Last: ", className="text-muted"),
                    html.Span(
                        id=f"health-last-{exchange_id}",
                        children="--:--:--",
                    ),
                ], lg=2, md=2, sm=4),
            ]),
        ],
    )


def create_health_panel() -> html.Div:
    """
    Create the complete system health panel.

    Displays health status for all monitored exchanges.

    Returns:
        html.Div: Complete health panel component.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div(
                className="panel-title d-flex justify-content-between align-items-center",
                children=[
                    html.Span("System Health"),
                    html.Span(
                        id="health-overall-status",
                        className="badge bg-success",
                        children="Healthy",
                    ),
                ],
            ),

            # Exchange health rows
            create_exchange_health_row("binance"),
            create_exchange_health_row("okx"),

            # Overall metrics
            dbc.Row([
                dbc.Col([
                    html.Div(
                        className="mt-3 p-2 text-center",
                        style={"backgroundColor": "#252525", "borderRadius": "4px"},
                        children=[
                            dbc.Row([
                                dbc.Col([
                                    html.Small("Redis", className="text-muted d-block"),
                                    html.Span(
                                        id="health-redis-status",
                                        children=[
                                            html.I(className="fas fa-circle text-muted me-1"),
                                            "--",
                                        ],
                                    ),
                                ], width=3),
                                dbc.Col([
                                    html.Small("PostgreSQL", className="text-muted d-block"),
                                    html.Span(
                                        id="health-postgres-status",
                                        children=[
                                            html.I(className="fas fa-circle text-muted me-1"),
                                            "--",
                                        ],
                                    ),
                                ], width=3),
                                dbc.Col([
                                    html.Small("Total Active Alerts", className="text-muted d-block"),
                                    html.Span(
                                        id="health-active-alerts",
                                        children="--",
                                    ),
                                ], width=3),
                                dbc.Col([
                                    html.Small("Uptime", className="text-muted d-block"),
                                    html.Span(
                                        id="health-uptime",
                                        children="--",
                                    ),
                                ], width=3),
                            ]),
                        ],
                    ),
                ], width=12),
            ]),

            # Recent gaps section
            html.Div(
                id="recent-gaps-container",
                className="mt-3",
                children=[],
            ),
        ],
    )


def render_health_status(health: HealthStatus) -> dict:
    """
    Render health status values for callback output.

    Args:
        health: HealthStatus object.

    Returns:
        dict: Dictionary of display values.
    """
    exchange_id = health.exchange.lower()

    # Determine lag color
    if health.lag_ms < 100:
        lag_class = "text-success"
    elif health.lag_ms < 500:
        lag_class = "text-warning"
    else:
        lag_class = "text-danger"

    # Determine gaps color
    if health.gaps_last_hour == 0:
        gaps_class = "text-success"
    elif health.gaps_last_hour < 3:
        gaps_class = "text-warning"
    else:
        gaps_class = "text-danger"

    return {
        "status_icon": get_connection_status_icon(health.status),
        "status_text": html.Span(
            health.status.value.capitalize(),
            className=get_connection_status_class(health.status),
        ),
        "lag": html.Span(f"{health.lag_ms} ms", className=lag_class),
        "msgs": f"{health.message_count:,}/min",
        "gaps": html.Span(str(health.gaps_last_hour), className=gaps_class),
        "last": format_timestamp(health.last_message_at),
    }


def render_overall_status(health_dict: Dict[str, HealthStatus]) -> tuple:
    """
    Render overall system status based on all exchange health.

    Args:
        health_dict: Dictionary of HealthStatus keyed by exchange.

    Returns:
        tuple: (badge_text, badge_class)
    """
    if not health_dict:
        return "Unknown", "bg-secondary"

    # Check if all exchanges are healthy
    all_healthy = all(h.is_healthy for h in health_dict.values())
    any_disconnected = any(
        h.status == ConnectionStatus.DISCONNECTED
        for h in health_dict.values()
    )
    any_degraded = any(h.is_degraded for h in health_dict.values())

    if all_healthy:
        return "Healthy", "bg-success"
    elif any_disconnected:
        return "Disconnected", "bg-danger"
    elif any_degraded:
        return "Degraded", "bg-warning"
    else:
        return "Warning", "bg-warning"


def render_recent_gaps(gaps: List[GapMarker], max_display: int = 3) -> list:
    """
    Render recent data gaps.

    Args:
        gaps: List of recent GapMarker objects.
        max_display: Maximum number of gaps to display.

    Returns:
        list: List of gap alert components.
    """
    if not gaps:
        return []

    components = [
        html.Small("Recent Gaps:", className="text-muted d-block mb-2"),
    ]

    for gap in gaps[:max_display]:
        components.append(
            dbc.Alert(
                [
                    html.I(className="fas fa-exclamation-triangle me-2"),
                    html.Span(f"{gap.exchange.capitalize()} - {gap.instrument}"),
                    html.Span(
                        f" | {float(gap.duration_seconds):.1f}s",
                        className="text-muted ms-2",
                    ),
                    html.Span(
                        f" | {format_timestamp(gap.gap_start)}",
                        className="text-muted ms-2",
                    ),
                ],
                color="warning",
                className="py-1 px-2 mb-1",
            )
        )

    return components


def render_database_status(is_connected: bool, name: str) -> html.Span:
    """
    Render database connection status.

    Args:
        is_connected: Whether the database is connected.
        name: Database name for display.

    Returns:
        html.Span: Status indicator.
    """
    if is_connected:
        return html.Span([
            html.I(className="fas fa-circle text-success me-1"),
            "Connected",
        ])
    else:
        return html.Span([
            html.I(className="fas fa-circle text-danger me-1"),
            "Disconnected",
        ])
