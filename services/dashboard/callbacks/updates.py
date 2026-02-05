"""
Dashboard callback functions for auto-refresh updates.

This module defines all Dash callbacks for updating dashboard components:
- Current state: every 1 second (Redis)
- Alerts: every 1 second (Redis)
- Charts: every 5 seconds (PostgreSQL)
- Health: every 1 second (Redis)

Note:
    All data is READ from storage - callbacks never calculate metrics.
    This module is owned by the VIZ agent.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from dash import Dash, html, callback_context, no_update
from dash.dependencies import Input, Output, State

import structlog

from services.dashboard.components.state_card import (
    render_metric_update,
    render_zscore_indicator,
)
from services.dashboard.components.alert_list import (
    render_alerts_list,
    get_priority_counts,
)
from services.dashboard.components.spread_chart import create_spread_chart
from services.dashboard.components.basis_chart import create_basis_chart
from services.dashboard.components.depth_heatmap import (
    create_depth_chart,
    render_imbalance_indicator,
)
from services.dashboard.components.cross_exchange import render_cross_exchange_update
from services.dashboard.components.health_panel import (
    render_health_status,
    render_overall_status,
    render_database_status,
)
from services.dashboard.data.fetchers import (
    get_current_state,
    get_active_alerts,
    get_spread_history,
    get_basis_history,
    get_depth_current,
    get_health_status,
    get_cross_exchange_data,
    get_zscore_warmup_status,
)

logger = structlog.get_logger(__name__)

# Default thresholds (should be loaded from config in production)
DEFAULT_THRESHOLDS = {
    "spread_warning": Decimal("3.0"),
    "spread_critical": Decimal("5.0"),
    "basis_warning": Decimal("10.0"),
    "basis_critical": Decimal("20.0"),
    "depth_warning": Decimal("500000"),
    "depth_critical": Decimal("200000"),
    "divergence_threshold": 5.0,
}


def register_callbacks(app: Dash) -> None:
    """
    Register all dashboard callbacks with the Dash app.

    Args:
        app: Dash application instance.
    """

    # =========================================================================
    # EXCHANGE AND TIME RANGE SELECTION CALLBACKS
    # =========================================================================

    @app.callback(
        [
            Output("selected-exchange", "data"),
            Output("btn-exchange-binance", "color"),
            Output("btn-exchange-binance", "outline"),
            Output("btn-exchange-okx", "color"),
            Output("btn-exchange-okx", "outline"),
            Output("btn-exchange-all", "color"),
            Output("btn-exchange-all", "outline"),
        ],
        [
            Input("btn-exchange-binance", "n_clicks"),
            Input("btn-exchange-okx", "n_clicks"),
            Input("btn-exchange-all", "n_clicks"),
        ],
        [State("selected-exchange", "data")],
        prevent_initial_call=True,
    )
    def update_exchange_selection(
        binance_clicks: Optional[int],
        okx_clicks: Optional[int],
        all_clicks: Optional[int],
        current_exchange: str,
    ) -> Tuple:
        """Handle exchange filter button clicks."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update

        button_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if button_id == "btn-exchange-binance":
            return "binance", "primary", False, "secondary", True, "secondary", True
        elif button_id == "btn-exchange-okx":
            return "okx", "secondary", True, "primary", False, "secondary", True
        else:  # all
            return "all", "secondary", True, "secondary", True, "primary", False

    @app.callback(
        [
            Output("selected-time-range", "data"),
            Output("btn-range-5m", "color"),
            Output("btn-range-5m", "outline"),
            Output("btn-range-15m", "color"),
            Output("btn-range-15m", "outline"),
            Output("btn-range-1h", "color"),
            Output("btn-range-1h", "outline"),
            Output("btn-range-4h", "color"),
            Output("btn-range-4h", "outline"),
            Output("btn-range-24h", "color"),
            Output("btn-range-24h", "outline"),
        ],
        [
            Input("btn-range-5m", "n_clicks"),
            Input("btn-range-15m", "n_clicks"),
            Input("btn-range-1h", "n_clicks"),
            Input("btn-range-4h", "n_clicks"),
            Input("btn-range-24h", "n_clicks"),
        ],
        [State("selected-time-range", "data")],
        prevent_initial_call=True,
    )
    def update_time_range_selection(
        clicks_5m: Optional[int],
        clicks_15m: Optional[int],
        clicks_1h: Optional[int],
        clicks_4h: Optional[int],
        clicks_24h: Optional[int],
        current_range: str,
    ) -> Tuple:
        """Handle time range button clicks."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update

        button_id = ctx.triggered[0]["prop_id"].split(".")[0]

        # Default: all buttons secondary/outline except selected
        result = {
            "5m": ("secondary", True),
            "15m": ("secondary", True),
            "1h": ("secondary", True),
            "4h": ("secondary", True),
            "24h": ("secondary", True),
        }

        # Determine selected range
        range_map = {
            "btn-range-5m": "5m",
            "btn-range-15m": "15m",
            "btn-range-1h": "1h",
            "btn-range-4h": "4h",
            "btn-range-24h": "24h",
        }

        selected = range_map.get(button_id, "1h")
        result[selected] = ("primary", False)

        return (
            selected,
            result["5m"][0], result["5m"][1],
            result["15m"][0], result["15m"][1],
            result["1h"][0], result["1h"][1],
            result["4h"][0], result["4h"][1],
            result["24h"][0], result["24h"][1],
        )

    # =========================================================================
    # TIMESTAMP UPDATE CALLBACK
    # =========================================================================

    @app.callback(
        Output("last-update-timestamp", "children"),
        Input("interval-1s", "n_intervals"),
    )
    def update_timestamp(n_intervals: int) -> str:
        """Update the last update timestamp display."""
        now = datetime.utcnow()
        return f"Last update: {now.strftime('%H:%M:%S')} UTC"

    # =========================================================================
    # CURRENT STATE CALLBACK (1 second)
    # =========================================================================

    @app.callback(
        [
            Output("metric-value-spread", "children"),
            Output("metric-status-spread", "children"),
            Output("metric-zscore-spread", "children"),
            Output("metric-value-depth", "children"),
            Output("metric-status-depth", "children"),
            Output("metric-value-basis", "children"),
            Output("metric-status-basis", "children"),
            Output("metric-zscore-basis", "children"),
            Output("metric-value-imbalance", "children"),
            Output("metric-status-imbalance", "children"),
            Output("current-exchange-display", "children"),
        ],
        [Input("interval-1s", "n_intervals")],
        [
            State("selected-exchange", "data"),
            State("selected-instrument", "data"),
        ],
    )
    def update_current_state(
        n_intervals: int,
        exchange: str,
        instrument: str,
    ) -> Tuple:
        """Update current state metrics from Redis (every 1 second)."""
        # Get Redis client from Flask config
        from flask import current_app
        redis_client = current_app.config.get("redis_client")

        # Default outputs for unavailable state
        unavailable_icon = html.I(className="fas fa-circle status-unavailable")
        unavailable_zscore = html.Span("Z-Score: --", className="text-muted")

        try:
            # This would be async in production - using sync wrapper for Dash
            import asyncio

            # Create event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Fetch current state
            if redis_client is not None:
                state = loop.run_until_complete(
                    get_current_state(redis_client, exchange, instrument)
                )
            else:
                state = None

            if state is None:
                return (
                    "--", unavailable_icon, unavailable_zscore,  # Spread
                    "--", unavailable_icon,  # Depth
                    "--", unavailable_icon, unavailable_zscore,  # Basis
                    "--", unavailable_icon,  # Imbalance
                    f"{exchange.capitalize() if exchange != 'all' else 'All Exchanges'}",
                )

            # Spread
            spread_value, spread_status = render_metric_update(
                state.get("spread_bps"),
                precision=2,
                warning_threshold=DEFAULT_THRESHOLDS["spread_warning"],
                critical_threshold=DEFAULT_THRESHOLDS["spread_critical"],
            )

            # Get spread z-score warmup status
            if redis_client:
                spread_warmup = loop.run_until_complete(
                    get_zscore_warmup_status(
                        redis_client, exchange if exchange != "all" else "binance",
                        instrument, "spread_bps"
                    )
                )
                spread_zscore = render_zscore_indicator(
                    spread_warmup.get("zscore"),
                    spread_warmup.get("sample_count", 0),
                    spread_warmup.get("min_samples", 30),
                )
            else:
                spread_zscore = unavailable_zscore

            # Depth
            depth_total = state.get("depth_10bps_total")
            if depth_total is not None:
                # Format in millions
                depth_display = f"${float(depth_total) / 1_000_000:.2f}M"
            else:
                depth_display = "--"

            depth_value, depth_status = render_metric_update(
                depth_total,
                precision=0,
                warning_threshold=DEFAULT_THRESHOLDS["depth_warning"],
                critical_threshold=DEFAULT_THRESHOLDS["depth_critical"],
                is_lower_worse=True,
            )

            # Basis (placeholder - would need basis calculation from metrics engine)
            basis_value = "--"
            basis_status = unavailable_icon
            basis_zscore = unavailable_zscore

            # Imbalance
            imbalance = state.get("imbalance")
            if imbalance is not None:
                imbalance_display = f"{float(imbalance):+.2f}"
                # Imbalance doesn't have traditional thresholds
                if abs(float(imbalance)) > 0.3:
                    imbalance_status = html.I(className="fas fa-circle status-warning")
                else:
                    imbalance_status = html.I(className="fas fa-circle status-normal")
            else:
                imbalance_display = "--"
                imbalance_status = unavailable_icon

            exchange_display = exchange.capitalize() if exchange != "all" else "All Exchanges"

            return (
                spread_value, spread_status, spread_zscore,
                depth_display, depth_status,
                basis_value, basis_status, basis_zscore,
                imbalance_display, imbalance_status,
                exchange_display,
            )

        except Exception as e:
            logger.error("update_current_state_error", error=str(e))
            return (
                "--", unavailable_icon, unavailable_zscore,
                "--", unavailable_icon,
                "--", unavailable_icon, unavailable_zscore,
                "--", unavailable_icon,
                "Error",
            )

    # =========================================================================
    # ALERTS CALLBACK (1 second)
    # =========================================================================

    @app.callback(
        [
            Output("alerts-list-container", "children"),
            Output("alerts-count-badge", "children"),
        ],
        [Input("interval-1s", "n_intervals")],
    )
    def update_alerts(n_intervals: int) -> Tuple[List, str]:
        """Update active alerts from Redis (every 1 second)."""
        from flask import current_app
        redis_client = current_app.config.get("redis_client")

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if redis_client is not None:
                alerts = loop.run_until_complete(get_active_alerts(redis_client))
            else:
                alerts = []

            alert_components = render_alerts_list(alerts)
            counts = get_priority_counts(alerts)

            return alert_components, str(counts["total"])

        except Exception as e:
            logger.error("update_alerts_error", error=str(e))
            from services.dashboard.components.alert_list import render_no_alerts_message
            return [render_no_alerts_message()], "0"

    # =========================================================================
    # SPREAD CHART CALLBACK (5 seconds)
    # =========================================================================

    @app.callback(
        Output("spread-chart", "figure"),
        [
            Input("interval-5s", "n_intervals"),
            Input("spread-chart-options", "value"),
        ],
        [
            State("selected-exchange", "data"),
            State("selected-time-range", "data"),
            State("selected-instrument", "data"),
        ],
    )
    def update_spread_chart(
        n_intervals: int,
        chart_options: List[str],
        exchange: str,
        time_range: str,
        instrument: str,
    ):
        """Update spread time series chart from PostgreSQL (every 5 seconds)."""
        from flask import current_app
        postgres_client = current_app.config.get("postgres_client")

        show_zscore = "show_zscore" in (chart_options or [])
        show_thresholds = "show_thresholds" in (chart_options or [])

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if postgres_client is not None:
                data = loop.run_until_complete(
                    get_spread_history(postgres_client, exchange, instrument, time_range)
                )
            else:
                data = {"binance": [], "okx": []}

            binance_data = data.get("binance", [])
            okx_data = data.get("okx", [])

            # Filter by exchange selection
            if exchange == "binance":
                okx_data = []
            elif exchange == "okx":
                binance_data = []

            fig = create_spread_chart(
                binance_data=binance_data,
                okx_data=okx_data,
                warning_threshold=float(DEFAULT_THRESHOLDS["spread_warning"]),
                critical_threshold=float(DEFAULT_THRESHOLDS["spread_critical"]),
                show_thresholds=show_thresholds,
                show_zscore=show_zscore,
            )

            return fig

        except Exception as e:
            logger.error("update_spread_chart_error", error=str(e))
            from services.dashboard.components.spread_chart import create_empty_spread_chart
            return create_empty_spread_chart()

    # =========================================================================
    # BASIS CHART CALLBACK (5 seconds)
    # =========================================================================

    @app.callback(
        Output("basis-chart", "figure"),
        [
            Input("interval-5s", "n_intervals"),
            Input("basis-chart-options", "value"),
        ],
        [
            State("selected-exchange", "data"),
            State("selected-time-range", "data"),
            State("selected-instrument", "data"),
        ],
    )
    def update_basis_chart(
        n_intervals: int,
        chart_options: List[str],
        exchange: str,
        time_range: str,
        instrument: str,
    ):
        """Update basis time series chart from PostgreSQL (every 5 seconds)."""
        from flask import current_app
        postgres_client = current_app.config.get("postgres_client")

        show_zscore = "show_zscore" in (chart_options or [])
        show_thresholds = "show_thresholds" in (chart_options or [])
        color_direction = "color_direction" in (chart_options or [])

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if postgres_client is not None:
                data = loop.run_until_complete(
                    get_basis_history(postgres_client, exchange, instrument, time_range)
                )
            else:
                data = {"binance": [], "okx": []}

            binance_data = data.get("binance", [])
            okx_data = data.get("okx", [])

            if exchange == "binance":
                okx_data = []
            elif exchange == "okx":
                binance_data = []

            fig = create_basis_chart(
                binance_data=binance_data,
                okx_data=okx_data,
                warning_threshold=float(DEFAULT_THRESHOLDS["basis_warning"]),
                critical_threshold=float(DEFAULT_THRESHOLDS["basis_critical"]),
                show_thresholds=show_thresholds,
                show_zscore=show_zscore,
                color_by_direction=color_direction,
            )

            return fig

        except Exception as e:
            logger.error("update_basis_chart_error", error=str(e))
            from services.dashboard.components.basis_chart import create_empty_basis_chart
            return create_empty_basis_chart()

    # =========================================================================
    # DEPTH CHART CALLBACK (1 second)
    # =========================================================================

    @app.callback(
        [
            Output("depth-chart", "figure"),
            Output("depth-imbalance-value", "children"),
            Output("depth-imbalance-direction", "children"),
        ],
        [
            Input("interval-1s", "n_intervals"),
            Input("depth-exchange-select", "value"),
        ],
        [State("selected-instrument", "data")],
    )
    def update_depth_chart(
        n_intervals: int,
        depth_exchange: str,
        instrument: str,
    ):
        """Update depth chart from Redis (every 1 second)."""
        from flask import current_app
        redis_client = current_app.config.get("redis_client")

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if redis_client is not None:
                depth_data = loop.run_until_complete(
                    get_depth_current(redis_client, depth_exchange, instrument)
                )
            else:
                depth_data = None

            if depth_data is None:
                from services.dashboard.components.depth_heatmap import create_empty_depth_chart
                return create_empty_depth_chart(), "--", ""

            fig = create_depth_chart(
                depth_5bps_bid=depth_data.get("depth_5bps_bid"),
                depth_5bps_ask=depth_data.get("depth_5bps_ask"),
                depth_10bps_bid=depth_data.get("depth_10bps_bid"),
                depth_10bps_ask=depth_data.get("depth_10bps_ask"),
                depth_25bps_bid=depth_data.get("depth_25bps_bid"),
                depth_25bps_ask=depth_data.get("depth_25bps_ask"),
            )

            imbalance_value, imbalance_direction = render_imbalance_indicator(
                depth_data.get("imbalance")
            )

            return fig, imbalance_value, imbalance_direction

        except Exception as e:
            logger.error("update_depth_chart_error", error=str(e))
            from services.dashboard.components.depth_heatmap import create_empty_depth_chart
            return create_empty_depth_chart(), "--", ""

    # =========================================================================
    # CROSS-EXCHANGE CALLBACK (1 second)
    # =========================================================================

    @app.callback(
        [
            Output("cross-exchange-binance-price", "children"),
            Output("cross-exchange-okx-price", "children"),
            Output("divergence-value", "children"),
            Output("divergence-value", "className"),
            Output("divergence-direction", "children"),
            Output("divergence-status-icon", "children"),
            Output("cross-exchange-binance-spread", "children"),
            Output("cross-exchange-okx-spread", "children"),
            Output("cross-exchange-price-diff", "children"),
        ],
        [Input("interval-1s", "n_intervals")],
        [State("selected-instrument", "data")],
    )
    def update_cross_exchange(
        n_intervals: int,
        instrument: str,
    ):
        """Update cross-exchange comparison from Redis (every 1 second)."""
        from flask import current_app
        redis_client = current_app.config.get("redis_client")

        default_values = ("$--", "$--", "-- bps", "", "", html.I(className="fas fa-question-circle text-muted"), "-- bps", "-- bps", "$--")

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if redis_client is not None:
                data = loop.run_until_complete(
                    get_cross_exchange_data(redis_client, instrument)
                )
            else:
                data = {}

            if not data:
                return default_values

            binance_data = data.get("binance") or {}
            okx_data = data.get("okx") or {}

            result = render_cross_exchange_update(
                binance_mid=binance_data.get("mid_price"),
                okx_mid=okx_data.get("mid_price"),
                binance_spread_bps=binance_data.get("spread_bps"),
                okx_spread_bps=okx_data.get("spread_bps"),
                divergence_threshold=DEFAULT_THRESHOLDS["divergence_threshold"],
            )

            return (
                result["binance_price"],
                result["okx_price"],
                result["divergence_value"],
                result["divergence_class"],
                result["divergence_direction"],
                result["divergence_status_icon"],
                result["binance_spread"],
                result["okx_spread"],
                result["price_diff"],
            )

        except Exception as e:
            logger.error("update_cross_exchange_error", error=str(e))
            return default_values

    # =========================================================================
    # HEALTH PANEL CALLBACK (1 second)
    # =========================================================================

    @app.callback(
        [
            Output("health-status-icon-binance", "children"),
            Output("health-status-text-binance", "children"),
            Output("health-lag-binance", "children"),
            Output("health-msgs-binance", "children"),
            Output("health-gaps-binance", "children"),
            Output("health-last-binance", "children"),
            Output("health-status-icon-okx", "children"),
            Output("health-status-text-okx", "children"),
            Output("health-lag-okx", "children"),
            Output("health-msgs-okx", "children"),
            Output("health-gaps-okx", "children"),
            Output("health-last-okx", "children"),
            Output("health-overall-status", "children"),
            Output("health-overall-status", "className"),
            Output("health-redis-status", "children"),
            Output("health-postgres-status", "children"),
        ],
        [Input("interval-1s", "n_intervals")],
    )
    def update_health_panel(n_intervals: int):
        """Update system health panel from Redis (every 1 second)."""
        from flask import current_app
        redis_client = current_app.config.get("redis_client")
        postgres_client = current_app.config.get("postgres_client")

        unavailable = html.I(className="fas fa-circle text-muted")
        default_text = "--"

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Check Redis connection
            redis_connected = False
            if redis_client is not None:
                try:
                    redis_connected = loop.run_until_complete(redis_client.ping())
                except Exception:
                    redis_connected = False

            # Check PostgreSQL connection
            postgres_connected = False
            if postgres_client is not None:
                try:
                    postgres_connected = loop.run_until_complete(postgres_client.ping())
                except Exception:
                    postgres_connected = False

            # Fetch health status
            if redis_client is not None and redis_connected:
                health_dict = loop.run_until_complete(get_health_status(redis_client))
            else:
                health_dict = {}

            # Render Binance health
            binance_health = health_dict.get("binance")
            if binance_health:
                binance_rendered = render_health_status(binance_health)
                binance_outputs = (
                    binance_rendered["status_icon"],
                    binance_rendered["status_text"],
                    binance_rendered["lag"],
                    binance_rendered["msgs"],
                    binance_rendered["gaps"],
                    binance_rendered["last"],
                )
            else:
                binance_outputs = (unavailable, default_text, default_text, default_text, default_text, default_text)

            # Render OKX health
            okx_health = health_dict.get("okx")
            if okx_health:
                okx_rendered = render_health_status(okx_health)
                okx_outputs = (
                    okx_rendered["status_icon"],
                    okx_rendered["status_text"],
                    okx_rendered["lag"],
                    okx_rendered["msgs"],
                    okx_rendered["gaps"],
                    okx_rendered["last"],
                )
            else:
                okx_outputs = (unavailable, default_text, default_text, default_text, default_text, default_text)

            # Overall status
            overall_text, overall_class = render_overall_status(health_dict)

            # Database status
            redis_status = render_database_status(redis_connected, "Redis")
            postgres_status = render_database_status(postgres_connected, "PostgreSQL")

            return (
                *binance_outputs,
                *okx_outputs,
                overall_text,
                f"badge {overall_class}",
                redis_status,
                postgres_status,
            )

        except Exception as e:
            logger.error("update_health_panel_error", error=str(e))
            return (
                unavailable, default_text, default_text, default_text, default_text, default_text,
                unavailable, default_text, default_text, default_text, default_text, default_text,
                "Error", "badge bg-danger",
                render_database_status(False, "Redis"),
                render_database_status(False, "PostgreSQL"),
            )

    logger.info("dashboard_callbacks_registered")
