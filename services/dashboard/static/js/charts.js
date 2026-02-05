/**
 * Chart rendering functions using Plotly.js
 *
 * Provides:
 * - Spread time series chart
 * - Basis time series chart
 * - Depth bar chart
 */

const DashboardCharts = {
    // Plotly layout defaults for dark theme
    darkLayout: {
        paper_bgcolor: '#16213e',
        plot_bgcolor: '#16213e',
        font: {
            color: '#adb5bd',
            size: 11
        },
        margin: { t: 30, r: 30, b: 40, l: 50 },
        xaxis: {
            gridcolor: '#0f3460',
            linecolor: '#0f3460',
            tickformat: '%H:%M'
        },
        yaxis: {
            gridcolor: '#0f3460',
            linecolor: '#0f3460'
        },
        legend: {
            orientation: 'h',
            yanchor: 'top',
            y: 1.1,
            xanchor: 'left',
            x: 0
        }
    },

    // Config options for Plotly
    plotConfig: {
        responsive: true,
        displayModeBar: false
    },

    // Threshold values (from PRD)
    thresholds: {
        spread: { warning: 3.0, critical: 5.0 },
        basis: { warning: 10.0, critical: 20.0 }
    },

    /**
     * Render spread time series chart.
     * @param {string} elementId - DOM element ID
     * @param {Array} binanceData - Binance data points
     * @param {Array} okxData - OKX data points
     * @param {Object} options - Chart options
     */
    renderSpreadChart(elementId, binanceData, okxData, options = {}) {
        const traces = [];

        // Binance trace
        if (binanceData && binanceData.length > 0) {
            traces.push({
                x: binanceData.map(d => d.timestamp),
                y: binanceData.map(d => parseFloat(d.value) || null),
                type: 'scatter',
                mode: 'lines',
                name: 'Binance',
                line: { color: '#3498db', width: 2 }
            });
        }

        // OKX trace
        if (okxData && okxData.length > 0) {
            traces.push({
                x: okxData.map(d => d.timestamp),
                y: okxData.map(d => parseFloat(d.value) || null),
                type: 'scatter',
                mode: 'lines',
                name: 'OKX',
                line: { color: '#e67e22', width: 2 }
            });
        }

        // Add threshold lines if enabled
        if (options.showThresholds && traces.length > 0) {
            const allTimestamps = [
                ...(binanceData || []).map(d => d.timestamp),
                ...(okxData || []).map(d => d.timestamp)
            ].sort();

            if (allTimestamps.length > 0) {
                const firstTs = allTimestamps[0];
                const lastTs = allTimestamps[allTimestamps.length - 1];

                // Warning threshold
                traces.push({
                    x: [firstTs, lastTs],
                    y: [this.thresholds.spread.warning, this.thresholds.spread.warning],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Warning',
                    line: { color: '#ffc107', width: 1, dash: 'dash' }
                });

                // Critical threshold
                traces.push({
                    x: [firstTs, lastTs],
                    y: [this.thresholds.spread.critical, this.thresholds.spread.critical],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Critical',
                    line: { color: '#dc3545', width: 1, dash: 'dash' }
                });
            }
        }

        // Layout
        const layout = {
            ...this.darkLayout,
            yaxis: {
                ...this.darkLayout.yaxis,
                title: 'Spread (bps)',
                rangemode: 'tozero'
            }
        };

        // If no data, show empty chart
        if (traces.length === 0) {
            traces.push({
                x: [],
                y: [],
                type: 'scatter',
                mode: 'lines',
                name: 'No Data'
            });
        }

        Plotly.newPlot(elementId, traces, layout, this.plotConfig);
    },

    /**
     * Render basis time series chart.
     * @param {string} elementId - DOM element ID
     * @param {Array} binanceData - Binance data points
     * @param {Array} okxData - OKX data points
     * @param {Object} options - Chart options
     */
    renderBasisChart(elementId, binanceData, okxData, options = {}) {
        const traces = [];

        // Binance trace
        if (binanceData && binanceData.length > 0) {
            traces.push({
                x: binanceData.map(d => d.timestamp),
                y: binanceData.map(d => parseFloat(d.value) || null),
                type: 'scatter',
                mode: 'lines',
                name: 'Binance',
                line: { color: '#3498db', width: 2 }
            });
        }

        // OKX trace
        if (okxData && okxData.length > 0) {
            traces.push({
                x: okxData.map(d => d.timestamp),
                y: okxData.map(d => parseFloat(d.value) || null),
                type: 'scatter',
                mode: 'lines',
                name: 'OKX',
                line: { color: '#e67e22', width: 2 }
            });
        }

        // Add threshold lines if enabled
        if (options.showThresholds && traces.length > 0) {
            const allTimestamps = [
                ...(binanceData || []).map(d => d.timestamp),
                ...(okxData || []).map(d => d.timestamp)
            ].sort();

            if (allTimestamps.length > 0) {
                const firstTs = allTimestamps[0];
                const lastTs = allTimestamps[allTimestamps.length - 1];

                // Warning thresholds (positive and negative for basis)
                traces.push({
                    x: [firstTs, lastTs],
                    y: [this.thresholds.basis.warning, this.thresholds.basis.warning],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Warning (+)',
                    line: { color: '#ffc107', width: 1, dash: 'dash' },
                    showlegend: false
                });
                traces.push({
                    x: [firstTs, lastTs],
                    y: [-this.thresholds.basis.warning, -this.thresholds.basis.warning],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Warning (-)',
                    line: { color: '#ffc107', width: 1, dash: 'dash' }
                });

                // Critical thresholds
                traces.push({
                    x: [firstTs, lastTs],
                    y: [this.thresholds.basis.critical, this.thresholds.basis.critical],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Critical (+)',
                    line: { color: '#dc3545', width: 1, dash: 'dash' },
                    showlegend: false
                });
                traces.push({
                    x: [firstTs, lastTs],
                    y: [-this.thresholds.basis.critical, -this.thresholds.basis.critical],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Critical (-)',
                    line: { color: '#dc3545', width: 1, dash: 'dash' }
                });

                // Zero line
                traces.push({
                    x: [firstTs, lastTs],
                    y: [0, 0],
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Zero',
                    line: { color: '#6c757d', width: 1, dash: 'dot' },
                    showlegend: false
                });
            }
        }

        // Layout
        const layout = {
            ...this.darkLayout,
            yaxis: {
                ...this.darkLayout.yaxis,
                title: 'Basis (bps)'
            }
        };

        // If no data, show empty chart
        if (traces.length === 0) {
            traces.push({
                x: [],
                y: [],
                type: 'scatter',
                mode: 'lines',
                name: 'No Data'
            });
        }

        Plotly.newPlot(elementId, traces, layout, this.plotConfig);
    },

    /**
     * Render depth bar chart.
     * @param {string} elementId - DOM element ID
     * @param {Object} data - Depth data at various levels
     */
    renderDepthChart(elementId, data) {
        const levels = ['5 bps', '10 bps', '25 bps'];
        const bidDepths = [
            parseFloat(data.depth_5bps_bid) / 1e6 || 0,
            parseFloat(data.depth_10bps_bid) / 1e6 || 0,
            parseFloat(data.depth_25bps_bid) / 1e6 || 0
        ];
        const askDepths = [
            parseFloat(data.depth_5bps_ask) / 1e6 || 0,
            parseFloat(data.depth_10bps_ask) / 1e6 || 0,
            parseFloat(data.depth_25bps_ask) / 1e6 || 0
        ];

        const traces = [
            {
                x: levels,
                y: bidDepths.map(d => -d),  // Negative for left side
                type: 'bar',
                name: 'Bid',
                marker: { color: '#28a745' },
                orientation: 'v',
                hovertemplate: 'Bid: $%{customdata:.2f}M<extra></extra>',
                customdata: bidDepths
            },
            {
                x: levels,
                y: askDepths,
                type: 'bar',
                name: 'Ask',
                marker: { color: '#dc3545' },
                orientation: 'v',
                hovertemplate: 'Ask: $%{y:.2f}M<extra></extra>'
            }
        ];

        const maxDepth = Math.max(...bidDepths, ...askDepths, 1) * 1.1;

        const layout = {
            ...this.darkLayout,
            barmode: 'overlay',
            bargap: 0.3,
            xaxis: {
                ...this.darkLayout.xaxis,
                type: 'category',
                tickformat: null,
                title: 'Distance from Mid'
            },
            yaxis: {
                ...this.darkLayout.yaxis,
                title: 'Depth ($M)',
                range: [-maxDepth, maxDepth],
                tickformat: '.1f'
            },
            showlegend: true
        };

        Plotly.newPlot(elementId, traces, layout, this.plotConfig);
    },

    /**
     * Render empty chart with message.
     * @param {string} elementId - DOM element ID
     * @param {string} message - Message to display
     */
    renderEmptyChart(elementId, message = 'No data available') {
        const layout = {
            ...this.darkLayout,
            annotations: [{
                text: message,
                showarrow: false,
                font: { size: 14, color: '#6c757d' }
            }],
            xaxis: { ...this.darkLayout.xaxis, visible: false },
            yaxis: { ...this.darkLayout.yaxis, visible: false }
        };

        Plotly.newPlot(elementId, [], layout, this.plotConfig);
    },

    /**
     * Update chart data without full redraw.
     * @param {string} elementId - DOM element ID
     * @param {Array} data - New data
     * @param {number} traceIndex - Trace index to update
     */
    updateChartData(elementId, data, traceIndex = 0) {
        Plotly.extendTraces(elementId, {
            x: [[data.timestamp]],
            y: [[parseFloat(data.value) || null]]
        }, [traceIndex]);
    }
};

// Make charts available globally
window.DashboardCharts = DashboardCharts;
