"""
Alert storage for Redis and PostgreSQL.

This module provides the AlertStorage class which handles alert persistence
to both Redis (for active alerts) and PostgreSQL (for historical records).

Key Features:
    - Dual storage: Redis for real-time, PostgreSQL for history
    - Active alert management
    - Resolution and escalation updates
    - Query methods for escalation checks

Note:
    This module is owned by the ANOMALY-DETECTOR agent.

Example:
    >>> storage = AlertStorage(redis_client, postgres_client)
    >>> await storage.save(alert)
    >>> active = await storage.get_active_alerts()
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import structlog

from src.models.alerts import Alert, AlertPriority
from src.storage.redis_client import RedisClient
from src.storage.postgres_client import PostgresClient

logger = structlog.get_logger(__name__)


class AlertStorage:
    """
    Dual storage for alerts: Redis for active, PostgreSQL for history.

    This class manages alert persistence across two storage systems:
    - Redis: Fast lookup for active alerts, supports real-time queries
    - PostgreSQL: Historical record for audit trail and analysis

    Attributes:
        redis_client: Redis client for active alert storage.
        postgres_client: PostgreSQL client for historical storage.

    Example:
        >>> storage = AlertStorage(redis_client, postgres_client)
        >>> await storage.save(alert)  # Saves to both Redis and PostgreSQL
        >>> await storage.update_resolution(
        ...     alert_id=alert.alert_id,
        ...     resolved_at=datetime.utcnow(),
        ...     resolution_type="auto",
        ... )
    """

    def __init__(
        self,
        redis_client: RedisClient,
        postgres_client: PostgresClient,
    ) -> None:
        """
        Initialize the alert storage.

        Args:
            redis_client: Connected Redis client for active alerts.
            postgres_client: Connected PostgreSQL client for history.

        Example:
            >>> storage = AlertStorage(redis_client, postgres_client)
        """
        self.redis_client = redis_client
        self.postgres_client = postgres_client

        logger.debug("alert_storage_initialized")

    async def save(self, alert: Alert) -> None:
        """
        Save an alert to both Redis and PostgreSQL.

        For new alerts (is_active=True), stores in both systems.
        Redis is updated with current state; PostgreSQL inserts/upserts.

        Args:
            alert: The Alert to save.

        Raises:
            Exception: If storage operation fails.

        Example:
            >>> await storage.save(alert)
        """
        try:
            # Save to Redis for real-time access
            await self.redis_client.set_alert(alert)

            # Save to PostgreSQL for history
            await self.postgres_client.insert_alert(alert)

            logger.info(
                "alert_saved",
                alert_id=alert.alert_id,
                alert_type=alert.alert_type,
                priority=alert.priority.value,
                is_active=alert.is_active,
            )

        except Exception as e:
            logger.error(
                "alert_save_failed",
                alert_id=alert.alert_id,
                error=str(e),
            )
            raise

    async def update_resolution(
        self,
        alert_id: str,
        resolved_at: datetime,
        resolution_type: str,
        resolution_value: Optional[Decimal] = None,
    ) -> Optional[Alert]:
        """
        Update an alert with resolution information.

        Updates both Redis and PostgreSQL with resolution details.

        Args:
            alert_id: The unique alert identifier.
            resolved_at: When the alert was resolved.
            resolution_type: How it was resolved (auto, manual, timeout).
            resolution_value: The metric value at resolution time.

        Returns:
            Optional[Alert]: The updated alert, or None if not found.

        Raises:
            Exception: If update operation fails.

        Example:
            >>> updated = await storage.update_resolution(
            ...     alert_id="abc123",
            ...     resolved_at=datetime.utcnow(),
            ...     resolution_type="auto",
            ...     resolution_value=Decimal("2.5"),
            ... )
        """
        try:
            # Get current alert from Redis
            alert = await self.redis_client.get_alert(alert_id)
            if alert is None:
                logger.warning(
                    "alert_not_found_for_resolution",
                    alert_id=alert_id,
                )
                return None

            # Create resolved alert
            resolved_alert = alert.resolve(
                resolution_type=resolution_type,
                resolution_value=resolution_value,
                timestamp=resolved_at,
            )

            # Update Redis (will remove from active set)
            await self.redis_client.set_alert(resolved_alert)

            # Update PostgreSQL
            await self.postgres_client.update_alert_status(
                alert_id=alert_id,
                status="resolved",
                resolved_at=resolved_at,
                resolution_type=resolution_type,
                resolution_value=resolution_value,
                duration_seconds=resolved_alert.duration_seconds,
                peak_value=resolved_alert.peak_value,
                peak_at=resolved_alert.peak_at,
            )

            logger.info(
                "alert_resolved",
                alert_id=alert_id,
                resolution_type=resolution_type,
                duration_seconds=resolved_alert.duration_seconds,
            )

            return resolved_alert

        except Exception as e:
            logger.error(
                "alert_resolution_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise

    async def update_escalation(
        self,
        alert_id: str,
        new_priority: AlertPriority,
        escalated_at: datetime,
    ) -> Optional[Alert]:
        """
        Escalate an alert to a higher priority.

        Updates both Redis and PostgreSQL with escalation details.

        Args:
            alert_id: The unique alert identifier.
            new_priority: The new priority level (typically P1).
            escalated_at: When escalation occurred.

        Returns:
            Optional[Alert]: The escalated alert, or None if not found.

        Raises:
            Exception: If escalation operation fails.

        Example:
            >>> escalated = await storage.update_escalation(
            ...     alert_id="abc123",
            ...     new_priority=AlertPriority.P1,
            ...     escalated_at=datetime.utcnow(),
            ... )
        """
        try:
            # Get current alert from Redis
            alert = await self.redis_client.get_alert(alert_id)
            if alert is None:
                logger.warning(
                    "alert_not_found_for_escalation",
                    alert_id=alert_id,
                )
                return None

            # Create escalated alert
            escalated_alert = alert.escalate(
                new_priority=new_priority,
                timestamp=escalated_at,
            )

            # Update Redis
            await self.redis_client.set_alert(escalated_alert)

            # Update PostgreSQL
            await self.postgres_client.update_alert_status(
                alert_id=alert_id,
                status="escalated",
                escalated=True,
                escalated_at=escalated_at,
                new_priority=new_priority,
                original_priority=alert.priority,
            )

            logger.info(
                "alert_escalated",
                alert_id=alert_id,
                from_priority=alert.priority.value,
                to_priority=new_priority.value,
            )

            return escalated_alert

        except Exception as e:
            logger.error(
                "alert_escalation_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise

    async def update_peak(
        self,
        alert_id: str,
        peak_value: Decimal,
        peak_at: datetime,
    ) -> Optional[Alert]:
        """
        Update the peak value for an active alert.

        Only updates if the new value is more extreme than the current peak.

        Args:
            alert_id: The unique alert identifier.
            peak_value: The new potential peak value.
            peak_at: When the peak occurred.

        Returns:
            Optional[Alert]: The updated alert, or None if not found/not updated.

        Example:
            >>> updated = await storage.update_peak(
            ...     alert_id="abc123",
            ...     peak_value=Decimal("5.5"),
            ...     peak_at=datetime.utcnow(),
            ... )
        """
        try:
            alert = await self.redis_client.get_alert(alert_id)
            if alert is None:
                return None

            # Try to update peak
            updated_alert = alert.update_peak(peak_value, peak_at)

            # Only save if peak actually changed
            if updated_alert.peak_value != alert.peak_value:
                await self.redis_client.set_alert(updated_alert)
                await self.postgres_client.update_alert_status(
                    alert_id=alert_id,
                    status="peak_updated",
                    peak_value=peak_value,
                    peak_at=peak_at,
                )

                logger.debug(
                    "alert_peak_updated",
                    alert_id=alert_id,
                    peak_value=str(peak_value),
                )

                return updated_alert

            return alert

        except Exception as e:
            logger.error(
                "alert_peak_update_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise

    async def get_active_alerts(self) -> List[Alert]:
        """
        Get all active (non-resolved) alerts.

        Returns:
            List[Alert]: List of active alerts from Redis.

        Example:
            >>> active = await storage.get_active_alerts()
            >>> print(f"Active alerts: {len(active)}")
        """
        try:
            return await self.redis_client.get_active_alerts()
        except Exception as e:
            logger.error(
                "get_active_alerts_failed",
                error=str(e),
            )
            raise

    async def get_alert(self, alert_id: str) -> Optional[Alert]:
        """
        Get an alert by ID.

        Args:
            alert_id: The unique alert identifier.

        Returns:
            Optional[Alert]: The alert if found, None otherwise.

        Example:
            >>> alert = await storage.get_alert("abc123")
        """
        try:
            return await self.redis_client.get_alert(alert_id)
        except Exception as e:
            logger.error(
                "get_alert_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise

    async def get_alerts_for_escalation_check(
        self,
        escalation_threshold_seconds: int = 300,
    ) -> List[Alert]:
        """
        Get P2 alerts that may need escalation.

        Returns active P2 alerts that have been triggered for longer
        than the escalation threshold.

        Args:
            escalation_threshold_seconds: Seconds before P2 escalates to P1.

        Returns:
            List[Alert]: List of P2 alerts eligible for escalation.

        Example:
            >>> alerts = await storage.get_alerts_for_escalation_check(300)
            >>> for alert in alerts:
            ...     print(f"May escalate: {alert.alert_id}")
        """
        try:
            # Get all P2 alerts from Redis
            p2_alerts = await self.redis_client.get_alerts_by_priority(AlertPriority.P2)

            # Filter to those older than threshold
            now = datetime.utcnow()
            eligible = []

            for alert in p2_alerts:
                if alert.is_active and not alert.escalated:
                    age_seconds = (now - alert.triggered_at).total_seconds()
                    if age_seconds >= escalation_threshold_seconds:
                        eligible.append(alert)

            logger.debug(
                "alerts_for_escalation_check",
                total_p2=len(p2_alerts),
                eligible_count=len(eligible),
            )

            return eligible

        except Exception as e:
            logger.error(
                "get_alerts_for_escalation_failed",
                error=str(e),
            )
            raise

    async def get_alerts_by_instrument(self, instrument: str) -> List[Alert]:
        """
        Get active alerts for a specific instrument.

        Args:
            instrument: The instrument identifier.

        Returns:
            List[Alert]: List of active alerts for the instrument.

        Example:
            >>> alerts = await storage.get_alerts_by_instrument("BTC-USDT-PERP")
        """
        try:
            return await self.redis_client.get_alerts_by_instrument(instrument)
        except Exception as e:
            logger.error(
                "get_alerts_by_instrument_failed",
                instrument=instrument,
                error=str(e),
            )
            raise

    async def get_alerts_by_priority(self, priority: AlertPriority) -> List[Alert]:
        """
        Get active alerts by priority level.

        Args:
            priority: The priority level to filter by.

        Returns:
            List[Alert]: List of active alerts with the specified priority.

        Example:
            >>> p1_alerts = await storage.get_alerts_by_priority(AlertPriority.P1)
        """
        try:
            return await self.redis_client.get_alerts_by_priority(priority)
        except Exception as e:
            logger.error(
                "get_alerts_by_priority_failed",
                priority=priority.value,
                error=str(e),
            )
            raise

    async def remove_alert(self, alert_id: str) -> None:
        """
        Remove an alert from Redis (not from PostgreSQL history).

        Used for cleanup of old resolved alerts from Redis cache.

        Args:
            alert_id: The unique alert identifier to remove.

        Example:
            >>> await storage.remove_alert("abc123")
        """
        try:
            await self.redis_client.remove_alert(alert_id)
            logger.info(
                "alert_removed_from_cache",
                alert_id=alert_id,
            )
        except Exception as e:
            logger.error(
                "alert_remove_failed",
                alert_id=alert_id,
                error=str(e),
            )
            raise


async def create_alert_storage(
    redis_client: RedisClient,
    postgres_client: PostgresClient,
) -> AlertStorage:
    """
    Factory function to create an AlertStorage.

    Args:
        redis_client: Connected Redis client.
        postgres_client: Connected PostgreSQL client.

    Returns:
        AlertStorage: A new storage instance.

    Example:
        >>> storage = await create_alert_storage(redis_client, postgres_client)
    """
    return AlertStorage(redis_client, postgres_client)
