-- ============================================================================
-- Crypto Market Surveillance System
-- Database Seed Script
-- ============================================================================
-- This script inserts default configuration data for alerts.
-- Run after init.sql to populate alert definitions and thresholds.
-- ============================================================================

-- ============================================================================
-- ALERT DEFINITIONS
-- ============================================================================
-- Insert all alert type definitions

INSERT INTO alert_definitions (alert_type, name, description, metric_name, default_priority, default_severity, condition, requires_zscore, persistence_seconds, throttle_seconds, escalation_seconds, escalates_to, enabled)
VALUES
    -- Spread alerts
    ('spread_warning', 'Spread Warning', 'Bid-ask spread exceeds warning threshold', 'spread_bps', 'P2', 'warning', 'gt', TRUE, NULL, 60, 300, 'spread_critical', TRUE),
    ('spread_critical', 'Spread Critical', 'Bid-ask spread exceeds critical threshold', 'spread_bps', 'P1', 'critical', 'gt', TRUE, NULL, 30, NULL, NULL, TRUE),

    -- Basis alerts
    ('basis_warning', 'Basis Warning', 'Perp-spot basis exceeds warning threshold', 'basis_bps', 'P2', 'warning', 'abs_gt', TRUE, 120, 60, 300, 'basis_critical', TRUE),
    ('basis_critical', 'Basis Critical', 'Perp-spot basis exceeds critical threshold', 'basis_bps', 'P1', 'critical', 'abs_gt', TRUE, 60, 30, NULL, NULL, TRUE),

    -- Depth alerts
    ('depth_warning', 'Depth Warning', 'Order book depth below warning threshold', 'depth_10bps_total', 'P2', 'warning', 'lt', FALSE, NULL, 60, 300, 'depth_critical', TRUE),
    ('depth_critical', 'Depth Critical', 'Order book depth below critical threshold', 'depth_10bps_total', 'P1', 'critical', 'lt', FALSE, NULL, 30, NULL, NULL, TRUE),
    ('depth_drop', 'Depth Drop', 'Sudden drop in order book depth', 'depth_10bps_change_pct', 'P2', 'warning', 'lt', FALSE, NULL, 60, NULL, NULL, TRUE),

    -- Mark price alerts
    ('mark_deviation_warning', 'Mark Price Deviation Warning', 'Mark price deviates from index', 'mark_index_deviation_bps', 'P2', 'warning', 'abs_gt', TRUE, NULL, 60, 300, 'mark_deviation_critical', TRUE),
    ('mark_deviation_critical', 'Mark Price Deviation Critical', 'Mark price significantly deviates from index', 'mark_index_deviation_bps', 'P1', 'critical', 'abs_gt', TRUE, NULL, 30, NULL, NULL, TRUE),

    -- Cross-exchange alerts
    ('cross_exchange_divergence', 'Cross-Exchange Divergence', 'Price divergence between exchanges', 'cross_exchange_spread_bps', 'P2', 'warning', 'abs_gt', FALSE, 10, 60, NULL, NULL, TRUE),

    -- System alerts
    ('data_gap', 'Data Gap Detected', 'Gap in data stream detected', 'gap_duration_seconds', 'P3', 'info', 'gt', FALSE, NULL, 30, NULL, NULL, TRUE)
ON CONFLICT (alert_type) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    metric_name = EXCLUDED.metric_name,
    default_priority = EXCLUDED.default_priority,
    default_severity = EXCLUDED.default_severity,
    condition = EXCLUDED.condition,
    requires_zscore = EXCLUDED.requires_zscore,
    persistence_seconds = EXCLUDED.persistence_seconds,
    throttle_seconds = EXCLUDED.throttle_seconds,
    escalation_seconds = EXCLUDED.escalation_seconds,
    escalates_to = EXCLUDED.escalates_to,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- ALERT THRESHOLDS - BTC-USDT-PERP
-- ============================================================================

INSERT INTO alert_thresholds (alert_type, instrument, threshold, zscore_threshold, enabled)
VALUES
    ('spread_warning', 'BTC-USDT-PERP', 3.0, 2.0, TRUE),
    ('spread_critical', 'BTC-USDT-PERP', 5.0, 3.0, TRUE),
    ('basis_warning', 'BTC-USDT-PERP', 10.0, 2.0, TRUE),
    ('basis_critical', 'BTC-USDT-PERP', 20.0, 3.0, TRUE),
    ('depth_warning', 'BTC-USDT-PERP', 500000, NULL, TRUE),
    ('depth_critical', 'BTC-USDT-PERP', 200000, NULL, TRUE),
    ('depth_drop', 'BTC-USDT-PERP', -30, NULL, TRUE),
    ('mark_deviation_warning', 'BTC-USDT-PERP', 15.0, 2.0, TRUE),
    ('mark_deviation_critical', 'BTC-USDT-PERP', 30.0, 3.0, TRUE),
    ('cross_exchange_divergence', 'BTC-USDT-PERP', 5.0, NULL, TRUE),
    ('data_gap', 'BTC-USDT-PERP', 5, NULL, TRUE)
ON CONFLICT (alert_type, instrument) DO UPDATE SET
    threshold = EXCLUDED.threshold,
    zscore_threshold = EXCLUDED.zscore_threshold,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- ALERT THRESHOLDS - BTC-USDT-SPOT
-- ============================================================================

INSERT INTO alert_thresholds (alert_type, instrument, threshold, zscore_threshold, enabled)
VALUES
    ('spread_warning', 'BTC-USDT-SPOT', 2.0, 2.0, TRUE),
    ('spread_critical', 'BTC-USDT-SPOT', 4.0, 3.0, TRUE),
    ('depth_warning', 'BTC-USDT-SPOT', 300000, NULL, TRUE),
    ('depth_critical', 'BTC-USDT-SPOT', 100000, NULL, TRUE),
    ('depth_drop', 'BTC-USDT-SPOT', -30, NULL, TRUE),
    ('cross_exchange_divergence', 'BTC-USDT-SPOT', 3.0, NULL, TRUE),
    ('data_gap', 'BTC-USDT-SPOT', 5, NULL, TRUE)
ON CONFLICT (alert_type, instrument) DO UPDATE SET
    threshold = EXCLUDED.threshold,
    zscore_threshold = EXCLUDED.zscore_threshold,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- ALERT THRESHOLDS - ETH-USDT-PERP (Future use)
-- ============================================================================

INSERT INTO alert_thresholds (alert_type, instrument, threshold, zscore_threshold, enabled)
VALUES
    ('spread_warning', 'ETH-USDT-PERP', 5.0, 2.0, FALSE),
    ('spread_critical', 'ETH-USDT-PERP', 10.0, 3.0, FALSE),
    ('basis_warning', 'ETH-USDT-PERP', 15.0, 2.0, FALSE),
    ('basis_critical', 'ETH-USDT-PERP', 30.0, 3.0, FALSE),
    ('depth_warning', 'ETH-USDT-PERP', 200000, NULL, FALSE),
    ('depth_critical', 'ETH-USDT-PERP', 75000, NULL, FALSE),
    ('depth_drop', 'ETH-USDT-PERP', -30, NULL, FALSE),
    ('mark_deviation_warning', 'ETH-USDT-PERP', 20.0, 2.0, FALSE),
    ('mark_deviation_critical', 'ETH-USDT-PERP', 40.0, 3.0, FALSE),
    ('cross_exchange_divergence', 'ETH-USDT-PERP', 8.0, NULL, FALSE),
    ('data_gap', 'ETH-USDT-PERP', 5, NULL, FALSE)
ON CONFLICT (alert_type, instrument) DO UPDATE SET
    threshold = EXCLUDED.threshold,
    zscore_threshold = EXCLUDED.zscore_threshold,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- ALERT THRESHOLDS - ETH-USDT-SPOT (Future use)
-- ============================================================================

INSERT INTO alert_thresholds (alert_type, instrument, threshold, zscore_threshold, enabled)
VALUES
    ('spread_warning', 'ETH-USDT-SPOT', 4.0, 2.0, FALSE),
    ('spread_critical', 'ETH-USDT-SPOT', 8.0, 3.0, FALSE),
    ('depth_warning', 'ETH-USDT-SPOT', 150000, NULL, FALSE),
    ('depth_critical', 'ETH-USDT-SPOT', 50000, NULL, FALSE),
    ('depth_drop', 'ETH-USDT-SPOT', -30, NULL, FALSE),
    ('cross_exchange_divergence', 'ETH-USDT-SPOT', 5.0, NULL, FALSE),
    ('data_gap', 'ETH-USDT-SPOT', 5, NULL, FALSE)
ON CONFLICT (alert_type, instrument) DO UPDATE SET
    threshold = EXCLUDED.threshold,
    zscore_threshold = EXCLUDED.zscore_threshold,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- ALERT THRESHOLDS - DEFAULT FALLBACK
-- ============================================================================
-- Used for any instrument without specific thresholds

INSERT INTO alert_thresholds (alert_type, instrument, threshold, zscore_threshold, enabled)
VALUES
    ('spread_warning', '*', 10.0, 2.0, TRUE),
    ('spread_critical', '*', 20.0, 3.0, TRUE),
    ('basis_warning', '*', 25.0, 2.0, TRUE),
    ('basis_critical', '*', 50.0, 3.0, TRUE),
    ('depth_warning', '*', 100000, NULL, TRUE),
    ('depth_critical', '*', 50000, NULL, TRUE),
    ('depth_drop', '*', -40, NULL, TRUE),
    ('mark_deviation_warning', '*', 25.0, 2.0, TRUE),
    ('mark_deviation_critical', '*', 50.0, 3.0, TRUE),
    ('cross_exchange_divergence', '*', 10.0, NULL, TRUE),
    ('data_gap', '*', 5, NULL, TRUE)
ON CONFLICT (alert_type, instrument) DO UPDATE SET
    threshold = EXCLUDED.threshold,
    zscore_threshold = EXCLUDED.zscore_threshold,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();


-- ============================================================================
-- VERIFICATION
-- ============================================================================
-- Verify data was inserted correctly

DO $$
DECLARE
    def_count INT;
    threshold_count INT;
BEGIN
    SELECT COUNT(*) INTO def_count FROM alert_definitions;
    SELECT COUNT(*) INTO threshold_count FROM alert_thresholds;

    RAISE NOTICE 'Seed completed: % alert definitions, % thresholds', def_count, threshold_count;

    IF def_count < 11 THEN
        RAISE WARNING 'Expected at least 11 alert definitions, got %', def_count;
    END IF;

    IF threshold_count < 40 THEN
        RAISE WARNING 'Expected at least 40 thresholds, got %', threshold_count;
    END IF;
END $$;
