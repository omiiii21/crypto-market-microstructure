/**
 * WebSocket client for real-time dashboard updates.
 *
 * Handles:
 * - Connection management with auto-reconnection
 * - Subscription to channels (state, alerts, health)
 * - Message handling and callbacks
 */

const DashboardWebSocket = {
    socket: null,
    reconnectAttempts: 0,
    maxReconnectAttempts: 10,
    reconnectDelay: 1000,
    isConnected: false,
    callbacks: {
        onState: null,
        onAlerts: null,
        onHealth: null,
        onConnect: null,
        onDisconnect: null,
        onError: null
    },
    subscription: {
        channels: ['state', 'alerts', 'health'],
        exchanges: ['binance', 'okx'],
        instruments: ['BTC-USDT-PERP']
    },

    /**
     * Connect to the WebSocket server.
     */
    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/updates`;

        console.log('WebSocket connecting to:', wsUrl);

        try {
            this.socket = new WebSocket(wsUrl);

            this.socket.onopen = () => {
                console.log('WebSocket connected');
                this.isConnected = true;
                this.reconnectAttempts = 0;

                // Subscribe to channels
                this.subscribe();

                if (this.callbacks.onConnect) {
                    this.callbacks.onConnect();
                }
            };

            this.socket.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    this.handleMessage(message);
                } catch (error) {
                    console.error('WebSocket message parse error:', error);
                }
            };

            this.socket.onclose = (event) => {
                console.log('WebSocket disconnected:', event.code, event.reason);
                this.isConnected = false;

                if (this.callbacks.onDisconnect) {
                    this.callbacks.onDisconnect();
                }

                // Attempt to reconnect
                this.attemptReconnect();
            };

            this.socket.onerror = (error) => {
                console.error('WebSocket error:', error);

                if (this.callbacks.onError) {
                    this.callbacks.onError(error);
                }
            };

        } catch (error) {
            console.error('WebSocket connection failed:', error);
            this.attemptReconnect();
        }
    },

    /**
     * Disconnect from the WebSocket server.
     */
    disconnect() {
        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }
        this.isConnected = false;
    },

    /**
     * Attempt to reconnect to the server.
     */
    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('Max reconnection attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.min(this.reconnectAttempts, 5);

        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

        setTimeout(() => {
            if (!this.isConnected) {
                this.connect();
            }
        }, delay);
    },

    /**
     * Subscribe to channels.
     */
    subscribe() {
        if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
            console.warn('Cannot subscribe: WebSocket not connected');
            return;
        }

        const message = {
            action: 'subscribe',
            channels: this.subscription.channels,
            exchanges: this.subscription.exchanges,
            instruments: this.subscription.instruments
        };

        this.socket.send(JSON.stringify(message));
        console.log('Subscribed to:', message);
    },

    /**
     * Update subscription.
     * @param {Object} options - Subscription options
     */
    updateSubscription(options) {
        if (options.channels) this.subscription.channels = options.channels;
        if (options.exchanges) this.subscription.exchanges = options.exchanges;
        if (options.instruments) this.subscription.instruments = options.instruments;

        if (this.isConnected) {
            this.subscribe();
        }
    },

    /**
     * Handle incoming messages.
     * @param {Object} message - Parsed message
     */
    handleMessage(message) {
        const channel = message.channel;
        const data = message.data;

        switch (channel) {
            case 'state':
                if (this.callbacks.onState) {
                    this.callbacks.onState(data, message.exchange, message.instrument);
                }
                break;

            case 'alerts':
                if (this.callbacks.onAlerts) {
                    this.callbacks.onAlerts(data);
                }
                break;

            case 'health':
                if (this.callbacks.onHealth) {
                    this.callbacks.onHealth(data);
                }
                break;

            default:
                // Handle confirmation messages
                if (message.type === 'subscribed') {
                    console.log('Subscription confirmed:', message);
                } else if (message.type === 'pong') {
                    console.log('Pong received');
                } else if (message.type === 'error') {
                    console.error('Server error:', message.message);
                }
        }
    },

    /**
     * Send a ping to keep connection alive.
     */
    ping() {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify({ action: 'ping' }));
        }
    },

    /**
     * Set callback functions.
     * @param {Object} callbacks - Callback functions
     */
    setCallbacks(callbacks) {
        Object.assign(this.callbacks, callbacks);
    }
};

// Make WebSocket client available globally
window.DashboardWebSocket = DashboardWebSocket;
