# Dashboard Service Dockerfile
# Crypto Market Microstructure Surveillance System
#
# This Dockerfile builds the FastAPI surveillance dashboard with real-time
# WebSocket updates and REST API endpoints.
#
# Build:
#   docker build -t crypto-dashboard -f services/dashboard/Dockerfile .
#
# Run:
#   docker run -p 8050:8050 crypto-dashboard

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for installation
COPY pyproject.toml ./
COPY src/ ./src/

# Install the base package and all dependencies
RUN pip install --upgrade pip && \
    pip install .

# Install FastAPI-specific dependencies for the dashboard
# These replace Dash for an ASGI-native async architecture
RUN pip install \
    "fastapi>=0.109.0,<1.0" \
    "uvicorn[standard]>=0.27.0,<1.0" \
    "python-multipart>=0.0.6,<1.0"

# Copy config and service files
COPY config/ ./config/
COPY services/dashboard/ ./services/dashboard/

# Create non-root user for security
RUN useradd -m -u 1000 dashboard && \
    chown -R dashboard:dashboard /app

USER dashboard

# Expose dashboard port
EXPOSE 8050

# Health check - updated endpoint for FastAPI
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8050/api/health || exit 1

# Run with Uvicorn ASGI server
CMD ["python", "-m", "uvicorn", "services.dashboard.app:app", "--host", "0.0.0.0", "--port", "8050"]
