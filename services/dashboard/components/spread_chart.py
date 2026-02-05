"""
Spread time series chart component for the surveillance dashboard.

This module provides the spread chart with:
- Binance and OKX lines in different colors
- Warning and critical threshold bands
- Interactive hover and legend

Note:
    Historical data is READ from PostgreSQL - never calculated here.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from dash import html, dcc
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# Chart colors
CHART_COLORS = {
    "binance": "#F0B90B",  # Binance yellow
    "okx": "#00C853",      # OKX green
    "warning": "#ffc107",  # Yellow warning line
    "critical": "#dc3545", # Red critical line
    "grid": "#303030",     # Dark grid
    "text": "#adb5bd",     # Light text
    "background": "#212529",
}


def create_spread_chart_container() -> html.Div:
    """
    Create the spread chart container with loading spinner.

    Returns:
        html.Div: Chart container component.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div("Spread Time Series", className="panel-title"),

            # Chart controls
            dbc.Row([
                dbc.Col([
                    dbc.Checklist(
                        id="spread-chart-options",
                        options=[
                            {"label": " Show Z-Score Overlay", "value": "show_zscore"},
                            {"label": " Show Threshold Bands", "value": "show_thresholds"},
                        ],
                        value=["show_thresholds"],
                        inline=True,
                        className="mb-2",
                    ),
                ], width=12),
            ]),

            # Chart with loading
            dcc.Loading(
                id="spread-chart-loading",
                type="circle",
                children=[
                    dcc.Graph(
                        id="spread-chart",
                        className="chart-container",
                        config={
                            "displayModeBar": True,
                            "displaylogo": False,
                            "modeBarButtonsToRemove": [
                                "pan2d", "select2d", "lasso2d", "autoScale2d"
                            ],
                        },
                        figure=create_empty_spread_chart(),
                    ),
                ],
            ),
        ],
    )


def create_empty_spread_chart() -> go.Figure:
    """
    Create an empty spread chart with proper styling.

    Returns:
        go.Figure: Empty chart figure.
    """
    fig = go.Figure()

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=CHART_COLORS["background"],
        plot_bgcolor=CHART_COLORS["background"],
        margin=dict(l=50, r=30, t=30, b=50),
        height=300,
        xaxis=dict(
            title="Time",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
        ),
        yaxis=dict(
            title="Spread (bps)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
        ),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        annotations=[
            dict(
                text="No data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=14, color=CHART_COLORS["text"]),
            ),
        ],
    )

    return fig


def create_spread_chart(
    binance_data: List[Dict[str, Any]],
    okx_data: List[Dict[str, Any]],
    warning_threshold: float = 3.0,
    critical_threshold: float = 5.0,
    show_thresholds: bool = True,
    show_zscore: bool = False,
) -> go.Figure:
    """
    Create the spread time series chart.

    Args:
        binance_data: List of dicts with timestamp, spread_bps, zscore for Binance.
        okx_data: List of dicts with timestamp, spread_bps, zscore for OKX.
        warning_threshold: Warning threshold in bps.
        critical_threshold: Critical threshold in bps.
        show_thresholds: Whether to show threshold bands.
        show_zscore: Whether to show z-score overlay.

    Returns:
        go.Figure: Configured chart figure.
    """
    # Create figure with secondary y-axis if showing z-score
    if show_zscore:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
    else:
        fig = go.Figure()

    # Check if we have any data
    has_binance = binance_data and len(binance_data) > 0
    has_okx = okx_data and len(okx_data) > 0

    if not has_binance and not has_okx:
        return create_empty_spread_chart()

    # Add Binance spread line
    if has_binance:
        timestamps = [d["timestamp"] for d in binance_data]
        spreads = [float(d["spread_bps"]) if d["spread_bps"] else None for d in binance_data]

        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=spreads,
                name="Binance",
                line=dict(color=CHART_COLORS["binance"], width=2),
                mode="lines",
                hovertemplate="Binance<br>Time: %{x}<br>Spread: %{y:.2f} bps<extra></extra>",
            ),
            secondary_y=False if show_zscore else None,
        )

        # Add z-score overlay for Binance
        if show_zscore:
            zscores = [float(d["zscore"]) if d.get("zscore") else None for d in binance_data]
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=zscores,
                    name="Binance Z-Score",
                    line=dict(color=CHART_COLORS["binance"], width=1, dash="dash"),
                    mode="lines",
                    opacity=0.6,
                    hovertemplate="Binance Z-Score: %{y:.2f}s<extra></extra>",
                ),
                secondary_y=True,
            )

    # Add OKX spread line
    if has_okx:
        timestamps = [d["timestamp"] for d in okx_data]
        spreads = [float(d["spread_bps"]) if d["spread_bps"] else None for d in okx_data]

        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=spreads,
                name="OKX",
                line=dict(color=CHART_COLORS["okx"], width=2),
                mode="lines",
                hovertemplate="OKX<br>Time: %{x}<br>Spread: %{y:.2f} bps<extra></extra>",
            ),
            secondary_y=False if show_zscore else None,
        )

        # Add z-score overlay for OKX
        if show_zscore:
            zscores = [float(d["zscore"]) if d.get("zscore") else None for d in okx_data]
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=zscores,
                    name="OKX Z-Score",
                    line=dict(color=CHART_COLORS["okx"], width=1, dash="dash"),
                    mode="lines",
                    opacity=0.6,
                    hovertemplate="OKX Z-Score: %{y:.2f}s<extra></extra>",
                ),
                secondary_y=True,
            )

    # Add threshold lines
    if show_thresholds:
        # Get x-axis range from data
        all_timestamps = []
        if has_binance:
            all_timestamps.extend([d["timestamp"] for d in binance_data])
        if has_okx:
            all_timestamps.extend([d["timestamp"] for d in okx_data])

        if all_timestamps:
            x_min = min(all_timestamps)
            x_max = max(all_timestamps)

            # Warning threshold
            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[warning_threshold, warning_threshold],
                    name=f"Warning ({warning_threshold} bps)",
                    line=dict(color=CHART_COLORS["warning"], width=1, dash="dash"),
                    mode="lines",
                    hoverinfo="skip",
                ),
                secondary_y=False if show_zscore else None,
            )

            # Critical threshold
            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[critical_threshold, critical_threshold],
                    name=f"Critical ({critical_threshold} bps)",
                    line=dict(color=CHART_COLORS["critical"], width=1, dash="dash"),
                    mode="lines",
                    hoverinfo="skip",
                ),
                secondary_y=False if show_zscore else None,
            )

    # Update layout
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=CHART_COLORS["background"],
        plot_bgcolor=CHART_COLORS["background"],
        margin=dict(l=50, r=50 if show_zscore else 30, t=30, b=50),
        height=300,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        hovermode="x unified",
    )

    fig.update_xaxes(
        title="Time",
        gridcolor=CHART_COLORS["grid"],
        showgrid=True,
    )

    if show_zscore:
        fig.update_yaxes(
            title="Spread (bps)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
            secondary_y=False,
        )
        fig.update_yaxes(
            title="Z-Score (s)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=False,
            secondary_y=True,
        )
    else:
        fig.update_yaxes(
            title="Spread (bps)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
        )

    return fig
