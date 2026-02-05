"""
Basis time series chart component for the surveillance dashboard.

This module provides the basis chart with:
- Positive (contango) and negative (backwardation) coloring
- Warning and critical threshold bands
- Optional z-score overlay on secondary y-axis

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
    "contango": "#28a745",     # Green for positive basis
    "backwardation": "#dc3545", # Red for negative basis
    "warning": "#ffc107",       # Yellow warning line
    "critical": "#dc3545",      # Red critical line
    "grid": "#303030",          # Dark grid
    "text": "#adb5bd",          # Light text
    "background": "#212529",
    "binance": "#F0B90B",
    "okx": "#00C853",
}


def create_basis_chart_container() -> html.Div:
    """
    Create the basis chart container with loading spinner.

    Returns:
        html.Div: Chart container component.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div("Basis Time Series (Perp vs Spot)", className="panel-title"),

            # Chart controls
            dbc.Row([
                dbc.Col([
                    dbc.Checklist(
                        id="basis-chart-options",
                        options=[
                            {"label": " Show Z-Score Overlay", "value": "show_zscore"},
                            {"label": " Show Threshold Bands", "value": "show_thresholds"},
                            {"label": " Color by Direction", "value": "color_direction"},
                        ],
                        value=["show_thresholds", "color_direction"],
                        inline=True,
                        className="mb-2",
                    ),
                ], width=12),
            ]),

            # Chart with loading
            dcc.Loading(
                id="basis-chart-loading",
                type="circle",
                children=[
                    dcc.Graph(
                        id="basis-chart",
                        className="chart-container",
                        config={
                            "displayModeBar": True,
                            "displaylogo": False,
                            "modeBarButtonsToRemove": [
                                "pan2d", "select2d", "lasso2d", "autoScale2d"
                            ],
                        },
                        figure=create_empty_basis_chart(),
                    ),
                ],
            ),
        ],
    )


def create_empty_basis_chart() -> go.Figure:
    """
    Create an empty basis chart with proper styling.

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
            title="Basis (bps)",
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


def create_basis_chart(
    binance_data: List[Dict[str, Any]],
    okx_data: List[Dict[str, Any]],
    warning_threshold: float = 10.0,
    critical_threshold: float = 20.0,
    show_thresholds: bool = True,
    show_zscore: bool = False,
    color_by_direction: bool = True,
) -> go.Figure:
    """
    Create the basis time series chart.

    Args:
        binance_data: List of dicts with timestamp, basis_bps, zscore for Binance.
        okx_data: List of dicts with timestamp, basis_bps, zscore for OKX.
        warning_threshold: Warning threshold in bps (absolute value).
        critical_threshold: Critical threshold in bps (absolute value).
        show_thresholds: Whether to show threshold bands.
        show_zscore: Whether to show z-score overlay.
        color_by_direction: Whether to color by contango/backwardation.

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
        return create_empty_basis_chart()

    def add_basis_trace(data: List[Dict[str, Any]], name: str, base_color: str):
        """Add a basis trace with optional direction coloring."""
        timestamps = [d["timestamp"] for d in data]
        basis_values = [float(d["basis_bps"]) if d["basis_bps"] is not None else None for d in data]

        if color_by_direction:
            # Create filled areas for positive and negative values
            positive_y = [v if v is not None and v >= 0 else 0 for v in basis_values]
            negative_y = [v if v is not None and v < 0 else 0 for v in basis_values]

            # Positive (contango) fill
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=positive_y,
                    name=f"{name} (Contango)",
                    fill="tozeroy",
                    fillcolor="rgba(40, 167, 69, 0.3)",
                    line=dict(color=CHART_COLORS["contango"], width=1),
                    mode="lines",
                    hovertemplate=f"{name}<br>Time: %{{x}}<br>Basis: %{{y:.2f}} bps (Contango)<extra></extra>",
                ),
                secondary_y=False if show_zscore else None,
            )

            # Negative (backwardation) fill
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=negative_y,
                    name=f"{name} (Backwardation)",
                    fill="tozeroy",
                    fillcolor="rgba(220, 53, 69, 0.3)",
                    line=dict(color=CHART_COLORS["backwardation"], width=1),
                    mode="lines",
                    hovertemplate=f"{name}<br>Time: %{{x}}<br>Basis: %{{y:.2f}} bps (Backwardation)<extra></extra>",
                ),
                secondary_y=False if show_zscore else None,
            )
        else:
            # Simple line without direction coloring
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=basis_values,
                    name=name,
                    line=dict(color=base_color, width=2),
                    mode="lines",
                    hovertemplate=f"{name}<br>Time: %{{x}}<br>Basis: %{{y:.2f}} bps<extra></extra>",
                ),
                secondary_y=False if show_zscore else None,
            )

        # Add z-score overlay
        if show_zscore:
            zscores = [float(d["zscore"]) if d.get("zscore") else None for d in data]
            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=zscores,
                    name=f"{name} Z-Score",
                    line=dict(color=base_color, width=1, dash="dash"),
                    mode="lines",
                    opacity=0.6,
                    hovertemplate=f"{name} Z-Score: %{{y:.2f}}s<extra></extra>",
                ),
                secondary_y=True,
            )

    # Add traces for each exchange
    if has_binance:
        add_basis_trace(binance_data, "Binance", CHART_COLORS["binance"])

    if has_okx:
        add_basis_trace(okx_data, "OKX", CHART_COLORS["okx"])

    # Add threshold lines (both positive and negative)
    if show_thresholds:
        all_timestamps = []
        if has_binance:
            all_timestamps.extend([d["timestamp"] for d in binance_data])
        if has_okx:
            all_timestamps.extend([d["timestamp"] for d in okx_data])

        if all_timestamps:
            x_min = min(all_timestamps)
            x_max = max(all_timestamps)

            # Warning thresholds (positive and negative)
            for threshold_val in [warning_threshold, -warning_threshold]:
                fig.add_trace(
                    go.Scatter(
                        x=[x_min, x_max],
                        y=[threshold_val, threshold_val],
                        name=f"Warning ({'+' if threshold_val > 0 else ''}{threshold_val:.0f} bps)",
                        line=dict(color=CHART_COLORS["warning"], width=1, dash="dash"),
                        mode="lines",
                        hoverinfo="skip",
                        showlegend=(threshold_val > 0),
                    ),
                    secondary_y=False if show_zscore else None,
                )

            # Critical thresholds (positive and negative)
            for threshold_val in [critical_threshold, -critical_threshold]:
                fig.add_trace(
                    go.Scatter(
                        x=[x_min, x_max],
                        y=[threshold_val, threshold_val],
                        name=f"Critical ({'+' if threshold_val > 0 else ''}{threshold_val:.0f} bps)",
                        line=dict(color=CHART_COLORS["critical"], width=1, dash="dash"),
                        mode="lines",
                        hoverinfo="skip",
                        showlegend=(threshold_val > 0),
                    ),
                    secondary_y=False if show_zscore else None,
                )

    # Add zero line
    if has_binance or has_okx:
        all_timestamps = []
        if has_binance:
            all_timestamps.extend([d["timestamp"] for d in binance_data])
        if has_okx:
            all_timestamps.extend([d["timestamp"] for d in okx_data])

        if all_timestamps:
            x_min = min(all_timestamps)
            x_max = max(all_timestamps)

            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[0, 0],
                    name="Zero",
                    line=dict(color="#6c757d", width=1),
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
            title="Basis (bps)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
            secondary_y=False,
            zeroline=True,
            zerolinecolor="#6c757d",
        )
        fig.update_yaxes(
            title="Z-Score (s)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=False,
            secondary_y=True,
        )
    else:
        fig.update_yaxes(
            title="Basis (bps)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
            zeroline=True,
            zerolinecolor="#6c757d",
        )

    return fig
