/**
 * Main dashboard application controller.
 *
 * Initializes and coordinates:
 * - WebSocket connection for real-time updates
 * - REST API polling for historical data
 * - UI state management
 * - Chart rendering
 */

const DashboardApp = {
    // Current state
    state: {
        exchange: 'all',
        instrument: 'BTC-USDT-PERP',
        timeRange: '1h',
        connected: false
    },

    // Update intervals
    intervals: {
        charts: null,
        health: null
    },

    // Thresholds for status indicators
    thresholds: {
        spread: { warning: 3.0, critical: 5.0 },
        basis: { warning: 10.0, critical: 20.0 },
        depth: { warning: 500000, critical: 200000 },
        divergence: { warning: 5.0, critical: 10.0 }
    },

    /**
     * Initialize the dashboard application.
     */
    init() {
        console.log('Dashboard initializing...');

        // Setup event listeners
        this.setupEventListeners();

        // Setup WebSocket callbacks
        this.setupWebSocket();

        // Connect WebSocket
        DashboardWebSocket.connect();

        // Initial data load
        this.loadInitialData();

        // Start polling intervals
        this.startPolling();

        // Update timestamp
        this.updateTimestamp();
        setInterval(() => this.updateTimestamp(), 1000);

        console.log('Dashboard initialized');
    },

    /**
     * Setup UI event listeners.
     */
    setupEventListeners() {
        // Exchange filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.state.exchange = e.target.dataset.exchange;
                this.updateStateExchangeLabel();
                this.updateWebSocketSubscription();
                this.loadChartData();
            });
        });

        // Time range buttons
        document.querySelectorAll('.time-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.state.timeRange = e.target.dataset.range;
                this.loadChartData();
            });
        });

        // Chart options checkboxes
        document.getElementById('spread-show-thresholds')?.addEventListener('change', () => this.loadChartData());
        document.getElementById('spread-show-zscore')?.addEventListener('change', () => this.loadChartData());
        document.getElementById('basis-show-thresholds')?.addEventListener('change', () => this.loadChartData());
        document.getElementById('basis-show-zscore')?.addEventListener('change', () => this.loadChartData());

        // Depth exchange selector
        document.getElementById('depth-exchange-select')?.addEventListener('change', (e) => {
            this.loadDepthData(e.target.value);
        });
    },

    /**
     * Setup WebSocket callbacks.
     */
    setupWebSocket() {
        DashboardWebSocket.setCallbacks({
            onConnect: () => {
                this.state.connected = true;
                this.updateConnectionStatus('connected');
            },
            onDisconnect: () => {
                this.state.connected = false;
                this.updateConnectionStatus('disconnected');
            },
            onState: (data, exchange, instrument) => {
                this.handleStateUpdate(data, exchange, instrument);
            },
            onAlerts: (data) => {
                this.handleAlertsUpdate(data);
            },
            onHealth: (data) => {
                this.handleHealthUpdate(data);
            },
            onError: (error) => {
                console.error('WebSocket error:', error);
            }
        });
    },

    /**
     * Update WebSocket subscription based on current filters.
     */
    updateWebSocketSubscription() {
        const exchanges = this.state.exchange === 'all'
            ? ['binance', 'okx']
            : [this.state.exchange];

        DashboardWebSocket.updateSubscription({
            exchanges: exchanges,
            instruments: [this.state.instrument]
        });
    },

    /**
     * Load initial data from REST API.
     */
    async loadInitialData() {
        await Promise.all([
            this.loadStateData(),
            this.loadChartData(),
            this.loadAlertsData(),
            this.loadHealthData(),
            this.loadCrossExchangeData()
        ]);
    },

    /**
     * Start polling intervals for chart updates.
     */
    startPolling() {
        // Charts update every 5 seconds
        this.intervals.charts = setInterval(() => {
            this.loadChartData();
        }, 5000);

        // Health check every 5 seconds
        this.intervals.health = setInterval(() => {
            this.loadHealthData();
        }, 5000);

        // Cross-exchange comparison every 5 seconds
        this.intervals.crossExchange = setInterval(() => {
            this.loadCrossExchangeData();
        }, 5000);
    },

    /**
     * Update the last update timestamp display.
     */
    updateTimestamp() {
        const now = new Date();
        const timeStr = now.toISOString().slice(11, 19) + ' UTC';
        document.getElementById('last-update-time').textContent = timeStr;
    },

    /**
     * Update connection status indicator.
     * @param {string} status - Connection status
     */
    updateConnectionStatus(status) {
        const el = document.getElementById('connection-status');
        el.className = `connection-status ${status} visible`;
        el.querySelector('.status-text').textContent =
            status === 'connected' ? 'Connected' :
            status === 'disconnected' ? 'Disconnected - Reconnecting...' :
            'Connecting...';

        // Hide after 3 seconds if connected
        if (status === 'connected') {
            setTimeout(() => {
                el.classList.remove('visible');
            }, 3000);
        }
    },

    /**
     * Update state exchange label.
     */
    updateStateExchangeLabel() {
        const label = this.state.exchange === 'all' ? 'All Exchanges' :
            this.state.exchange.charAt(0).toUpperCase() + this.state.exchange.slice(1);
        document.getElementById('state-exchange').textContent = label;
    },

    // =========================================================================
    // STATE UPDATES
    // =========================================================================

    /**
     * Load current state from REST API.
     */
    async loadStateData() {
        const exchange = this.state.exchange === 'all' ? 'binance' : this.state.exchange;
        const data = await DashboardAPI.getState(exchange, this.state.instrument);

        if (data) {
            this.updateStateUI(data);
        }
    },

    /**
     * Handle WebSocket state update.
     */
    handleStateUpdate(data, exchange, instrument) {
        // Only update if matches current filter
        if (this.state.exchange !== 'all' && this.state.exchange !== exchange) {
            return;
        }
        if (instrument !== this.state.instrument) {
            return;
        }

        this.updateStateUI(data);
    },

    /**
     * Update state UI elements.
     * @param {Object} data - State data
     */
    updateStateUI(data) {
        // Spread
        const spreadValue = data.spread_bps ? parseFloat(data.spread_bps).toFixed(2) : '--';
        document.getElementById('metric-spread').textContent = spreadValue;
        this.setStatusIndicator('status-spread', this.getStatus(
            parseFloat(data.spread_bps),
            this.thresholds.spread.warning,
            this.thresholds.spread.critical
        ));

        // Spread Z-Score
        const zscoreEl = document.getElementById('zscore-spread');
        if (data.spread_zscore_status === 'warming') {
            zscoreEl.className = 'zscore-row warming';
            zscoreEl.textContent = `Z-Score: warming (${data.spread_warmup_progress})`;
        } else if (data.spread_zscore) {
            const zscore = parseFloat(data.spread_zscore);
            zscoreEl.className = 'zscore-row ' + (Math.abs(zscore) > 3 ? 'critical' : Math.abs(zscore) > 2 ? 'warning' : 'active');
            zscoreEl.textContent = `Z-Score: ${zscore.toFixed(1)}`;
        } else {
            zscoreEl.className = 'zscore-row';
            zscoreEl.textContent = 'Z-Score: --';
        }

        // Depth
        const depthValue = data.depth_10bps_total ?
            '$' + (parseFloat(data.depth_10bps_total) / 1e6).toFixed(2) + 'M' : '--';
        document.getElementById('metric-depth').textContent = depthValue;
        this.setStatusIndicator('status-depth', this.getStatus(
            parseFloat(data.depth_10bps_total),
            this.thresholds.depth.warning,
            this.thresholds.depth.critical,
            true  // Lower is worse for depth
        ));

        // Imbalance
        const imbalance = data.imbalance ? parseFloat(data.imbalance) : null;
        if (imbalance !== null) {
            document.getElementById('metric-imbalance').textContent = imbalance.toFixed(2);
            this.setStatusIndicator('status-imbalance', Math.abs(imbalance) > 0.3 ? 'warning' : 'normal');
        } else {
            document.getElementById('metric-imbalance').textContent = '--';
            this.setStatusIndicator('status-imbalance', 'unavailable');
        }
    },

    // =========================================================================
    // ALERTS
    // =========================================================================

    /**
     * Load alerts from REST API.
     */
    async loadAlertsData() {
        const data = await DashboardAPI.getAlerts({ status: 'active' });
        this.updateAlertsUI(data);
    },

    /**
     * Handle WebSocket alerts update.
     */
    handleAlertsUpdate(data) {
        this.updateAlertsUI(data);
    },

    /**
     * Update alerts UI.
     * @param {Object} data - Alerts data
     */
    updateAlertsUI(data) {
        const container = document.getElementById('alerts-container');
        const countEl = document.getElementById('alert-count');

        // Update count badge
        countEl.textContent = data.counts.total;
        countEl.className = 'alert-count';
        if (data.counts.P1 > 0) {
            countEl.classList.add('has-p1');
        } else if (data.counts.P2 > 0) {
            countEl.classList.add('has-p2');
        }

        // Render alerts
        if (data.alerts.length === 0) {
            container.innerHTML = '<div class="no-alerts">No active alerts</div>';
            return;
        }

        container.innerHTML = data.alerts.map(alert => `
            <div class="alert-card ${alert.priority.toLowerCase()}">
                <div class="alert-header">
                    <span class="alert-badge ${alert.priority.toLowerCase()}">${alert.priority}</span>
                    <span class="alert-type">${this.formatAlertType(alert.alert_type)}</span>
                </div>
                <div class="alert-details">
                    ${alert.instrument || ''} | ${alert.trigger_value || '--'} ${alert.trigger_metric || ''}
                    ${alert.zscore_value ? ` | Z: ${parseFloat(alert.zscore_value).toFixed(1)}` : ''}
                </div>
                <div class="alert-duration">
                    Duration: ${this.formatDuration(alert.duration_seconds)}
                </div>
            </div>
        `).join('');
    },

    // =========================================================================
    // CHARTS
    // =========================================================================

    /**
     * Load chart data from REST API.
     */
    async loadChartData() {
        const showThresholds = document.getElementById('spread-show-thresholds')?.checked ?? true;
        const showZscore = document.getElementById('spread-show-zscore')?.checked ?? false;

        // Spread chart
        const spreadData = await DashboardAPI.getAllExchangeMetrics(
            'spread',
            this.state.instrument,
            this.state.timeRange
        );

        let binanceSpread = spreadData.binance || [];
        let okxSpread = spreadData.okx || [];

        // Filter by exchange selection
        if (this.state.exchange === 'binance') {
            okxSpread = [];
        } else if (this.state.exchange === 'okx') {
            binanceSpread = [];
        }

        DashboardCharts.renderSpreadChart('spread-chart', binanceSpread, okxSpread, {
            showThresholds: showThresholds,
            showZscore: showZscore
        });

        // Basis chart
        const basisShowThresholds = document.getElementById('basis-show-thresholds')?.checked ?? true;
        const basisShowZscore = document.getElementById('basis-show-zscore')?.checked ?? false;

        const basisData = await DashboardAPI.getAllExchangeMetrics(
            'basis',
            this.state.instrument,
            this.state.timeRange
        );

        let binanceBasis = basisData.binance || [];
        let okxBasis = basisData.okx || [];

        if (this.state.exchange === 'binance') {
            okxBasis = [];
        } else if (this.state.exchange === 'okx') {
            binanceBasis = [];
        }

        // Update basis metric in Current State panel from latest fetched data
        const latestBasis = (binanceBasis.length > 0 ? binanceBasis : okxBasis);
        if (latestBasis.length > 0) {
            const last = latestBasis[latestBasis.length - 1];
            const basisVal = last.value ? parseFloat(last.value) : null;
            document.getElementById('metric-basis').textContent =
                basisVal !== null ? basisVal.toFixed(2) : '--';
            const basisZscoreEl = document.getElementById('zscore-basis');
            if (last.zscore) {
                const zs = parseFloat(last.zscore);
                basisZscoreEl.className = 'zscore-row ' + (Math.abs(zs) > 3 ? 'critical' : Math.abs(zs) > 2 ? 'warning' : 'active');
                basisZscoreEl.textContent = 'Z-Score: ' + zs.toFixed(1);
            } else {
                basisZscoreEl.className = 'zscore-row warming';
                basisZscoreEl.textContent = 'Z-Score: warming';
            }
        }

        DashboardCharts.renderBasisChart('basis-chart', binanceBasis, okxBasis, {
            showThresholds: basisShowThresholds,
            showZscore: basisShowZscore
        });

        // Depth chart (uses current data from selected exchange)
        const depthExchange = document.getElementById('depth-exchange-select')?.value || 'binance';
        await this.loadDepthData(depthExchange);
    },

    /**
     * Load depth data for a specific exchange.
     * @param {string} exchange - Exchange identifier
     */
    async loadDepthData(exchange) {
        const stateData = await DashboardAPI.getState(exchange, this.state.instrument);

        if (stateData) {
            DashboardCharts.renderDepthChart('depth-chart', stateData);

            // Update imbalance display
            const imbalance = stateData.imbalance ? parseFloat(stateData.imbalance) : null;
            if (imbalance !== null) {
                document.getElementById('depth-imbalance').textContent = imbalance.toFixed(3);
                const direction = imbalance > 0 ? 'Bid Heavy' : imbalance < 0 ? 'Ask Heavy' : 'Balanced';
                const dirEl = document.getElementById('depth-direction');
                dirEl.textContent = direction;
                dirEl.className = 'direction ' + (imbalance > 0 ? 'bid' : imbalance < 0 ? 'ask' : '');
            }
        } else {
            DashboardCharts.renderEmptyChart('depth-chart', 'No depth data available');
        }
    },

    // =========================================================================
    // CROSS-EXCHANGE
    // =========================================================================

    /**
     * Load cross-exchange comparison data.
     */
    async loadCrossExchangeData() {
        const data = await DashboardAPI.getCrossExchange(this.state.instrument);

        if (!data) return;

        // Binance
        if (data.binance) {
            document.getElementById('binance-price').textContent =
                data.binance.mid_price ? '$' + parseFloat(data.binance.mid_price).toFixed(2) : '$--';
            document.getElementById('binance-spread').textContent =
                data.binance.spread_bps ? parseFloat(data.binance.spread_bps).toFixed(2) + ' bps' : '-- bps';
        }

        // OKX
        if (data.okx) {
            document.getElementById('okx-price').textContent =
                data.okx.mid_price ? '$' + parseFloat(data.okx.mid_price).toFixed(2) : '$--';
            document.getElementById('okx-spread').textContent =
                data.okx.spread_bps ? parseFloat(data.okx.spread_bps).toFixed(2) + ' bps' : '-- bps';
        }

        // Divergence
        if (data.divergence_bps) {
            const divergence = Math.abs(parseFloat(data.divergence_bps));
            const divEl = document.getElementById('divergence-value');
            divEl.textContent = divergence.toFixed(2) + ' bps';
            divEl.className = 'value ' + this.getStatus(
                divergence,
                this.thresholds.divergence.warning,
                this.thresholds.divergence.critical
            );
        }

        if (data.price_diff) {
            document.getElementById('price-diff').textContent =
                '$' + parseFloat(data.price_diff).toFixed(2);
        }
    },

    // =========================================================================
    // HEALTH
    // =========================================================================

    /**
     * Load health data from REST API.
     */
    async loadHealthData() {
        const data = await DashboardAPI.getHealth();

        if (data) {
            this.updateHealthUI(data);
        }
    },

    /**
     * Handle WebSocket health update.
     */
    handleHealthUpdate(data) {
        // Health updates come with exchanges object
        this.updateHealthUIFromWS(data.exchanges);
    },

    /**
     * Update health UI from REST API data.
     * @param {Object} data - Health data
     */
    updateHealthUI(data) {
        // Overall status
        const overallEl = document.getElementById('overall-status');
        overallEl.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
        overallEl.className = 'overall-status ' + data.status;

        // Exchanges
        for (const [exchange, health] of Object.entries(data.exchanges || {})) {
            this.updateExchangeHealth(exchange, health);
        }

        // Infrastructure
        document.getElementById('health-redis').textContent = data.infrastructure?.redis || '--';
        document.getElementById('health-redis').className =
            'value ' + (data.infrastructure?.redis === 'connected' ? 'connected' : 'disconnected');

        document.getElementById('health-postgres').textContent = data.infrastructure?.postgres || '--';
        document.getElementById('health-postgres').className =
            'value ' + (data.infrastructure?.postgres === 'connected' ? 'connected' : 'disconnected');

        // Uptime
        document.getElementById('health-uptime').textContent = this.formatDuration(data.uptime_seconds);
    },

    /**
     * Update health UI from WebSocket data.
     * @param {Object} exchanges - Exchange health data
     */
    updateHealthUIFromWS(exchanges) {
        for (const [exchange, health] of Object.entries(exchanges || {})) {
            this.updateExchangeHealth(exchange, health);
        }
    },

    /**
     * Update single exchange health display.
     * @param {string} exchange - Exchange name
     * @param {Object} health - Health data
     */
    updateExchangeHealth(exchange, health) {
        const statusEl = document.getElementById(`health-status-${exchange}`);
        if (statusEl) {
            const status = health.status || 'unknown';
            statusEl.className = 'health-status ' + status;
        }

        const lagEl = document.getElementById(`health-lag-${exchange}`);
        if (lagEl) {
            lagEl.textContent = health.lag_ms ? `${health.lag_ms} ms` : '-- ms';
        }

        const rateEl = document.getElementById(`health-rate-${exchange}`);
        if (rateEl) {
            rateEl.textContent = health.message_rate ? `${health.message_rate}/min` : '--/min';
        }

        const gapsEl = document.getElementById(`health-gaps-${exchange}`);
        if (gapsEl) {
            gapsEl.textContent = health.gaps_1h ?? '--';
        }
    },

    // =========================================================================
    // UTILITIES
    // =========================================================================

    /**
     * Get status class based on thresholds.
     * @param {number} value - Current value
     * @param {number} warningThreshold - Warning threshold
     * @param {number} criticalThreshold - Critical threshold
     * @param {boolean} lowerIsWorse - If true, lower values are worse
     * @returns {string} Status class
     */
    getStatus(value, warningThreshold, criticalThreshold, lowerIsWorse = false) {
        if (value === null || value === undefined || isNaN(value)) {
            return 'unavailable';
        }

        if (lowerIsWorse) {
            if (value <= criticalThreshold) return 'critical';
            if (value <= warningThreshold) return 'warning';
            return 'normal';
        } else {
            if (value >= criticalThreshold) return 'critical';
            if (value >= warningThreshold) return 'warning';
            return 'normal';
        }
    },

    /**
     * Set status indicator element class.
     * @param {string} elementId - Element ID
     * @param {string} status - Status class
     */
    setStatusIndicator(elementId, status) {
        const el = document.getElementById(elementId);
        if (el) {
            el.className = 'status-indicator ' + status;
        }
    },

    /**
     * Format alert type for display.
     * @param {string} alertType - Alert type string
     * @returns {string} Formatted alert type
     */
    formatAlertType(alertType) {
        return alertType
            .replace(/_/g, ' ')
            .replace(/\b\w/g, c => c.toUpperCase());
    },

    /**
     * Format duration in seconds to human-readable string.
     * @param {number} seconds - Duration in seconds
     * @returns {string} Formatted duration
     */
    formatDuration(seconds) {
        if (!seconds || seconds < 0) return '--';

        if (seconds < 60) {
            return `${Math.floor(seconds)}s`;
        }
        if (seconds < 3600) {
            const minutes = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${minutes}m ${secs}s`;
        }
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        return `${hours}h ${minutes}m`;
    }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    DashboardApp.init();
});

// Make app available globally for debugging
window.DashboardApp = DashboardApp;
