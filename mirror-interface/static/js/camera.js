/**
 * camera.js
 * Camera capture via getUserMedia + frame streaming to backend
 * Mirror Mirror Installation
 */

(function (global) {
  'use strict';

  const CAPTURE_W      = 320;
  const CAPTURE_H      = 240;
  const PREVIEW_W      = 120;
  const PREVIEW_H      = 90;
  const FRAME_INTERVAL = 500;  // ms between frames sent to backend
  const JPEG_QUALITY   = 0.72;

  /* ── Hidden capture elements ──────────────────────────── */
  let _video      = null;  // <video> element (hidden)
  let _captureCanvas = null;  // offscreen canvas at 320x240
  let _captureCtx = null;

  /* ── Preview canvas (visible, bottom corner) ──────────── */
  const _previewCanvas = document.getElementById('camera-preview');
  let   _previewCtx    = null;

  /* ── State ────────────────────────────────────────────── */
  let _stream        = null;
  let _frameTimer    = null;
  let _running       = false;
  let _frameCount    = 0;
  let _lastFpsTime   = Date.now();
  let _fps           = 0;

  /* ── Internal helpers ─────────────────────────────────── */

  function _createHiddenVideo() {
    const v = document.createElement('video');
    v.setAttribute('playsinline', '');
    v.setAttribute('autoplay', '');
    v.setAttribute('muted', '');
    v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;top:0;left:0;pointer-events:none;';
    document.body.appendChild(v);
    return v;
  }

  function _initCanvases() {
    _captureCanvas = document.createElement('canvas');
    _captureCanvas.width  = CAPTURE_W;
    _captureCanvas.height = CAPTURE_H;
    _captureCtx = _captureCanvas.getContext('2d');

    if (_previewCanvas) {
      _previewCanvas.width  = PREVIEW_W;
      _previewCanvas.height = PREVIEW_H;
      _previewCtx = _previewCanvas.getContext('2d');
    }
  }

  function _captureFrame() {
    if (!_video || _video.readyState < 2 || !_captureCtx) return null;

    /* Draw to capture canvas (downscaled) */
    _captureCtx.drawImage(_video, 0, 0, CAPTURE_W, CAPTURE_H);

    /* Draw to preview (ghostly) */
    if (_previewCtx) {
      _previewCtx.drawImage(_video, 0, 0, PREVIEW_W, PREVIEW_H);
    }

    /* Export as JPEG base64 */
    const dataURL = _captureCanvas.toDataURL('image/jpeg', JPEG_QUALITY);
    /* Strip header "data:image/jpeg;base64," */
    return dataURL.split(',')[1];
  }

  function _tick() {
    const b64 = _captureFrame();
    if (b64 && global.SocketClient && global.SocketClient.connected) {
      global.SocketClient.sendFrame(b64);
    }

    /* FPS calc */
    _frameCount++;
    const now = Date.now();
    if (now - _lastFpsTime >= 2000) {
      _fps = (_frameCount / ((now - _lastFpsTime) / 1000)).toFixed(1);
      _frameCount  = 0;
      _lastFpsTime = now;
    }
  }

  /* ── Public API ───────────────────────────────────────── */
  const CameraManager = {

    /**
     * Request camera access and start streaming frames.
     * @returns {Promise<void>}
     */
    async start() {
      if (_running) return;

      _video = _createHiddenVideo();
      _initCanvases();

      try {
        _stream = await navigator.mediaDevices.getUserMedia({
          video: {
            width:  { ideal: 640 },
            height: { ideal: 480 },
            facingMode: 'user',
          },
          audio: false,
        });

        _video.srcObject = _stream;
        await _video.play();
        _running = true;

        /* Update status dot */
        const dot = document.getElementById('camera-dot');
        if (dot) dot.classList.add('active');

        /* Start frame capture loop */
        _frameTimer = setInterval(_tick, FRAME_INTERVAL);

        console.log('[Camera] Started. Track:', _stream.getVideoTracks()[0]?.label);
      } catch (err) {
        console.error('[Camera] getUserMedia failed:', err);
        /* Update dot to error state */
        const dot = document.getElementById('camera-dot');
        if (dot) dot.classList.add('error');
        throw err;
      }
    },

    /**
     * Stop camera and release resources.
     */
    stop() {
      if (_frameTimer) {
        clearInterval(_frameTimer);
        _frameTimer = null;
      }
      if (_stream) {
        _stream.getTracks().forEach(t => t.stop());
        _stream = null;
      }
      if (_video) {
        _video.srcObject = null;
        _video.remove();
        _video = null;
      }
      _running = false;

      const dot = document.getElementById('camera-dot');
      if (dot) {
        dot.classList.remove('active');
        dot.classList.remove('error');
      }
      console.log('[Camera] Stopped.');
    },

    /**
     * Return current FPS string for debug display.
     */
    getFps() {
      return _running ? _fps + ' fps' : 'stopped';
    },

    /**
     * Capture a single frame and return base64 JPEG (no emit).
     * Useful for transition phase snapshot.
     * @returns {string|null}
     */
    snapshot() {
      return _captureFrame();
    },

    /**
     * Returns the hidden video element (for canvas compositing in transition).
     */
    getVideoElement() {
      return _video;
    },

    isRunning() {
      return _running;
    },
  };

  global.CameraManager = CameraManager;

})(window);
