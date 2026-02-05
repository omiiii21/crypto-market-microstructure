/**
 * REST API client for the dashboard.
 *
 * Provides functions for fetching data from the FastAPI backend:
 * - Current state
 * - Active alerts
 * - Historical metrics
 * - System health
 */

const DashboardAPI = {
    baseUrl: '',

    /**
     * Fetch current state for an exchange/instrument.
     * @param {string} exchange - Exchange identifier
     * @param {string} instrument - Instrument identifier
     * @returns {Promise<Object>} Current state data
     */
    async getState(exchange, instrument) {
        try {
            const response = await fetch(`${this.baseUrl}/api/state/${exchange}/${instrument}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getState):', error);
            return null;
        }
    },

    /**
     * Fetch cross-exchange comparison data.
     * @param {string} instrument - Instrument identifier
     * @returns {Promise<Object>} Cross-exchange data
     */
    async getCrossExchange(instrument) {
        try {
            const response = await fetch(`${this.baseUrl}/api/state/cross-exchange/${instrument}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getCrossExchange):', error);
            return null;
        }
    },

    /**
     * Fetch active alerts.
     * @param {Object} options - Filter options
     * @returns {Promise<Object>} Alerts data
     */
    async getAlerts(options = {}) {
        try {
            const params = new URLSearchParams();
            if (options.status) params.append('status', options.status);
            if (options.priority) params.append('priority', options.priority);
            if (options.exchange) params.append('exchange', options.exchange);
            if (options.instrument) params.append('instrument', options.instrument);

            const url = `${this.baseUrl}/api/alerts?${params.toString()}`;
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getAlerts):', error);
            return { alerts: [], counts: { P1: 0, P2: 0, P3: 0, total: 0 } };
        }
    },

    /**
     * Fetch historical metrics.
     * @param {string} metricType - Metric type (spread, basis, depth)
     * @param {string} exchange - Exchange identifier
     * @param {string} instrument - Instrument identifier
     * @param {string} timeRange - Time range (5m, 15m, 1h, 4h, 24h)
     * @returns {Promise<Object>} Historical metrics data
     */
    async getMetrics(metricType, exchange, instrument, timeRange) {
        try {
            const url = `${this.baseUrl}/api/metrics/${metricType}/${exchange}/${instrument}?time_range=${timeRange}`;
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getMetrics):', error);
            return { data: [] };
        }
    },

    /**
     * Fetch historical metrics from all exchanges.
     * @param {string} metricType - Metric type (spread, basis, depth)
     * @param {string} instrument - Instrument identifier
     * @param {string} timeRange - Time range
     * @returns {Promise<Object>} Multi-exchange metrics data
     */
    async getAllExchangeMetrics(metricType, instrument, timeRange) {
        try {
            const url = `${this.baseUrl}/api/metrics/${metricType}/all/${instrument}?time_range=${timeRange}`;
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getAllExchangeMetrics):', error);
            return { binance: [], okx: [] };
        }
    },

    /**
     * Fetch system health status.
     * @returns {Promise<Object>} Health data
     */
    async getHealth() {
        try {
            const response = await fetch(`${this.baseUrl}/api/health`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getHealth):', error);
            return null;
        }
    },

    /**
     * Fetch detailed health check.
     * @returns {Promise<Object>} Detailed health data
     */
    async getDetailedHealth() {
        try {
            const response = await fetch(`${this.baseUrl}/api/health/detailed`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error (getDetailedHealth):', error);
            return null;
        }
    }
};

// Make API available globally
window.DashboardAPI = DashboardAPI;
