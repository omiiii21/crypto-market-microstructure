"""
Current state panel component for the surveillance dashboard.

This module provides components for displaying real-time metric values
including spread, depth, basis, and imbalance with status indicators
and z-score warmup handling.

Note:
    All values are READ from Redis - never calculated here.
    Z-score warmup states are displayed when samples < 30.
"""

from decimal import Decimal
from typing import Optional

from dash import html
import dash_bootstrap_components as dbc


def create_metric_card(
    title: str,
    metric_id: str,
    unit: str = "",
    has_zscore: bool = False,
) -> html.Div:
    """
    Create a metric card component.

    Args:
        title: Display title for the metric.
        metric_id: Unique identifier for the metric (used in callback IDs).
        unit: Unit label (e.g., "bps", "$M").
        has_zscore: Whether to include z-score display.

    Returns:
        html.Div: Metric card component.
    """
    children = [
        html.Div(title, className="metric-title"),
        html.Div(
            id=f"metric-value-{metric_id}",
            className="metric-value",
            children="--",
        ),
        html.Span(unit, className="metric-unit"),
        html.Div(
            id=f"metric-status-{metric_id}",
            className="status-indicator",
            children=html.I(className="fas fa-circle status-unavailable"),
        ),
    ]

    # Add z-score indicator if applicable
    if has_zscore:
        children.append(
            html.Div(
                id=f"metric-zscore-{metric_id}",
                className="zscore-indicator mt-2",
                children=render_zscore_indicator(None, 0),
            )
        )

    return html.Div(
        className="metric-card",
        children=children,
    )


def render_zscore_indicator(
    zscore_value: Optional[Decimal],
    sample_count: int,
    min_samples: int = 30,
) -> html.Span:
    """
    Render a z-score indicator with warmup handling.

    During warmup (samples < min_samples), displays progress.
    When active, displays z-score value with appropriate coloring.

    Args:
        zscore_value: Current z-score value, or None during warmup.
        sample_count: Current number of samples collected.
        min_samples: Minimum samples required for z-score calculation.

    Returns:
        html.Span: Z-score indicator component.
    """
    if zscore_value is None:
        return html.Span(
            [
                "Z-Score: warming up ",
                html.Span(
                    f"({sample_count}/{min_samples})",
                    className="warmup-progress",
                ),
            ],
            className="zscore-warmup",
        )

    # Determine color class based on z-score magnitude
    abs_zscore = abs(float(zscore_value))
    if abs_zscore > 3:
        color_class = "zscore-critical"
    elif abs_zscore > 2:
        color_class = "zscore-warning"
    else:
        color_class = "zscore-normal"

    # Format with sigma symbol
    sign = "+" if zscore_value > 0 else ""
    return html.Span(
        f"Z-Score: {sign}{float(zscore_value):.1f}s",
        className=color_class,
    )


def get_status_class(
    value: Optional[Decimal],
    warning_threshold: Optional[Decimal] = None,
    critical_threshold: Optional[Decimal] = None,
    is_lower_worse: bool = False,
) -> str:
    """
    Determine status class based on value and thresholds.

    Args:
        value: Current metric value.
        warning_threshold: Warning level threshold.
        critical_threshold: Critical level threshold.
        is_lower_worse: If True, lower values are worse (e.g., depth).

    Returns:
        str: CSS class for status indicator.
    """
    if value is None:
        return "status-unavailable"

    if critical_threshold is not None:
        if is_lower_worse:
            if value < critical_threshold:
                return "status-critical"
        else:
            if value > critical_threshold:
                return "status-critical"

    if warning_threshold is not None:
        if is_lower_worse:
            if value < warning_threshold:
                return "status-warning"
        else:
            if value > warning_threshold:
                return "status-warning"

    return "status-normal"


def format_metric_value(
    value: Optional[Decimal],
    precision: int = 2,
    prefix: str = "",
    suffix: str = "",
    scale_factor: float = 1.0,
) -> str:
    """
    Format a metric value for display.

    Args:
        value: Value to format.
        precision: Decimal places to show.
        prefix: String prefix (e.g., "$").
        suffix: String suffix (e.g., "M").
        scale_factor: Scale factor to apply (e.g., 1e-6 for millions).

    Returns:
        str: Formatted value string, or "--" if None.
    """
    if value is None:
        return "--"

    scaled_value = float(value) * scale_factor
    formatted = f"{scaled_value:.{precision}f}"
    return f"{prefix}{formatted}{suffix}"


def create_current_state_panel() -> html.Div:
    """
    Create the complete current state panel.

    Displays four key metrics:
    - Spread (bps)
    - Depth ($M at 10bps)
    - Basis (bps)
    - Imbalance (ratio)

    Each metric includes a status indicator and appropriate z-score display.

    Returns:
        html.Div: Complete current state panel.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div("Current State", className="panel-title"),

            dbc.Row([
                # Spread
                dbc.Col([
                    create_metric_card(
                        title="Spread",
                        metric_id="spread",
                        unit="bps",
                        has_zscore=True,
                    ),
                ], lg=3, md=6, sm=12),

                # Depth at 10bps
                dbc.Col([
                    create_metric_card(
                        title="Depth (10bps)",
                        metric_id="depth",
                        unit="",
                        has_zscore=False,
                    ),
                ], lg=3, md=6, sm=12),

                # Basis
                dbc.Col([
                    create_metric_card(
                        title="Basis",
                        metric_id="basis",
                        unit="bps",
                        has_zscore=True,
                    ),
                ], lg=3, md=6, sm=12),

                # Imbalance
                dbc.Col([
                    create_metric_card(
                        title="Imbalance",
                        metric_id="imbalance",
                        unit="",
                        has_zscore=False,
                    ),
                ], lg=3, md=6, sm=12),
            ]),

            # Exchange and instrument info
            dbc.Row([
                dbc.Col([
                    html.Div(
                        id="current-state-info",
                        className="text-muted mt-3 text-center",
                        children=[
                            html.Small("Viewing: "),
                            html.Small(id="current-exchange-display", children="All Exchanges"),
                            html.Small(" | "),
                            html.Small(id="current-instrument-display", children="BTC-USDT-PERP"),
                        ],
                    ),
                ], width=12),
            ]),
        ],
    )


def render_metric_update(
    value: Optional[Decimal],
    precision: int,
    warning_threshold: Optional[Decimal],
    critical_threshold: Optional[Decimal],
    is_lower_worse: bool = False,
    prefix: str = "",
    suffix: str = "",
    scale_factor: float = 1.0,
) -> tuple:
    """
    Render a metric value and status for callback output.

    Args:
        value: Current metric value.
        precision: Decimal places.
        warning_threshold: Warning level.
        critical_threshold: Critical level.
        is_lower_worse: If True, lower values trigger alerts.
        prefix: Display prefix.
        suffix: Display suffix.
        scale_factor: Value scaling.

    Returns:
        tuple: (formatted_value, status_indicator) for callback.
    """
    formatted = format_metric_value(value, precision, prefix, suffix, scale_factor)
    status_class = get_status_class(value, warning_threshold, critical_threshold, is_lower_worse)

    status_indicator = html.I(className=f"fas fa-circle {status_class}")

    return formatted, status_indicator
