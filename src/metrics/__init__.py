"""
Metric calculators for the surveillance system.

This module contains all metric calculation logic including spread,
depth, basis, and z-score computations.

Components:
    spread: SpreadCalculator for bid-ask spread metrics
    depth: DepthCalculator for order book depth metrics
    basis: BasisCalculator for perpetual-spot basis
    zscore: ZScoreCalculator with warmup guards
    aggregator: MetricsAggregator combining all calculators

Note:
    This module is owned by the QUANT agent.
"""

from src.metrics.aggregator import MetricsAggregator
from src.metrics.basis import BasisCalculator
from src.metrics.depth import DepthCalculator
from src.metrics.spread import SpreadCalculator
from src.metrics.zscore import ZScoreCalculator, ZScoreStatus

__all__: list[str] = [
    "SpreadCalculator",
    "DepthCalculator",
    "BasisCalculator",
    "ZScoreCalculator",
    "ZScoreStatus",
    "MetricsAggregator",
]
