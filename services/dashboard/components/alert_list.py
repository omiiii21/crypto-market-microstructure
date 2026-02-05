"""
Active alerts panel component for the surveillance dashboard.

This module provides components for displaying active P1/P2/P3 alerts
with severity colors, duration tracking, and alert details.

Note:
    Alert data is READ from Redis - alert logic is handled elsewhere.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from dash import html
import dash_bootstrap_components as dbc

from src.models.alerts import Alert, AlertPriority, AlertSeverity


def format_duration(triggered_at: datetime) -> str:
    """
    Format the duration since an alert was triggered.

    Args:
        triggered_at: When the alert was triggered.

    Returns:
        str: Human-readable duration string.
    """
    now = datetime.utcnow()
    delta = now - triggered_at
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def get_priority_badge(priority: AlertPriority) -> html.Span:
    """
    Create a priority badge component.

    Args:
        priority: Alert priority level.

    Returns:
        html.Span: Badge component with appropriate styling.
    """
    badge_classes = {
        AlertPriority.P1: "badge-p1",
        AlertPriority.P2: "badge-p2",
        AlertPriority.P3: "badge-p3",
    }

    return html.Span(
        priority.value,
        className=f"alert-badge {badge_classes.get(priority, 'badge-p3')}",
    )


def create_alert_card(alert: Alert) -> html.Div:
    """
    Create an alert card component.

    Args:
        alert: Alert instance to display.

    Returns:
        html.Div: Alert card component.
    """
    card_classes = {
        AlertPriority.P1: "alert-p1",
        AlertPriority.P2: "alert-p2",
        AlertPriority.P3: "alert-p3",
    }

    return html.Div(
        className=f"alert-card {card_classes.get(alert.priority, 'alert-p3')}",
        children=[
            dbc.Row([
                # Priority badge and alert type
                dbc.Col([
                    get_priority_badge(alert.priority),
                    html.Span(
                        f" {alert.alert_type.replace('_', ' ').title()}",
                        className="ms-2",
                    ),
                ], width=6),

                # Duration
                dbc.Col([
                    html.Span(
                        format_duration(alert.triggered_at),
                        className="text-muted",
                    ),
                ], width=3, className="text-end"),

                # Exchange
                dbc.Col([
                    html.Span(
                        alert.exchange.capitalize(),
                        className="text-muted",
                    ),
                ], width=3, className="text-end"),
            ]),

            # Alert details
            dbc.Row([
                dbc.Col([
                    html.Small(
                        [
                            html.Span(f"{alert.instrument}", className="text-info"),
                            html.Span(" | ", className="text-muted"),
                            html.Span(
                                f"{alert.trigger_metric}: {float(alert.trigger_value):.2f}",
                                className="text-warning",
                            ),
                            html.Span(" > ", className="text-muted"),
                            html.Span(
                                f"{float(alert.trigger_threshold):.2f}",
                                className="text-muted",
                            ),
                        ],
                        className="mt-1 d-block",
                    ),
                ], width=12),
            ]),

            # Z-score if available
            html.Div(
                html.Small(
                    f"Z-Score: {float(alert.zscore_value):.1f}s",
                    className="text-muted",
                ),
                className="mt-1",
            ) if alert.zscore_value is not None else None,
        ],
    )


def create_alerts_panel() -> html.Div:
    """
    Create the complete active alerts panel.

    The panel displays:
    - Header with active alert count
    - List of active alerts sorted by priority then time
    - "No active alerts" message when empty

    Returns:
        html.Div: Complete alerts panel component.
    """
    return html.Div(
        className="panel-container",
        children=[
            # Header with count
            html.Div(
                className="panel-title d-flex justify-content-between align-items-center",
                children=[
                    html.Span("Active Alerts"),
                    html.Span(
                        id="alerts-count-badge",
                        className="badge bg-secondary",
                        children="0",
                    ),
                ],
            ),

            # Alerts list container
            html.Div(
                id="alerts-list-container",
                style={"maxHeight": "400px", "overflowY": "auto"},
                children=[
                    render_no_alerts_message(),
                ],
            ),
        ],
    )


def render_no_alerts_message() -> html.Div:
    """
    Render the "no active alerts" message.

    Returns:
        html.Div: No alerts message component.
    """
    return html.Div(
        className="no-data-message",
        children=[
            html.I(className="fas fa-check-circle fa-2x mb-3", style={"color": "#28a745"}),
            html.Div("No active alerts"),
        ],
    )


def render_alerts_list(alerts: List[Alert]) -> List[html.Div]:
    """
    Render a list of alert cards.

    Alerts are sorted by priority (P1 first) then by triggered time (newest first).

    Args:
        alerts: List of active alerts.

    Returns:
        List[html.Div]: List of alert card components.
    """
    if not alerts:
        return [render_no_alerts_message()]

    # Sort by priority (P1=0, P2=1, P3=2) then by time (newest first)
    priority_order = {AlertPriority.P1: 0, AlertPriority.P2: 1, AlertPriority.P3: 2}
    sorted_alerts = sorted(
        alerts,
        key=lambda a: (priority_order.get(a.priority, 3), -a.triggered_at.timestamp()),
    )

    return [create_alert_card(alert) for alert in sorted_alerts]


def get_priority_counts(alerts: List[Alert]) -> dict:
    """
    Count alerts by priority.

    Args:
        alerts: List of active alerts.

    Returns:
        dict: Counts keyed by priority string.
    """
    counts = {
        "P1": 0,
        "P2": 0,
        "P3": 0,
        "total": 0,
    }

    for alert in alerts:
        counts[alert.priority.value] = counts.get(alert.priority.value, 0) + 1
        counts["total"] += 1

    return counts
