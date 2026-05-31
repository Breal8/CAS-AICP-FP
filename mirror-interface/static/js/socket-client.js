/**
 * socket-client.js
 * WebSocket bridge (Socket.IO) — Mirror Mirror
 *
 * Emits:  frame, start_conversation, answer, print_poloroid
 * Listens: emotion_update, question, conversation_complete,
 *          portrait_ready, print_status, avatar_ready, stt_partial
 */

(function (global) {
  'use strict';

  /* ── Internal Event Bus ───────────────────────────────── */
  const _listeners = {};

  function _emit(event, data) {
    const handlers = _listeners[event];
    if (!handlers) return;
    handlers.forEach(fn => {
      try { fn(data); } catch (e) { console.error('[SocketClient] handler error:', e); }
    });
  }

  /* ── Public API ───────────────────────────────────────── */
  const SocketClient = {
    socket: null,
    connected: false,
    _frameQueue: [],
    _draining: false,

    /**
     * Connect to the Socket.IO server on the same origin.
     * @param {string} [url] - Optional override URL
     */
    connect(url) {
      if (this.socket) return;

      const target = url || window.location.origin;
      console.log('[SocketClient] Connecting to', target);

      this.socket = io(target, {
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 1500,
        reconnectionAttempts: Infinity,
      });

      /* Connection lifecycle */
      this.socket.on('connect', () => {
        this.connected = true;
        console.log('[SocketClient] Connected:', this.socket.id);
        _emit('connected', { id: this.socket.id });
      });

      this.socket.on('disconnect', (reason) => {
        this.connected = false;
        console.warn('[SocketClient] Disconnected:', reason);
        _emit('disconnected', { reason });
      });

      this.socket.on('connect_error', (err) => {
        console.warn('[SocketClient] Connection error:', err.message);
        _emit('error', { message: err.message });
      });

      /* Server → Client events */
      this.socket.on('emotion_update', (data) => {
        _emit('emotion_update', data);
      });

      this.socket.on('conversation_complete', (data) => {
        console.log('[SocketClient] conversation_complete:', data);
        _emit('conversation_complete', data);
      });

      this.socket.on('portrait_ready', (data) => {
        console.log('[SocketClient] portrait_ready:', data);
        _emit('portrait_ready', data);
      });

      this.socket.on('print_status', (data) => {
        console.log('[SocketClient] print_status:', data);
        _emit('print_status', data);
      });

    },

    /**
     * Subscribe to a client-side event.
     * @param {string} event
     * @param {Function} handler
     */
    on(event, handler) {
      if (!_listeners[event]) _listeners[event] = [];
      _listeners[event].push(handler);
    },

    /**
     * Remove a handler.
     */
    off(event, handler) {
      if (!_listeners[event]) return;
      _listeners[event] = _listeners[event].filter(fn => fn !== handler);
    },

    /* ── Emit helpers ─────────────────────────────────── */

    /**
     * Send a camera frame (base64 JPEG string).
     * Only used as fallback when Pi camera is unavailable.
     */
    sendFrame(base64Data) {
      if (!this.connected || !this.socket) return;
      this.socket.emit('frame', { image: base64Data, ts: Date.now() });
    },

    /**
     * Tell backend to begin the conversation sequence.
     */
    startConversation() {
      if (!this.connected) return;
      console.log('[SocketClient] → start_conversation');
      this.socket.emit('start_conversation', {});
    },

    /**
     * Send name + answers after the Runway avatar signals interview_complete.
     * @param {{ name: string, answers: string[] }} payload
     */
    sendInterviewComplete(payload) {
      if (!this.connected) return;
      console.log('[SocketClient] → interview_complete:', payload);
      this.socket.emit('interview_complete', payload);
    },

    /**
     * Trigger Polaroid print.
     * @param {Object} [opts]
     */
    printPolaroid(imageUrl, opts = {}) {
      if (!this.connected) return;
      const payload = imageUrl ? { ...opts, image_url: imageUrl } : opts;
      console.log('[SocketClient] → print_poloroid', payload);
      this.socket.emit('print_poloroid', payload);
    },

    /**
     * Return connection status string for debug display.
     */
    statusLabel() {
      if (!this.socket) return 'NOT INIT';
      if (this.connected) return 'CONNECTED (' + (this.socket.id || '—') + ')';
      return 'DISCONNECTED';
    },

    disconnect() {
      if (this.socket) {
        this.socket.disconnect();
        this.socket = null;
        this.connected = false;
      }
    },
  };

  global.SocketClient = SocketClient;

})(window);
