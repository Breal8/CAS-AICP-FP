/**
 * transition.js
 * 4-Phase Canvas Transition Engine
 * Mirror Mirror Installation
 *
 * Phase 1 — DISSOLUTION   (6s):  Camera feed blurs + grain noise builds up
 * Phase 2 — INTERPRETATION (7s+, elastic): Color field + floating words
 * Phase 3 — CRYSTALLIZATION (5s): Portrait emerges from blur
 * Phase 4 — REVEAL         (2s): Snap to clarity + white flash
 */

(function (global) {
  'use strict';

  /* ── Phase durations (ms) ─────────────────────────────── */
  const PHASE_DURATION = {
    dissolution:     6000,
    interpretation:  7000,   // elastic — extended if portrait not yet ready
    crystallization: 5000,
    reveal:          2000,
  };

  /* Band color palettes (RGBA arrays) */
  const BAND_COLORS = [
    [[26, 14, 46], [10, 8, 20]],    // 0 violet
    [[14, 26, 46], [8, 10, 20]],    // 1 indigo
    [[14, 42, 26], [8, 14, 12]],    // 2 teal
    [[42, 26, 14], [20, 12, 8]],    // 3 amber
    [[46, 14, 26], [20, 8, 14]],    // 4 crimson
    [[26, 26, 14], [12, 14, 8]],    // 5 olive
  ];

  /* Emotional words per emotion */
  const EMOTION_WORDS = {
    happy:    ['LIGHT', 'WARMTH', 'JOY', 'RADIANCE', 'BLOOM', 'HOPE'],
    sad:      ['WEIGHT', 'DUSK', 'LONGING', 'STILL', 'GREY', 'ABSENCE'],
    angry:    ['TENSION', 'FRACTURE', 'BURN', 'EDGE', 'STORM', 'SHATTER'],
    fear:     ['SHADOW', 'HOLLOW', 'TREMOR', 'VOID', 'SILENT', 'THRESHOLD'],
    surprise: ['WONDER', 'BREACH', 'SUDDEN', 'FLASH', 'SHIFT', 'OPEN'],
    disgust:  ['RESIST', 'RECOIL', 'DISTANCE', 'VEIL', 'TURN', 'HOLLOW'],
    neutral:  ['STILL', 'SURFACE', 'THRESHOLD', 'BETWEEN', 'WAIT', 'MIRROR'],
  };

  /* ── State ────────────────────────────────────────────── */
  let _canvas    = null;
  let _ctx       = null;
  let _phase     = null;       // current phase name
  let _phaseStart = 0;         // timestamp phase began
  let _rafId     = null;
  let _band      = 0;
  let _emotion   = 'neutral';
  let _portraitReady = false;  // flag from backend
  let _portraitUrl   = null;
  let _portraitImg   = null;   // loaded Image for phase 3
  let _onCompleteCallback = null;

  /* Floating words state */
  const _floatingWords = [];

  function _initFloatingWords() {
    _floatingWords.length = 0;
    const words = EMOTION_WORDS[_emotion] || EMOTION_WORDS.neutral;
    const W = _canvas.width, H = _canvas.height;

    for (let i = 0; i < 8; i++) {
      _floatingWords.push({
        text:    words[i % words.length],
        x:       Math.random() * W,
        y:       Math.random() * H,
        vx:      (Math.random() - 0.5) * 0.35,
        vy:      (Math.random() - 0.5) * 0.18,
        alpha:   0,
        targetA: Math.random() * 0.25 + 0.05,
        size:    Math.random() * 14 + 10,
        delay:   Math.random() * 1200,
        born:    0,
      });
    }
  }

  /* ── Helpers ──────────────────────────────────────────── */

  function _lerp(a, b, t) { return a + (b - a) * t; }

  function _easeInOut(t) { return t < 0.5 ? 2*t*t : -1+(4-2*t)*t; }

  function _elapsed() { return performance.now() - _phaseStart; }

  function _bandColors() {
    return BAND_COLORS[_band] || BAND_COLORS[0];
  }

  /* ── Phase Renderers ──────────────────────────────────── */

  function _renderDissolution() {
    const W = _canvas.width, H = _canvas.height;
    const t = Math.min(_elapsed() / PHASE_DURATION.dissolution, 1);
    const eased = _easeInOut(t);

    /* Draw live camera frame if available */
    const video = global.CameraManager && global.CameraManager.getVideoElement();
    if (video && video.readyState >= 2) {
      _ctx.filter = `blur(${_lerp(0, 22, eased)}px) grayscale(${_lerp(0, 0.85, eased)}) brightness(${_lerp(1, 0.5, eased)})`;
      _ctx.drawImage(video, 0, 0, W, H);
      _ctx.filter = 'none';
    } else {
      /* Fallback: black with growing noise */
      _ctx.fillStyle = `rgba(0,0,0,${_lerp(0.4, 0.95, eased)})`;
      _ctx.fillRect(0, 0, W, H);
    }

    /* Add grain overlay (noise using random rects) */
    const grainIntensity = _lerp(0, 0.18, eased);
    if (grainIntensity > 0.02) {
      _ctx.save();
      for (let i = 0; i < 1800; i++) {
        const gx = Math.random() * W;
        const gy = Math.random() * H;
        const gs = Math.random() * 2.5;
        const ga = Math.random() * grainIntensity;
        _ctx.fillStyle = `rgba(200,210,215,${ga})`;
        _ctx.fillRect(gx, gy, gs, gs);
      }
      _ctx.restore();
    }

    /* Dark vignette building */
    const vigGrad = _ctx.createRadialGradient(W/2, H/2, H*0.1, W/2, H/2, H*0.85);
    vigGrad.addColorStop(0, 'rgba(0,0,0,0)');
    vigGrad.addColorStop(1, `rgba(0,0,0,${_lerp(0.2, 0.9, eased)})`);
    _ctx.fillStyle = vigGrad;
    _ctx.fillRect(0, 0, W, H);

    /* Phase complete */
    if (t >= 1) _startPhase('interpretation');
  }

  function _renderInterpretation() {
    const W = _canvas.width, H = _canvas.height;
    const now = performance.now();
    const elapsed = now - _phaseStart;
    const minDuration = PHASE_DURATION.interpretation;
    const t = Math.min(elapsed / minDuration, 1);

    const [col1, col2] = _bandColors();

    /* Animated color field */
    const wave = Math.sin(elapsed * 0.0008) * 0.5 + 0.5;
    const r = Math.round(_lerp(col2[0], col1[0], wave));
    const g = Math.round(_lerp(col2[1], col1[1], wave));
    const b = Math.round(_lerp(col2[2], col1[2], wave));

    /* Background fill */
    _ctx.fillStyle = `rgba(${r},${g},${b},0.92)`;
    _ctx.fillRect(0, 0, W, H);

    /* Gradient noise overlay */
    const grd = _ctx.createRadialGradient(W*0.4, H*0.45, 0, W*0.4, H*0.45, H*0.7);
    grd.addColorStop(0, `rgba(${col1[0]+20},${col1[1]+15},${col1[2]+30},0.4)`);
    grd.addColorStop(1, 'rgba(0,0,0,0.5)');
    _ctx.fillStyle = grd;
    _ctx.fillRect(0, 0, W, H);

    /* Floating words */
    _ctx.font = `300 12px 'Courier Prime', monospace`;
    _ctx.textAlign = 'center';
    _floatingWords.forEach((w, i) => {
      const age = elapsed - w.delay;
      if (age < 0) return;
      w.x  += w.vx;
      w.y  += w.vy;
      /* Wrap edges */
      if (w.x < -80) w.x = W + 80;
      if (w.x > W + 80) w.x = -80;
      if (w.y < -20) w.y = H + 20;
      if (w.y > H + 20) w.y = -20;

      /* Fade in */
      if (w.alpha < w.targetA) w.alpha = Math.min(w.alpha + 0.003, w.targetA);

      _ctx.globalAlpha = w.alpha;
      _ctx.font = `300 ${w.size}px 'Courier Prime', monospace`;
      _ctx.fillStyle = `rgba(200,210,220,1)`;
      _ctx.letterSpacing = '0.25em';
      _ctx.fillText(w.text, w.x, w.y);
    });
    _ctx.globalAlpha = 1;
    _ctx.textAlign = 'left';

    /* Scanline overlay */
    _ctx.fillStyle = 'rgba(0,0,0,0.08)';
    for (let y = 0; y < H; y += 6) {
      _ctx.fillRect(0, y + 5, W, 1);
    }

    /* Vignette */
    const vig = _ctx.createRadialGradient(W/2, H/2, H*0.15, W/2, H/2, H*0.9);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, 'rgba(0,0,0,0.7)');
    _ctx.fillStyle = vig;
    _ctx.fillRect(0, 0, W, H);

    /* Transition to crystallization when:
       - portrait is ready, or
       - minimum duration elapsed */
    if (_portraitReady && t > 0.25) {
      _startPhase('crystallization');
    } else if (t >= 1 && _portraitReady) {
      _startPhase('crystallization');
    }
  }

  function _renderCrystallization() {
    const W = _canvas.width, H = _canvas.height;
    const t = Math.min(_elapsed() / PHASE_DURATION.crystallization, 1);
    const eased = _easeInOut(t);

    /* Background — dark, de-saturated */
    _ctx.fillStyle = `rgba(5,5,8,0.95)`;
    _ctx.fillRect(0, 0, W, H);

    /* Portrait image (progressively un-blurring) */
    if (_portraitImg) {
      const blur = _lerp(22, 0, eased);
      const bright = _lerp(0.35, 1, eased);
      _ctx.filter = `blur(${blur.toFixed(1)}px) brightness(${bright.toFixed(2)})`;
      /* Center the portrait */
      const imgW = Math.min(W * 0.35, 360);
      const imgH = imgW * (3 / 2);
      const imgX = (W - imgW) / 2;
      const imgY = (H - imgH) / 2;
      _ctx.drawImage(_portraitImg, imgX, imgY, imgW, imgH);
      _ctx.filter = 'none';
    }

    /* Grain still present, fading */
    const grainA = _lerp(0.12, 0, eased);
    for (let i = 0; i < 800; i++) {
      _ctx.fillStyle = `rgba(200,210,215,${Math.random() * grainA})`;
      _ctx.fillRect(Math.random()*W, Math.random()*H, Math.random()*2+0.5, Math.random()*2+0.5);
    }

    /* Vignette */
    const vig = _ctx.createRadialGradient(W/2, H/2, H*0.2, W/2, H/2, H*0.85);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, `rgba(0,0,0,${_lerp(0.85, 0.55, eased)})`);
    _ctx.fillStyle = vig;
    _ctx.fillRect(0, 0, W, H);

    if (t >= 1) _startPhase('reveal');
  }

  function _renderReveal() {
    const W = _canvas.width, H = _canvas.height;
    const t = Math.min(_elapsed() / PHASE_DURATION.reveal, 1);

    /* Clear — show portrait sharply */
    _ctx.clearRect(0, 0, W, H);

    if (_portraitImg) {
      _ctx.filter = 'none';
      const imgW = Math.min(W * 0.35, 360);
      const imgH = imgW * (3 / 2);
      const imgX = (W - imgW) / 2;
      const imgY = (H - imgH) / 2;
      _ctx.drawImage(_portraitImg, imgX, imgY, imgW, imgH);
    }

    /* White flash pulse (peaks at t=0.15) */
    const flashAlpha = t < 0.15
      ? (t / 0.15) * 0.75
      : _lerp(0.75, 0, (t - 0.15) / 0.85);

    if (flashAlpha > 0) {
      _ctx.fillStyle = `rgba(255,255,255,${flashAlpha})`;
      _ctx.fillRect(0, 0, W, H);
    }

    if (t >= 1 && _phase === 'reveal') {
      /* Stop RAF and fire callback exactly once */
      _stopRaf();
      _phase = 'done';
      _canvas.style.transition = 'opacity 0.9s ease';
      _canvas.style.opacity = '0';
      setTimeout(() => {
        _canvas.classList.remove('active');
        _canvas.style.opacity = '';
        _canvas.style.transition = '';
        _phase = null;
        if (typeof _onCompleteCallback === 'function') {
          _onCompleteCallback(_portraitUrl);
        }
      }, 950);
    }
  }

  /* ── Phase Management ─────────────────────────────────── */

  function _startPhase(name) {
    _phase      = name;
    _phaseStart = performance.now();
    const dbg   = document.getElementById('dbg-phase');
    if (dbg) dbg.textContent = name.toUpperCase();
    console.log('[Transition] Phase:', name);

    if (name === 'interpretation') _initFloatingWords();
  }

  function _tick() {
    if (!_phase || !_ctx) return;

    switch (_phase) {
      case 'dissolution':     _renderDissolution();     break;
      case 'interpretation':  _renderInterpretation();  break;
      case 'crystallization': _renderCrystallization(); break;
      case 'reveal':          _renderReveal();          break;
    }

    _rafId = requestAnimationFrame(_tick);
  }

  function _stopRaf() {
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
  }

  function _resizeCanvas() {
    if (!_canvas) return;
    _canvas.width  = window.innerWidth;
    _canvas.height = window.innerHeight;
  }

  /* ── Public API ───────────────────────────────────────── */
  const TransitionEngine = {

    init() {
      _canvas = document.getElementById('transition-canvas');
      if (!_canvas) { console.error('[Transition] Canvas element not found'); return; }
      _ctx = _canvas.getContext('2d');
      _resizeCanvas();
      window.addEventListener('resize', _resizeCanvas);
    },

    /**
     * Start the 4-phase transition sequence.
     * @param {number} band            - LoRA band index (0-5)
     * @param {string} [emotion]       - Dominant emotion string
     * @param {Function} [onComplete]  - Called with portraitUrl when reveal finishes
     */
    start(band, emotion, onComplete) {
      _band          = band || 0;
      _emotion       = emotion || 'neutral';
      _portraitReady = false;
      _portraitUrl   = null;
      _portraitImg   = null;
      _onCompleteCallback = onComplete || null;

      _canvas.classList.add('active');
      _canvas.style.opacity = '1';

      _stopRaf();
      _startPhase('dissolution');
      _rafId = requestAnimationFrame(_tick);
    },

    /**
     * Called when backend signals portrait is ready.
     * Triggers elastic phase jump if in interpretation.
     * @param {string} portraitUrl
     */
    onPortraitReady(portraitUrl) {
      _portraitUrl   = portraitUrl;
      _portraitReady = true;

      console.log('[Transition] Portrait ready, loading image…');
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        _portraitImg = img;
        console.log('[Transition] Portrait image loaded.');
      };
      img.onerror = (e) => {
        console.error('[Transition] Portrait image load failed:', e);
      };
      img.src = portraitUrl;
    },

    /**
     * Cancel any running transition.
     */
    cancel() {
      _stopRaf();
      _phase = null;
      if (_canvas) {
        _canvas.classList.remove('active');
        _canvas.style.opacity = '0';
      }
    },

    isRunning() {
      return _phase !== null;
    },

    currentPhase() {
      return _phase;
    },
  };

  global.TransitionEngine = TransitionEngine;

})(window);
