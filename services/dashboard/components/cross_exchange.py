"""
Cross-exchange comparison component for the surveillance dashboard.

This module provides side-by-side comparison of:
- Binance vs OKX mid prices
- Price divergence in bps
- Visual indicator when divergence exceeds threshold

Note:
    All data is READ from Redis - never calculated here.
"""

from decimal import Decimal
from typing import Optional

from dash import html
import dash_bootstrap_components as dbc


def create_cross_exchange_panel() -> html.Div:
    """
    Create the cross-exchange comparison panel.

    Displays:
    - Side-by-side mid prices for Binance and OKX
    - Price divergence in bps
    - Visual alert when divergence is significant

    Returns:
        html.Div: Cross-exchange comparison panel.
    """
    return html.Div(
        className="panel-container",
        children=[
            html.Div("Cross-Exchange Comparison", className="panel-title"),

            # Exchange prices side by side
            dbc.Row([
                # Binance
                dbc.Col([
                    html.Div(
                        className="text-center p-3",
                        style={"backgroundColor": "#303030", "borderRadius": "8px"},
                        children=[
                            html.Div(
                                className="mb-2",
                                children=[
                                    html.I(className="fas fa-exchange-alt me-2"),
                                    html.Span("Binance", style={"color": "#F0B90B"}),
                                ],
                            ),
                            html.Div(
                                id="cross-exchange-binance-price",
                                className="h4 mb-0",
                                children="$--",
                            ),
                            html.Small(
                                id="cross-exchange-binance-time",
                                className="text-muted",
                                children="--:--:--",
                            ),
                        ],
                    ),
                ], width=5),

                # Divergence indicator
                dbc.Col([
                    html.Div(
                        className="text-center p-3",
                        children=[
                            html.Div(
                                id="cross-exchange-divergence",
                                className="h3 mb-0",
                                children=[
                                    html.Span("-- bps", id="divergence-value"),
                                ],
                            ),
                            html.Small(
                                id="divergence-direction",
                                className="text-muted",
                                children="",
                            ),
                            html.Div(
                                id="divergence-status-icon",
                                className="mt-2",
                                children=html.I(
                                    className="fas fa-check-circle fa-lg text-success",
                                ),
                            ),
                        ],
                    ),
                ], width=2, className="d-flex align-items-center justify-content-center"),

                # OKX
                dbc.Col([
                    html.Div(
                        className="text-center p-3",
                        style={"backgroundColor": "#303030", "borderRadius": "8px"},
                        children=[
                            html.Div(
                                className="mb-2",
                                children=[
                                    html.I(className="fas fa-exchange-alt me-2"),
                                    html.Span("OKX", style={"color": "#00C853"}),
                                ],
                            ),
                            html.Div(
                                id="cross-exchange-okx-price",
                                className="h4 mb-0",
                                children="$--",
                            ),
                            html.Small(
                                id="cross-exchange-okx-time",
                                className="text-muted",
                                children="--:--:--",
                            ),
                        ],
                    ),
                ], width=5),
            ]),

            # Additional metrics
            dbc.Row([
                dbc.Col([
                    html.Div(
                        className="mt-3 p-2 text-center",
                        style={"backgroundColor": "#252525", "borderRadius": "4px"},
                        children=[
                            dbc.Row([
                                dbc.Col([
                                    html.Small("Binance Spread", className="text-muted d-block"),
                                    html.Span(id="cross-exchange-binance-spread", children="-- bps"),
                                ], width=4),
                                dbc.Col([
                                    html.Small("Price Diff", className="text-muted d-block"),
                                    html.Span(id="cross-exchange-price-diff", children="$--"),
                                ], width=4),
                                dbc.Col([
                                    html.Small("OKX Spread", className="text-muted d-block"),
                                    html.Span(id="cross-exchange-okx-spread", children="-- bps"),
                                ], width=4),
                            ]),
                        ],
                    ),
                ], width=12),
            ]),

            # Arbitrage opportunity alert
            html.Div(
                id="arbitrage-alert-container",
                className="mt-3",
                children=[],
            ),
        ],
    )


def format_price(price: Optional[Decimal], precision: int = 2) -> str:
    """
    Format a price for display.

    Args:
        price: Price value in Decimal.
        precision: Decimal places to show.

    Returns:
        str: Formatted price string.
    """
    if price is None:
        return "$--"

    return f"${float(price):,.{precision}f}"


def format_divergence(divergence_bps: Optional[Decimal]) -> tuple:
    """
    Format divergence value with appropriate styling.

    Args:
        divergence_bps: Price divergence in basis points.

    Returns:
        tuple: (formatted_value, css_class, direction_text)
    """
    if divergence_bps is None:
        return "-- bps", "text-muted", ""

    abs_divergence = abs(float(divergence_bps))
    sign = "+" if divergence_bps > 0 else ""

    # Determine color class based on magnitude
    if abs_divergence > 5:
        css_class = "price-divergence-critical"
    elif abs_divergence > 3:
        css_class = "price-divergence-warning"
    else:
        css_class = "price-divergence-normal"

    formatted = f"{sign}{float(divergence_bps):.2f} bps"

    # Direction text
    if divergence_bps > 0:
        direction = "Binance higher"
    elif divergence_bps < 0:
        direction = "OKX higher"
    else:
        direction = "Aligned"

    return formatted, css_class, direction


def render_divergence_status_icon(divergence_bps: Optional[Decimal], threshold: float = 5.0) -> html.I:
    """
    Render the status icon based on divergence.

    Args:
        divergence_bps: Price divergence in basis points.
        threshold: Threshold for warning.

    Returns:
        html.I: Font Awesome icon element.
    """
    if divergence_bps is None:
        return html.I(className="fas fa-question-circle fa-lg text-muted")

    abs_divergence = abs(float(divergence_bps))

    if abs_divergence > threshold:
        return html.I(className="fas fa-exclamation-triangle fa-lg text-danger")
    elif abs_divergence > threshold / 2:
        return html.I(className="fas fa-exclamation-circle fa-lg text-warning")
    else:
        return html.I(className="fas fa-check-circle fa-lg text-success")


def render_arbitrage_alert(
    has_opportunity: bool,
    cross_spread: Optional[Decimal] = None,
) -> list:
    """
    Render arbitrage opportunity alert if conditions are met.

    Args:
        has_opportunity: Whether an arbitrage opportunity exists.
        cross_spread: The cross-exchange spread value.

    Returns:
        list: Alert components (or empty list).
    """
    if not has_opportunity:
        return []

    spread_text = ""
    if cross_spread is not None:
        spread_text = f" (${float(cross_spread):,.2f})"

    return [
        dbc.Alert(
            [
                html.I(className="fas fa-bolt me-2"),
                html.Strong("Potential Arbitrage Opportunity"),
                html.Span(spread_text, className="ms-1"),
            ],
            color="warning",
            className="mb-0",
        ),
    ]


def render_cross_exchange_update(
    binance_mid: Optional[Decimal],
    okx_mid: Optional[Decimal],
    binance_spread_bps: Optional[Decimal],
    okx_spread_bps: Optional[Decimal],
    divergence_threshold: float = 5.0,
) -> dict:
    """
    Prepare all cross-exchange display values for callback output.

    Args:
        binance_mid: Binance mid price.
        okx_mid: OKX mid price.
        binance_spread_bps: Binance spread in bps.
        okx_spread_bps: OKX spread in bps.
        divergence_threshold: Threshold for divergence warning.

    Returns:
        dict: Dictionary of all display values for callbacks.
    """
    # Calculate divergence
    divergence_bps = None
    price_diff = None

    if binance_mid is not None and okx_mid is not None:
        price_diff = binance_mid - okx_mid
        avg_price = (binance_mid + okx_mid) / 2
        if avg_price > 0:
            divergence_bps = (price_diff / avg_price) * Decimal("10000")

    # Format all values
    divergence_text, divergence_class, direction = format_divergence(divergence_bps)

    return {
        "binance_price": format_price(binance_mid),
        "okx_price": format_price(okx_mid),
        "divergence_value": divergence_text,
        "divergence_class": divergence_class,
        "divergence_direction": direction,
        "divergence_status_icon": render_divergence_status_icon(divergence_bps, divergence_threshold),
        "binance_spread": f"{float(binance_spread_bps):.2f} bps" if binance_spread_bps else "-- bps",
        "okx_spread": f"{float(okx_spread_bps):.2f} bps" if okx_spread_bps else "-- bps",
        "price_diff": f"${float(price_diff):,.2f}" if price_diff is not None else "$--",
    }
