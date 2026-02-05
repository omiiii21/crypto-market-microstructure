"""
Dashboard callbacks module.

This module contains all Dash callback functions for the surveillance dashboard.
"""

from services.dashboard.callbacks.updates import register_callbacks

__all__ = ["register_callbacks"]
