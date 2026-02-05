"""
Reusable dashboard components.

Contains cards, charts, and other UI components for the surveillance dashboard.

Components:
    - state_card: Current state metrics panel with warmup indicators
    - alert_list: Active alerts panel
    - spread_chart: Spread time series chart
    - basis_chart: Basis time series chart
    - depth_heatmap: Depth visualization
    - cross_exchange: Cross-exchange comparison panel
    - health_panel: System health panel

"""

from services.dashboard.components.state_card import (
    create_current_state_panel,
    create_metric_card,
    render_zscore_indicator,
)
from services.dashboard.components.alert_list import (
    create_alerts_panel,
    create_alert_card,
    render_alerts_list,
)
from services.dashboard.components.spread_chart import (
    create_spread_chart_container,
    create_spread_chart,
)
from services.dashboard.components.basis_chart import (
    create_basis_chart_container,
    create_basis_chart,
)
from services.dashboard.components.depth_heatmap import (
    create_depth_heatmap_container,
    create_depth_chart,
)
from services.dashboard.components.cross_exchange import create_cross_exchange_panel
from services.dashboard.components.health_panel import create_health_panel

__all__ = [
    "create_current_state_panel",
    "create_metric_card",
    "render_zscore_indicator",
    "create_alerts_panel",
    "create_alert_card",
    "render_alerts_list",
    "create_spread_chart_container",
    "create_spread_chart",
    "create_basis_chart_container",
    "create_basis_chart",
    "create_depth_heatmap_container",
    "create_depth_chart",
    "create_cross_exchange_panel",
    "create_health_panel",
]
