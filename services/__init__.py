"""
Docker service entry points for the surveillance system.

Each subdirectory contains a standalone service that runs in Docker.

Services:
    data-ingestion: Exchange WebSocket connections and data normalization
    metrics-engine: Metric calculation and z-score computation
    anomaly-detector: Alert evaluation and notification
    dashboard: Plotly Dash web interface
"""
