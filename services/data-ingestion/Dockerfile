# Data Ingestion Service Dockerfile
# Builds a container for the data ingestion service that streams
# market data from exchange WebSocket connections.

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for installation
COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and all dependencies
RUN pip install --upgrade pip && \
    pip install .

# Copy config and service files
COPY config/ ./config/
COPY services/data-ingestion/ ./services/data-ingestion/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import redis; r=redis.Redis.from_url('${REDIS_URL:-redis://localhost:6379}'); r.ping()" || exit 1

# Default environment variables
ENV REDIS_URL=redis://redis:6379 \
    DATABASE_URL=postgresql://surveillance:surveillance_dev@timescaledb:5432/surveillance \
    LOG_LEVEL=INFO \
    CONFIG_PATH=config

# Run the service
CMD ["python", "-m", "services.data-ingestion.main"]
