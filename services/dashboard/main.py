"""
Dashboard service entry point.

This module initializes and runs the FastAPI surveillance dashboard using Uvicorn.

The dashboard:
- Runs on 0.0.0.0:8050 by default
- Provides REST API for current state, alerts, metrics, and health
- Provides WebSocket for real-time push updates
- Serves static HTML/CSS/JS dashboard UI

Usage:
    python -m services.dashboard.main

    Or with uvicorn directly:
    uvicorn services.dashboard.main:app --host 0.0.0.0 --port 8050

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DATABASE_URL: PostgreSQL connection URL
    LOG_LEVEL: Logging level (default: INFO)
    DASHBOARD_PORT: Port to run the dashboard on (default: 8050)
    DASHBOARD_HOST: Host to bind to (default: 0.0.0.0)

Note:
    This module is owned by the VIZ agent.
"""

import logging
import os
import sys
from pathlib import Path

import structlog
import uvicorn

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def setup_logging() -> None:
    """Configure structured logging for the dashboard service."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level),
    )

    # Reduce noise from uvicorn access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main() -> None:
    """
    Main entry point for the dashboard service.

    Configures logging and starts the Uvicorn server with the FastAPI application.
    """
    setup_logging()

    logger = structlog.get_logger(__name__)
    logger.info(
        "dashboard_service_starting",
        version="1.0.0",
        python_version=sys.version,
    )

    # Get configuration from environment
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8050"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    # Run uvicorn
    uvicorn.run(
        "services.dashboard.app:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
        workers=1,
        access_log=False,
    )


# Export the app for uvicorn direct usage
from services.dashboard.app import app

if __name__ == "__main__":
    main()
