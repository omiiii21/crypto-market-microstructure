"""
Storage clients for the surveillance system.

This module provides clients for Redis (real-time state) and
PostgreSQL/TimescaleDB (historical data) storage.

Components:
    redis_client: Async Redis client for current state and pub/sub
    postgres_client: Async PostgreSQL client for time-series data

Note:
    This module is owned by the ARCHITECT agent.
"""

from src.storage.redis_client import (
    RedisClient,
    RedisClientError,
    RedisConnectionException,
    RedisOperationError,
)
from src.storage.postgres_client import (
    PostgresClient,
    PostgresClientError,
    PostgresConnectionException,
    PostgresOperationError,
)

__all__: list[str] = [
    # Redis
    "RedisClient",
    "RedisClientError",
    "RedisConnectionException",
    "RedisOperationError",
    # PostgreSQL
    "PostgresClient",
    "PostgresClientError",
    "PostgresConnectionException",
    "PostgresOperationError",
]
