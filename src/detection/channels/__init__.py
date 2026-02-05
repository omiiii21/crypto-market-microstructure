"""
Alert notification channels.

This module contains implementations for different alert delivery
mechanisms including console logging and Slack notifications.

Components:
    console: Console/log output for alerts
    slack: Slack webhook integration (mock for development)

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> from src.detection.channels import ConsoleChannel, SlackChannel
    >>>
    >>> console = ConsoleChannel(format=OutputFormat.SIMPLE)
    >>> slack = SlackChannel(webhook_url="https://hooks.slack.com/...")
    >>>
    >>> await console.dispatch(alert)
    >>> await slack.dispatch(alert)
"""

from src.detection.channels.console import (
    ConsoleChannel,
    OutputFormat,
    AnsiColors,
    create_console_channel,
)
from src.detection.channels.slack import (
    SlackChannel,
    create_slack_channel,
)

__all__ = [
    # Console
    "ConsoleChannel",
    "OutputFormat",
    "AnsiColors",
    "create_console_channel",
    # Slack
    "SlackChannel",
    "create_slack_channel",
]
