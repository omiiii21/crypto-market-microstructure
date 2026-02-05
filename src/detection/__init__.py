"""
Anomaly detection for the surveillance system.

This module contains alert evaluation, persistence tracking, and
alert lifecycle management.

Components:
    evaluator: AlertEvaluator for dual-condition evaluation
    persistence: PersistenceTracker for time-based conditions
    manager: AlertManager for alert lifecycle
    storage: AlertStorage for Redis and PostgreSQL persistence
    dispatcher: ChannelDispatcher for notification routing
    channels/: Alert notification channels (console, slack)

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> from src.detection import (
    ...     AlertEvaluator,
    ...     PersistenceTracker,
    ...     AlertManager,
    ...     AlertStorage,
    ...     ChannelDispatcher,
    ... )
    >>>
    >>> # Create components
    >>> evaluator = AlertEvaluator()
    >>> tracker = PersistenceTracker()
    >>> storage = AlertStorage(redis_client, postgres_client)
    >>> manager = AlertManager(
    ...     storage=storage,
    ...     evaluator=evaluator,
    ...     persistence_tracker=tracker,
    ...     alert_definitions=definitions,
    ...     thresholds=thresholds,
    ... )
"""

from src.detection.evaluator import AlertEvaluator, create_evaluator
from src.detection.persistence import (
    PersistenceTracker,
    create_persistence_tracker,
    build_condition_key,
)
from src.detection.storage import AlertStorage, create_alert_storage
from src.detection.manager import (
    AlertManager,
    create_alert_manager,
    DEFAULT_THROTTLE_SECONDS,
    DEFAULT_ESCALATION_SECONDS,
)
from src.detection.dispatcher import (
    ChannelDispatcher,
    create_dispatcher,
    DEFAULT_PRIORITY_CHANNELS,
)

__all__ = [
    # Evaluator
    "AlertEvaluator",
    "create_evaluator",
    # Persistence
    "PersistenceTracker",
    "create_persistence_tracker",
    "build_condition_key",
    # Storage
    "AlertStorage",
    "create_alert_storage",
    # Manager
    "AlertManager",
    "create_alert_manager",
    "DEFAULT_THROTTLE_SECONDS",
    "DEFAULT_ESCALATION_SECONDS",
    # Dispatcher
    "ChannelDispatcher",
    "create_dispatcher",
    "DEFAULT_PRIORITY_CHANNELS",
]
