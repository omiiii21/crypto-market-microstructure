"""
Main dashboard layout for the surveillance system.

This module defines the main layout structure with:
- Header with title, exchange filter, and time range selector
- Current state panel (metrics)
- Active alerts panel
- Time series charts (spread, basis)
- Depth heatmap and cross-exchange comparison
- System health panel

Note:
    All data is fetched via callbacks, not computed here.
"""

from dash import html, dcc
import dash_bootstrap_components as dbc

from services.dashboard.components.state_card import create_current_state_panel
from services.dashboard.components.alert_list import create_alerts_panel
from services.dashboard.components.spread_chart import create_spread_chart_container
from services.dashboard.components.basis_chart import create_basis_chart_container
from services.dashboard.components.depth_heatmap import create_depth_heatmap_container
from services.dashboard.components.cross_exchange import create_cross_exchange_panel
from services.dashboard.components.health_panel import create_health_panel


def create_header() -> html.Div:
    """
    Create the dashboard header with title and controls.

    Returns:
        html.Div: Header component with title, exchange filter, and time range.
    """
    return html.Div(
        className="dashboard-header",
        children=[
            dbc.Row([
                # Title
                dbc.Col([
                    html.H1("CRYPTO MARKET MICROSTRUCTURE", className="header-title"),
                ], width=4),

                # Exchange filter buttons
                dbc.Col([
                    dbc.ButtonGroup([
                        dbc.Button(
                            "Binance",
                            id="btn-exchange-binance",
                            className="exchange-filter-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                        ),
                        dbc.Button(
                            "OKX",
                            id="btn-exchange-okx",
                            className="exchange-filter-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                        ),
                        dbc.Button(
                            "Both",
                            id="btn-exchange-all",
                            className="exchange-filter-btn active",
                            color="primary",
                            size="sm",
                        ),
                    ]),
                ], width=3, className="text-center"),

                # Time range selector
                dbc.Col([
                    dbc.ButtonGroup([
                        dbc.Button("5m", id="btn-range-5m", size="sm", color="secondary", outline=True),
                        dbc.Button("15m", id="btn-range-15m", size="sm", color="secondary", outline=True),
                        dbc.Button("1h", id="btn-range-1h", size="sm", color="primary"),
                        dbc.Button("4h", id="btn-range-4h", size="sm", color="secondary", outline=True),
                        dbc.Button("24h", id="btn-range-24h", size="sm", color="secondary", outline=True),
                    ]),
                ], width=3, className="text-center"),

                # Last update timestamp
                dbc.Col([
                    html.Span(
                        id="last-update-timestamp",
                        className="last-update",
                        children="Last update: --:--:--",
                    ),
                ], width=2, className="text-end"),
            ]),
        ],
    )


def create_main_layout() -> html.Div:
    """
    Create the main dashboard layout with all panels.

    The layout is organized as:
    - Row 1: Current State (left), Active Alerts (right)
    - Row 2: Spread Time Series (full width)
    - Row 3: Basis Time Series (full width)
    - Row 4: Depth Heatmap (left), Cross-Exchange Comparison (right)
    - Row 5: System Health (full width)

    Returns:
        html.Div: Complete main layout component.
    """
    return html.Div([
        # Header
        create_header(),

        # Main content container
        dbc.Container(
            fluid=True,
            className="px-4",
            children=[
                # Row 1: Current State and Active Alerts
                dbc.Row([
                    dbc.Col([
                        create_current_state_panel(),
                    ], lg=6, md=12),
                    dbc.Col([
                        create_alerts_panel(),
                    ], lg=6, md=12),
                ], className="mb-3"),

                # Row 2: Spread Time Series
                dbc.Row([
                    dbc.Col([
                        create_spread_chart_container(),
                    ], width=12),
                ], className="mb-3"),

                # Row 3: Basis Time Series
                dbc.Row([
                    dbc.Col([
                        create_basis_chart_container(),
                    ], width=12),
                ], className="mb-3"),

                # Row 4: Depth Heatmap and Cross-Exchange Comparison
                dbc.Row([
                    dbc.Col([
                        create_depth_heatmap_container(),
                    ], lg=6, md=12),
                    dbc.Col([
                        create_cross_exchange_panel(),
                    ], lg=6, md=12),
                ], className="mb-3"),

                # Row 5: System Health
                dbc.Row([
                    dbc.Col([
                        create_health_panel(),
                    ], width=12),
                ], className="mb-3"),
            ],
        ),
    ])
