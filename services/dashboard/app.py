"""
FastAPI application for Crypto Market Microstructure Surveillance Dashboard.

This module creates and configures the FastAPI application with:
- CORS configuration for cross-origin requests
- Static file mounting for HTML/CSS/JS
- Router registration for API endpoints
- Lifespan events for database connection management
- WebSocket support for real-time updates

The dashboard runs on port 8050 by default and provides:
- REST API: /api/state, /api/alerts, /api/metrics, /api/health
- WebSocket: /ws/updates for real-time push updates
- Static files: Dashboard UI at /

Note:
    This module is owned by the VIZ agent.
    All metric values are READ from Redis/PostgreSQL - never calculated here.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logger = structlog.get_logger(__name__)


class AppState:
    """
    Application state container for database clients.

    Holds references to Redis and PostgreSQL clients that are
    initialized during application startup and closed on shutdown.
    """

    def __init__(self):
        self.redis_client: Optional[Any] = None
        self.postgres_client: Optional[Any] = None
        self.start_time: datetime = datetime.utcnow()
        self.config: Dict[str, Any] = {}


# Global application state
app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application lifespan events.

    Initializes database connections on startup and closes them on shutdown.
    This ensures proper resource management and prevents connection leaks.

    Args:
        app: FastAPI application instance.

    Yields:
        None: Control flow returns to the application.
    """
    logger.info("dashboard_starting")

    # Initialize Redis client
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        from services.dashboard.clients.redis_client import DashboardRedisClient
        app_state.redis_client = DashboardRedisClient(redis_url)
        await app_state.redis_client.connect()
        logger.info("redis_connected", url=redis_url)
    except Exception as e:
        logger.warning(
            "redis_connection_failed",
            error=str(e),
            message="Dashboard will run without real-time data",
        )
        app_state.redis_client = None

    # Initialize PostgreSQL client
    database_url = os.getenv("DATABASE_URL", "postgresql://surveillance:surveillance_dev@localhost:5432/surveillance")
    try:
        from services.dashboard.clients.postgres_client import DashboardPostgresClient
        app_state.postgres_client = DashboardPostgresClient(database_url)
        await app_state.postgres_client.connect()
        logger.info("postgres_connected")
    except Exception as e:
        logger.warning(
            "postgres_connection_failed",
            error=str(e),
            message="Dashboard will run without historical data",
        )
        app_state.postgres_client = None

    app_state.start_time = datetime.utcnow()
    logger.info("dashboard_ready")

    yield

    # Cleanup on shutdown
    logger.info("dashboard_shutting_down")

    if app_state.redis_client:
        try:
            await app_state.redis_client.disconnect()
            logger.info("redis_disconnected")
        except Exception as e:
            logger.error("redis_disconnect_error", error=str(e))

    if app_state.postgres_client:
        try:
            await app_state.postgres_client.disconnect()
            logger.info("postgres_disconnected")
        except Exception as e:
            logger.error("postgres_disconnect_error", error=str(e))

    logger.info("dashboard_shutdown_complete")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application instance.

    Example:
        >>> app = create_app()
        >>> # Run with uvicorn
        >>> import uvicorn
        >>> uvicorn.run(app, host="0.0.0.0", port=8050)
    """
    app = FastAPI(
        title="Crypto Market Microstructure Dashboard",
        description="Real-time surveillance dashboard for monitoring market quality and pricing integrity",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for dashboard
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    from services.dashboard.api.state import router as state_router
    from services.dashboard.api.alerts import router as alerts_router
    from services.dashboard.api.metrics import router as metrics_router
    from services.dashboard.api.health import router as health_router

    app.include_router(state_router, prefix="/api", tags=["State"])
    app.include_router(alerts_router, prefix="/api", tags=["Alerts"])
    app.include_router(metrics_router, prefix="/api", tags=["Metrics"])
    app.include_router(health_router, prefix="/api", tags=["Health"])

    # Register WebSocket router
    from services.dashboard.websocket.updates import router as ws_router
    app.include_router(ws_router, tags=["WebSocket"])

    # Mount static files
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Serve index.html at root
    @app.get("/", include_in_schema=False)
    async def serve_index():
        """Serve the main dashboard page."""
        index_path = static_path / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return {"message": "Dashboard UI not found. Please ensure static files are present."}

    logger.info("fastapi_app_created")

    return app


# Create the application instance
app = create_app()
