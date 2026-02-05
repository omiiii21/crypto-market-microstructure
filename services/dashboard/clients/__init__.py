"""
Dashboard database clients.

This package provides async database clients for the dashboard:
- DashboardRedisClient: Async Redis client using redis.asyncio
- DashboardPostgresClient: Async PostgreSQL client using asyncpg

"""

from services.dashboard.clients.redis_client import DashboardRedisClient
from services.dashboard.clients.postgres_client import DashboardPostgresClient

__all__ = ["DashboardRedisClient", "DashboardPostgresClient"]
