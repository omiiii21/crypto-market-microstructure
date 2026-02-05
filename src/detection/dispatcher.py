"""
Channel dispatcher for routing alerts to notification channels.

This module provides the ChannelDispatcher class which routes alerts
to the appropriate notification channels based on priority configuration.

Key Features:
    - Routes alerts to multiple channels
    - Priority-based channel selection
    - Support for console and slack channels
    - Escalation and resolution notifications

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> dispatcher = ChannelDispatcher(
    ...     channels={"console": console_channel, "slack": slack_channel},
    ...     priority_channels={
    ...         AlertPriority.P1: ["console", "slack"],
    ...         AlertPriority.P2: ["console", "slack"],
    ...         AlertPriority.P3: ["console"],
    ...     },
    ... )
    >>> await dispatcher.dispatch(alert)
"""

from typing import Dict, List, Optional, Protocol

import structlog

from src.models.alerts import Alert, AlertPriority

logger = structlog.get_logger(__name__)


class AlertChannel(Protocol):
    """
    Protocol for alert notification channels.

    Any channel implementation must support these async methods.
    """

    async def dispatch(self, alert: Alert) -> None:
        """Dispatch an alert to the channel."""
        ...

    async def dispatch_escalation(self, alert: Alert) -> None:
        """Dispatch an escalation notification."""
        ...

    async def dispatch_resolution(self, alert: Alert) -> None:
        """Dispatch a resolution notification."""
        ...


# Default priority to channels mapping
DEFAULT_PRIORITY_CHANNELS: Dict[AlertPriority, List[str]] = {
    AlertPriority.P1: ["console", "slack"],
    AlertPriority.P2: ["console", "slack"],
    AlertPriority.P3: ["console"],
}


class ChannelDispatcher:
    """
    Routes alerts to appropriate notification channels.

    Determines which channels should receive each alert based on the
    alert's priority and dispatches to all applicable channels.

    Attributes:
        channels: Dict mapping channel name to channel instance.
        priority_channels: Dict mapping priority to list of channel names.

    Example:
        >>> dispatcher = ChannelDispatcher(
        ...     channels={"console": console_channel, "slack": slack_channel},
        ...     priority_channels=DEFAULT_PRIORITY_CHANNELS,
        ... )
        >>> await dispatcher.dispatch(alert)  # Routes based on priority
    """

    def __init__(
        self,
        channels: Dict[str, AlertChannel],
        priority_channels: Optional[Dict[AlertPriority, List[str]]] = None,
    ) -> None:
        """
        Initialize the channel dispatcher.

        Args:
            channels: Dict mapping channel name to channel instance.
            priority_channels: Dict mapping priority to list of channel names.
                             Defaults to DEFAULT_PRIORITY_CHANNELS.

        Example:
            >>> from src.detection.channels.console import ConsoleChannel
            >>> from src.detection.channels.slack import SlackChannel
            >>>
            >>> dispatcher = ChannelDispatcher(
            ...     channels={
            ...         "console": ConsoleChannel(),
            ...         "slack": SlackChannel(webhook_url="..."),
            ...     },
            ... )
        """
        self.channels = channels
        self.priority_channels = priority_channels or DEFAULT_PRIORITY_CHANNELS

        logger.info(
            "channel_dispatcher_initialized",
            available_channels=list(channels.keys()),
            priority_config={p.value: ch for p, ch in self.priority_channels.items()},
        )

    async def dispatch(
        self,
        alert: Alert,
        channels: Optional[List[str]] = None,
    ) -> int:
        """
        Dispatch an alert to appropriate channels.

        If channels is specified, dispatches to those channels.
        Otherwise, dispatches based on alert priority.

        Args:
            alert: The Alert to dispatch.
            channels: Optional explicit list of channel names to use.

        Returns:
            int: Number of channels the alert was dispatched to.

        Example:
            >>> count = await dispatcher.dispatch(alert)
            >>> print(f"Dispatched to {count} channels")
        """
        # Determine target channels
        if channels is None:
            channels = self.priority_channels.get(alert.priority, ["console"])

        dispatched_count = 0

        for channel_name in channels:
            channel = self.channels.get(channel_name)
            if channel is None:
                logger.warning(
                    "channel_not_found",
                    channel_name=channel_name,
                    alert_id=alert.alert_id,
                )
                continue

            try:
                await channel.dispatch(alert)
                dispatched_count += 1

                logger.debug(
                    "alert_dispatched_to_channel",
                    channel=channel_name,
                    alert_id=alert.alert_id,
                    priority=alert.priority.value,
                )

            except Exception as e:
                logger.error(
                    "channel_dispatch_failed",
                    channel=channel_name,
                    alert_id=alert.alert_id,
                    error=str(e),
                )

        logger.info(
            "alert_dispatch_complete",
            alert_id=alert.alert_id,
            dispatched_to=dispatched_count,
            total_channels=len(channels),
        )

        return dispatched_count

    async def dispatch_escalation(
        self,
        alert: Alert,
        channels: Optional[List[str]] = None,
    ) -> int:
        """
        Dispatch an escalation notification.

        Args:
            alert: The escalated Alert.
            channels: Optional explicit list of channel names.

        Returns:
            int: Number of channels notified.

        Example:
            >>> await dispatcher.dispatch_escalation(escalated_alert)
        """
        # Use P1 channels for escalations (escalation means it's now critical)
        if channels is None:
            channels = self.priority_channels.get(AlertPriority.P1, ["console"])

        dispatched_count = 0

        for channel_name in channels:
            channel = self.channels.get(channel_name)
            if channel is None:
                continue

            try:
                await channel.dispatch_escalation(alert)
                dispatched_count += 1

            except Exception as e:
                logger.error(
                    "escalation_dispatch_failed",
                    channel=channel_name,
                    alert_id=alert.alert_id,
                    error=str(e),
                )

        logger.info(
            "escalation_dispatch_complete",
            alert_id=alert.alert_id,
            dispatched_to=dispatched_count,
        )

        return dispatched_count

    async def dispatch_resolution(
        self,
        alert: Alert,
        channels: Optional[List[str]] = None,
    ) -> int:
        """
        Dispatch a resolution notification.

        Args:
            alert: The resolved Alert.
            channels: Optional explicit list of channel names.

        Returns:
            int: Number of channels notified.

        Example:
            >>> await dispatcher.dispatch_resolution(resolved_alert)
        """
        # Use the same channels that were used for the original alert
        if channels is None:
            # Use original priority if available (before escalation)
            priority = alert.original_priority or alert.priority
            channels = self.priority_channels.get(priority, ["console"])

        dispatched_count = 0

        for channel_name in channels:
            channel = self.channels.get(channel_name)
            if channel is None:
                continue

            try:
                await channel.dispatch_resolution(alert)
                dispatched_count += 1

            except Exception as e:
                logger.error(
                    "resolution_dispatch_failed",
                    channel=channel_name,
                    alert_id=alert.alert_id,
                    error=str(e),
                )

        logger.info(
            "resolution_dispatch_complete",
            alert_id=alert.alert_id,
            dispatched_to=dispatched_count,
        )

        return dispatched_count

    def add_channel(self, name: str, channel: AlertChannel) -> None:
        """
        Add a new channel to the dispatcher.

        Args:
            name: Channel name.
            channel: Channel instance.

        Example:
            >>> dispatcher.add_channel("email", email_channel)
        """
        self.channels[name] = channel
        logger.info(
            "channel_added",
            channel_name=name,
        )

    def remove_channel(self, name: str) -> bool:
        """
        Remove a channel from the dispatcher.

        Args:
            name: Channel name to remove.

        Returns:
            bool: True if channel was removed, False if not found.

        Example:
            >>> dispatcher.remove_channel("slack")
        """
        if name in self.channels:
            del self.channels[name]
            logger.info(
                "channel_removed",
                channel_name=name,
            )
            return True
        return False

    def set_priority_channels(
        self,
        priority: AlertPriority,
        channels: List[str],
    ) -> None:
        """
        Set the channels for a specific priority.

        Args:
            priority: The priority level.
            channels: List of channel names.

        Example:
            >>> dispatcher.set_priority_channels(
            ...     AlertPriority.P1,
            ...     ["console", "slack", "pagerduty"],
            ... )
        """
        self.priority_channels[priority] = channels
        logger.info(
            "priority_channels_updated",
            priority=priority.value,
            channels=channels,
        )

    def get_available_channels(self) -> List[str]:
        """
        Get list of available channel names.

        Returns:
            List[str]: Available channel names.
        """
        return list(self.channels.keys())

    def get_channels_for_priority(self, priority: AlertPriority) -> List[str]:
        """
        Get channel names configured for a priority.

        Args:
            priority: The priority level.

        Returns:
            List[str]: Channel names for the priority.
        """
        return self.priority_channels.get(priority, [])


async def create_dispatcher(
    console_format: str = "structured",
    console_colors: bool = True,
    slack_webhook_url: Optional[str] = None,
    slack_channel: str = "#market-ops",
    slack_enabled: bool = True,
    priority_channels: Optional[Dict[AlertPriority, List[str]]] = None,
) -> ChannelDispatcher:
    """
    Factory function to create a ChannelDispatcher with default channels.

    Creates console and slack channels and configures the dispatcher.

    Args:
        console_format: Console output format ("structured" or "simple").
        console_colors: Whether to use ANSI colors in console output.
        slack_webhook_url: Slack webhook URL.
        slack_channel: Target Slack channel.
        slack_enabled: Whether Slack channel is enabled.
        priority_channels: Custom priority to channels mapping.

    Returns:
        ChannelDispatcher: Configured dispatcher instance.

    Example:
        >>> dispatcher = await create_dispatcher(
        ...     console_format="simple",
        ...     slack_webhook_url="https://hooks.slack.com/...",
        ... )
    """
    from src.detection.channels.console import (
        ConsoleChannel,
        OutputFormat,
    )
    from src.detection.channels.slack import SlackChannel

    # Create channels
    channels: Dict[str, AlertChannel] = {
        "console": ConsoleChannel(
            format=OutputFormat(console_format),
            use_colors=console_colors,
        ),
    }

    # Add Slack if configured
    if slack_enabled and slack_webhook_url:
        channels["slack"] = SlackChannel(
            webhook_url=slack_webhook_url,
            channel=slack_channel,
            enabled=slack_enabled,
        )

    return ChannelDispatcher(
        channels=channels,
        priority_channels=priority_channels,
    )
