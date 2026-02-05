"""
Depth heatmap/bar chart component for the surveillance dashboard.

This module provides visualization of order book depth at:
- 5 bps from mid price
- 10 bps from mid price
- 25 bps from mid price

With split view showing bid (green) vs ask (red) depth.

Note:
    Depth data is READ from Redis - never calculated here.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from dash import html, dcc
import dash_bootstrap_components as dbc
import plotly.graph_objects as go


# Chart colors
CHART_COLORS = {
    "bid": "#28a745",       # Green for bids
    "ask": "#dc3545",       # Red for asks
    "grid": "#303030",      # Dark grid
    "text": "#adb5bd",      # Light text
    "background": "#212529",
}


def create_depth_heatmap_container() -> html.Div:
    """
    Create the depth heatmap container with loading spinner.

    Returns:
        html.Div: Chart container component.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div("Order Book Depth", className="panel-title"),

            # Exchange selector for depth
            dbc.Row([
                dbc.Col([
                    dbc.RadioItems(
                        id="depth-exchange-select",
                        options=[
                            {"label": "Binance", "value": "binance"},
                            {"label": "OKX", "value": "okx"},
                        ],
                        value="binance",
                        inline=True,
                        className="mb-2",
                    ),
                ], width=12),
            ]),

            # Chart with loading
            dcc.Loading(
                id="depth-chart-loading",
                type="circle",
                children=[
                    dcc.Graph(
                        id="depth-chart",
                        className="chart-container",
                        config={
                            "displayModeBar": False,
                        },
                        figure=create_empty_depth_chart(),
                    ),
                ],
            ),

            # Imbalance indicator
            dbc.Row([
                dbc.Col([
                    html.Div(
                        id="depth-imbalance-container",
                        className="text-center mt-2",
                        children=[
                            html.Small("Imbalance: ", className="text-muted"),
                            html.Span(
                                id="depth-imbalance-value",
                                children="--",
                            ),
                            html.Small(
                                id="depth-imbalance-direction",
                                className="ms-2",
                                children="",
                            ),
                        ],
                    ),
                ], width=12),
            ]),
        ],
    )


def create_empty_depth_chart() -> go.Figure:
    """
    Create an empty depth chart with proper styling.

    Returns:
        go.Figure: Empty chart figure.
    """
    fig = go.Figure()

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=CHART_COLORS["background"],
        plot_bgcolor=CHART_COLORS["background"],
        margin=dict(l=50, r=30, t=30, b=50),
        height=250,
        xaxis=dict(
            title="Depth (USD)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
        ),
        yaxis=dict(
            title="Level",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
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


def format_usd_millions(value: float) -> str:
    """
    Format a USD value in millions.

    Args:
        value: Value in USD.

    Returns:
        str: Formatted string (e.g., "$1.2M").
    """
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def create_depth_chart(
    depth_5bps_bid: Optional[float] = None,
    depth_5bps_ask: Optional[float] = None,
    depth_10bps_bid: Optional[float] = None,
    depth_10bps_ask: Optional[float] = None,
    depth_25bps_bid: Optional[float] = None,
    depth_25bps_ask: Optional[float] = None,
) -> go.Figure:
    """
    Create the depth bar chart.

    Shows horizontal bars for bid and ask depth at each level.
    Bids extend left (green), asks extend right (red).

    Args:
        depth_5bps_bid: Bid depth within 5 bps of mid (USD).
        depth_5bps_ask: Ask depth within 5 bps of mid (USD).
        depth_10bps_bid: Bid depth within 10 bps of mid (USD).
        depth_10bps_ask: Ask depth within 10 bps of mid (USD).
        depth_25bps_bid: Bid depth within 25 bps of mid (USD).
        depth_25bps_ask: Ask depth within 25 bps of mid (USD).

    Returns:
        go.Figure: Configured depth chart.
    """
    # Check if we have any data
    all_values = [
        depth_5bps_bid, depth_5bps_ask,
        depth_10bps_bid, depth_10bps_ask,
        depth_25bps_bid, depth_25bps_ask,
    ]

    if all(v is None for v in all_values):
        return create_empty_depth_chart()

    # Prepare data - use 0 for None values
    bid_values = [
        depth_5bps_bid or 0,
        depth_10bps_bid or 0,
        depth_25bps_bid or 0,
    ]

    ask_values = [
        depth_5bps_ask or 0,
        depth_10bps_ask or 0,
        depth_25bps_ask or 0,
    ]

    levels = ["5 bps", "10 bps", "25 bps"]

    fig = go.Figure()

    # Add bid bars (extending left, shown as negative for visual effect)
    fig.add_trace(
        go.Bar(
            y=levels,
            x=[-v for v in bid_values],
            name="Bid",
            orientation="h",
            marker=dict(
                color=CHART_COLORS["bid"],
                line=dict(color=CHART_COLORS["bid"], width=1),
            ),
            text=[format_usd_millions(v) for v in bid_values],
            textposition="inside",
            textfont=dict(color="white", size=12),
            hovertemplate="Bid @ %{y}<br>%{text}<extra></extra>",
        )
    )

    # Add ask bars (extending right)
    fig.add_trace(
        go.Bar(
            y=levels,
            x=ask_values,
            name="Ask",
            orientation="h",
            marker=dict(
                color=CHART_COLORS["ask"],
                line=dict(color=CHART_COLORS["ask"], width=1),
            ),
            text=[format_usd_millions(v) for v in ask_values],
            textposition="inside",
            textfont=dict(color="white", size=12),
            hovertemplate="Ask @ %{y}<br>%{text}<extra></extra>",
        )
    )

    # Calculate max for symmetric axis
    max_val = max(max(bid_values), max(ask_values)) * 1.1

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=CHART_COLORS["background"],
        plot_bgcolor=CHART_COLORS["background"],
        margin=dict(l=60, r=30, t=10, b=30),
        height=200,
        barmode="overlay",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        xaxis=dict(
            title="Depth (USD)",
            gridcolor=CHART_COLORS["grid"],
            showgrid=True,
            zeroline=True,
            zerolinecolor="#6c757d",
            range=[-max_val, max_val],
            tickformat=",.0f",
            tickvals=[-max_val * 0.75, -max_val * 0.5, -max_val * 0.25, 0, max_val * 0.25, max_val * 0.5, max_val * 0.75],
            ticktext=[
                format_usd_millions(max_val * 0.75),
                format_usd_millions(max_val * 0.5),
                format_usd_millions(max_val * 0.25),
                "0",
                format_usd_millions(max_val * 0.25),
                format_usd_millions(max_val * 0.5),
                format_usd_millions(max_val * 0.75),
            ],
        ),
        yaxis=dict(
            title="",
            gridcolor=CHART_COLORS["grid"],
            showgrid=False,
        ),
    )

    # Add center labels
    fig.add_annotation(
        x=-max_val * 0.5,
        y=1.15,
        yref="paper",
        text="BID",
        showarrow=False,
        font=dict(color=CHART_COLORS["bid"], size=12, weight="bold"),
    )

    fig.add_annotation(
        x=max_val * 0.5,
        y=1.15,
        yref="paper",
        text="ASK",
        showarrow=False,
        font=dict(color=CHART_COLORS["ask"], size=12, weight="bold"),
    )

    return fig


def render_imbalance_indicator(imbalance: Optional[float]) -> tuple:
    """
    Render the imbalance indicator value and direction.

    Args:
        imbalance: Imbalance ratio in range [-1, 1].

    Returns:
        tuple: (value_text, direction_component) for display.
    """
    if imbalance is None:
        return "--", ""

    # Format value
    value_text = f"{imbalance:+.2f}"

    # Determine direction and color
    if imbalance > 0.1:
        direction = html.Span(
            [html.I(className="fas fa-arrow-up me-1"), "Bid Heavy"],
            className="text-success",
        )
    elif imbalance < -0.1:
        direction = html.Span(
            [html.I(className="fas fa-arrow-down me-1"), "Ask Heavy"],
            className="text-danger",
        )
    else:
        direction = html.Span(
            "Balanced",
            className="text-muted",
        )

    # Color the value
    if imbalance > 0.1:
        color_class = "text-success"
    elif imbalance < -0.1:
        color_class = "text-danger"
    else:
        color_class = "text-warning"

    value_component = html.Span(value_text, className=color_class)

    return value_component, direction
