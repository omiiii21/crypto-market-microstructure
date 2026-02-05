-- ============================================================================
-- Crypto Market Surveillance System
-- Database Initialization Script
-- ============================================================================
-- This script creates all tables and indexes for the surveillance system.
-- It is designed for TimescaleDB (PostgreSQL 15 + time-series extension).
--
-- Run order:
--   1. init.sql (this file) - Creates schema
--   2. seed.sql - Inserts default configuration
-- ============================================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- ORDER BOOK SNAPSHOTS
-- ============================================================================
-- Stores aggregated order book data at 1-second intervals.
-- Raw order book levels stored as JSONB for replay capability.

CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id BIGSERIAL,
    exchange VARCHAR(20) NOT NULL,
    instrument VARCHAR(30) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    local_timestamp TIMESTAMPTZ NOT NULL,
    sequence_id BIGINT,

    -- Best prices
    best_bid DECIMAL(20, 8),
    best_ask DECIMAL(20, 8),
    mid_price DECIMAL(20, 8),

    -- Computed metrics (stored for fast queries)
    spread_abs DECIMAL(20, 8),
    spread_bps DECIMAL(10, 4),

    -- Depth at various bps levels (USD notional)
    depth_5bps_bid DECIMAL(20, 2),
    depth_5bps_ask DECIMAL(20, 2),
    depth_5bps_total DECIMAL(20, 2),
    depth_10bps_bid DECIMAL(20, 2),
    depth_10bps_ask DECIMAL(20, 2),
    depth_10bps_total DECIMAL(20, 2),
    depth_25bps_bid DECIMAL(20, 2),
    depth_25bps_ask DECIMAL(20, 2),
    depth_25bps_total DECIMAL(20, 2),

    -- Imbalance
    imbalance DECIMAL(5, 4),

    -- Raw data for replay
    bids_json JSONB,
    asks_json JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, created_at)
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('order_book_snapshots', 'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_obs_exchange_instrument_time
    ON order_book_snapshots (exchange, instrument, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_obs_instrument_created
    ON order_book_snapshots (instrument, created_at DESC);


-- ============================================================================
-- METRICS TIME-SERIES
-- ============================================================================
-- Stores computed metrics with optional z-scores.
-- Used for historical analysis and dashboard charts.

CREATE TABLE IF NOT EXISTS metrics (
    id BIGSERIAL,
    metric_name VARCHAR(50) NOT NULL,
    exchange VARCHAR(20) NOT NULL,
    instrument VARCHAR(30) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    value DECIMAL(20, 8) NOT NULL,
    zscore DECIMAL(10, 4),

    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, created_at)
);

-- Convert to hypertable
SELECT create_hypertable('metrics', 'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_metrics_name_instrument_time
    ON metrics (metric_name, instrument, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_metrics_instrument_created
    ON metrics (instrument, created_at DESC);


-- ============================================================================
-- DATA GAPS
-- ============================================================================
-- Records periods of missing data for audit and analysis exclusion.

CREATE TABLE IF NOT EXISTS data_gaps (
    id BIGSERIAL PRIMARY KEY,
    exchange VARCHAR(20) NOT NULL,
    instrument VARCHAR(30) NOT NULL,
    gap_start TIMESTAMPTZ NOT NULL,
    gap_end TIMESTAMPTZ NOT NULL,
    duration_seconds DECIMAL(10, 3) NOT NULL,
    reason VARCHAR(50) NOT NULL,
    sequence_id_before BIGINT,
    sequence_id_after BIGINT,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gaps_exchange_instrument_time
    ON data_gaps (exchange, instrument, gap_start DESC);


-- ============================================================================
-- ALERTS
-- ============================================================================
-- Stores all triggered alerts with full context and lifecycle tracking.

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_id VARCHAR(100) UNIQUE NOT NULL,

    -- Classification
    alert_type VARCHAR(50) NOT NULL,
    priority VARCHAR(5) NOT NULL,
    severity VARCHAR(10) NOT NULL,

    -- Location
    exchange VARCHAR(20),
    instrument VARCHAR(30),

    -- Primary trigger
    trigger_metric VARCHAR(50) NOT NULL,
    trigger_value DECIMAL(20, 8) NOT NULL,
    trigger_threshold DECIMAL(20, 8) NOT NULL,
    trigger_condition VARCHAR(10) NOT NULL,

    -- Z-score trigger (optional)
    zscore_value DECIMAL(10, 4),
    zscore_threshold DECIMAL(10, 4),

    -- Lifecycle timestamps
    triggered_at TIMESTAMPTZ NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    duration_seconds INT,

    -- Peak tracking
    peak_value DECIMAL(20, 8),
    peak_at TIMESTAMPTZ,

    -- Escalation
    escalated BOOLEAN DEFAULT FALSE,
    escalated_at TIMESTAMPTZ,
    original_priority VARCHAR(5),

    -- Context (additional data as JSON)
    context JSONB DEFAULT '{}',

    -- Resolution
    resolution_type VARCHAR(20),
    resolution_value DECIMAL(20, 8),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_alerts_active
    ON alerts (resolved_at) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_alerts_instrument_time
    ON alerts (instrument, triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_type_time
    ON alerts (alert_type, triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_priority
    ON alerts (priority, triggered_at DESC);


-- ============================================================================
-- ALERT DEFINITIONS (Configuration)
-- ============================================================================
-- Stores alert type configurations (can be managed via config files or DB).

CREATE TABLE IF NOT EXISTS alert_definitions (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    metric_name VARCHAR(50) NOT NULL,
    default_priority VARCHAR(5) NOT NULL,
    default_severity VARCHAR(10) NOT NULL,
    condition VARCHAR(10) NOT NULL,
    requires_zscore BOOLEAN DEFAULT FALSE,
    persistence_seconds INT,
    throttle_seconds INT DEFAULT 60,
    escalation_seconds INT,
    escalates_to VARCHAR(50),
    enabled BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================================
-- ALERT THRESHOLDS (Per-Instrument Configuration)
-- ============================================================================
-- Stores per-instrument threshold configurations.

CREATE TABLE IF NOT EXISTS alert_thresholds (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(50) NOT NULL REFERENCES alert_definitions(alert_type),
    instrument VARCHAR(30) NOT NULL,  -- '*' for default

    threshold DECIMAL(20, 8) NOT NULL,
    zscore_threshold DECIMAL(10, 4),

    priority_override VARCHAR(5),
    enabled BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(alert_type, instrument)
);


-- ============================================================================
-- TICKER SNAPSHOTS
-- ============================================================================
-- Stores ticker data including mark/index prices and funding rates.

CREATE TABLE IF NOT EXISTS ticker_snapshots (
    id BIGSERIAL,
    exchange VARCHAR(20) NOT NULL,
    instrument VARCHAR(30) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,

    last_price DECIMAL(20, 8) NOT NULL,
    mark_price DECIMAL(20, 8),
    index_price DECIMAL(20, 8),

    volume_24h DECIMAL(20, 8),
    volume_24h_usd DECIMAL(20, 2),
    high_24h DECIMAL(20, 8),
    low_24h DECIMAL(20, 8),

    funding_rate DECIMAL(20, 8),
    next_funding_time TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, created_at)
);

-- Convert to hypertable
SELECT create_hypertable('ticker_snapshots', 'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_ticker_instrument_time
    ON ticker_snapshots (instrument, timestamp DESC);


-- ============================================================================
-- BASIS METRICS
-- ============================================================================
-- Stores basis (perp-spot) metrics for derivatives surveillance.

CREATE TABLE IF NOT EXISTS basis_metrics (
    id BIGSERIAL,
    perp_instrument VARCHAR(30) NOT NULL,
    spot_instrument VARCHAR(30) NOT NULL,
    exchange VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,

    perp_mid DECIMAL(20, 8) NOT NULL,
    spot_mid DECIMAL(20, 8) NOT NULL,
    basis_abs DECIMAL(20, 8) NOT NULL,
    basis_bps DECIMAL(10, 4) NOT NULL,
    zscore DECIMAL(10, 4),

    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, created_at)
);

-- Convert to hypertable
SELECT create_hypertable('basis_metrics', 'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_basis_perp_time
    ON basis_metrics (perp_instrument, timestamp DESC);


-- ============================================================================
-- SYSTEM HEALTH
-- ============================================================================
-- Stores periodic health snapshots for monitoring.

CREATE TABLE IF NOT EXISTS health_snapshots (
    id BIGSERIAL,
    exchange VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    last_message_at TIMESTAMPTZ,
    message_count BIGINT DEFAULT 0,
    lag_ms INT DEFAULT 0,
    reconnect_count INT DEFAULT 0,
    gaps_last_hour INT DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, created_at)
);

-- Convert to hypertable
SELECT create_hypertable('health_snapshots', 'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);


-- ============================================================================
-- RETENTION POLICIES
-- ============================================================================
-- Set up automatic data retention using TimescaleDB policies.

-- Order book snapshots: 30 days
SELECT add_retention_policy('order_book_snapshots', INTERVAL '30 days', if_not_exists => TRUE);

-- Metrics: 90 days
SELECT add_retention_policy('metrics', INTERVAL '90 days', if_not_exists => TRUE);

-- Ticker snapshots: 30 days
SELECT add_retention_policy('ticker_snapshots', INTERVAL '30 days', if_not_exists => TRUE);

-- Basis metrics: 90 days
SELECT add_retention_policy('basis_metrics', INTERVAL '90 days', if_not_exists => TRUE);

-- Health snapshots: 7 days
SELECT add_retention_policy('health_snapshots', INTERVAL '7 days', if_not_exists => TRUE);


-- ============================================================================
-- COMPRESSION POLICIES
-- ============================================================================
-- Enable compression for older data to save storage.

-- Order book snapshots: compress after 7 days
ALTER TABLE order_book_snapshots SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange,instrument'
);
SELECT add_compression_policy('order_book_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- Metrics: compress after 7 days
ALTER TABLE metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'metric_name,instrument'
);
SELECT add_compression_policy('metrics', INTERVAL '7 days', if_not_exists => TRUE);

-- Ticker snapshots: compress after 7 days
ALTER TABLE ticker_snapshots SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange,instrument'
);
SELECT add_compression_policy('ticker_snapshots', INTERVAL '7 days', if_not_exists => TRUE);


-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to get latest metrics for an instrument
CREATE OR REPLACE FUNCTION get_latest_metrics(
    p_instrument VARCHAR,
    p_lookback_minutes INT DEFAULT 5
)
RETURNS TABLE (
    metric_name VARCHAR,
    latest_value DECIMAL,
    latest_zscore DECIMAL,
    avg_value DECIMAL,
    min_value DECIMAL,
    max_value DECIMAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.metric_name,
        (SELECT value FROM metrics WHERE instrument = p_instrument AND metric_name = m.metric_name ORDER BY timestamp DESC LIMIT 1) as latest_value,
        (SELECT zscore FROM metrics WHERE instrument = p_instrument AND metric_name = m.metric_name ORDER BY timestamp DESC LIMIT 1) as latest_zscore,
        AVG(m.value) as avg_value,
        MIN(m.value) as min_value,
        MAX(m.value) as max_value
    FROM metrics m
    WHERE m.instrument = p_instrument
      AND m.timestamp > NOW() - (p_lookback_minutes || ' minutes')::INTERVAL
    GROUP BY m.metric_name;
END;
$$ LANGUAGE plpgsql;


-- Function to get active alerts count by priority
CREATE OR REPLACE FUNCTION get_active_alerts_summary()
RETURNS TABLE (
    priority VARCHAR,
    alert_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.priority,
        COUNT(*) as alert_count
    FROM alerts a
    WHERE a.resolved_at IS NULL
    GROUP BY a.priority
    ORDER BY a.priority;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- GRANTS
-- ============================================================================
-- Grant permissions for the surveillance user

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO surveillance;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO surveillance;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO surveillance;
