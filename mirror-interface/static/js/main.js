/**
 * main.js
 * Main Orchestrator — Mirror Mirror Pi Installation
 *
 * Kiosk mode: auto-starts on load, no click needed.
 * Conversation driven by Runway GMW avatar video + Pi-native Vosk STT.
 *
 * State machine:
 *   idle → arrival → conversation → transition → reveal → exit → idle
 */

(function (global) {
  'use strict';

  /* ── State ────────────────────────────────────────────── */
  const STATES = {
    IDLE:         'idle',
    ARRIVAL:      'arrival',
    CONVERSATION: 'conversation',
    TRANSITION:   'transition',
    REVEAL:       'reveal',
    EXIT:         'exit',
  };

  let _state            = STATES.IDLE;
  let _revealTimer      = null;
  let _timeInterval     = null;
  let _debugVisible     = false;
  let _frameEmitPaused  = false;

  /* Last known values */
  let _lastBand          = 0;
  let _lastEmotion       = 'neutral';
  let _lastEmotions      = {};
  let _lastPortraitUrls  = [];

  /* Generation phrase cycling */
  const _GENERATION_PHRASES = [
    'Something of you stays behind.',
    'What you have left here will remain.',
    'The mirror remembers what you showed it.',
    'Your face is still forming in the dark.',
    'Every trace you leave becomes a portrait.',
    'Something passed between us. It is being kept.',
    'The system is reading what you left behind.',
    'You were here. That is being recorded.',
    'Your reflection is finding its shape.',
    'What remains of you is almost visible.',
    'The impression you made is still settling.',
    'A stranger stood here. The mirror did not forget.',
    'You gave something without meaning to.',
    'The image is arriving from somewhere inside you.',
    'Nothing is lost. It is only being translated.',
    'Your presence has already changed the room.',
    'Almost. The portrait finds its form.',
    'The machine is patient. It has time.',
    'You will not remember all of what you said.',
    'This is the part where you become an image.',
  ];
  let _phraseTimer = null;
  let _phraseIndex = 0;

  function _startGenerationPhrases() {
    _phraseIndex = 0;
    _showNextGenerationPhrase();
  }

  function _showNextGenerationPhrase() {
    const el = $('generation-text');
    if (!el || _state !== STATES.TRANSITION) return;
    const phrase = _GENERATION_PHRASES[_phraseIndex % _GENERATION_PHRASES.length];
    el.style.transition = 'opacity 0.8s';
    el.style.opacity = '0';
    setTimeout(() => {
      el.textContent = phrase;
      el.style.opacity = '1';
      _phraseIndex++;
      _phraseTimer = setTimeout(_showNextGenerationPhrase, 6000);
    }, 800);
  }

  function _stopGenerationPhrases() {
    if (_phraseTimer) { clearTimeout(_phraseTimer); _phraseTimer = null; }
    const el = $('generation-text');
    if (el) { el.style.opacity = '0'; setTimeout(() => { el.textContent = ''; }, 800); }
  }

  /* ── DOM shortcuts ────────────────────────────────────── */
  const $ = id => document.getElementById(id);

  /* ── State Machine ────────────────────────────────────── */

  function _setState(newState) {
    console.log(`[Main] State: ${_state} → ${newState}`);
    _state = newState;

    /* Update status label */
    const label = $('status-label');
    if (label) {
      const map = {
        [STATES.IDLE]:         '— IDLE —',
        [STATES.ARRIVAL]:      '— ARRIVAL —',
        [STATES.CONVERSATION]: '— INTERVIEW —',
        [STATES.TRANSITION]:   '— RENDERING —',
        [STATES.REVEAL]:       '— REVELATION —',
        [STATES.EXIT]:         '— RESETTING —',
      };
      label.textContent = map[newState] || newState.toUpperCase();
    }

    /* Debug */
    const dbgState = $('dbg-state');
    if (dbgState) dbgState.textContent = newState;
  }

  /* ── Transition to states ─────────────────────────────── */

  function _doIdle() {
    _setState(STATES.IDLE);
    _lastPortraitUrls = [];
    ConversationUI.hide();
    EmotionDisplay.reset();

    /* Show start overlay (briefly, then auto-advance in kiosk mode) */
    const overlay = $('start-overlay');
    if (overlay) overlay.classList.remove('hidden');

    /* Stop camera frame sending (keep camera on for preview) */
    _frameEmitPaused = true;

    /* Kiosk mode: auto-restart after a brief pause */
    setTimeout(() => {
      if (_state === STATES.IDLE) {
        if (overlay) overlay.classList.add('hidden');
        _doArrival();
      }
    }, 5000);
  }

  function _doArrival() {
    _setState(STATES.ARRIVAL);
    _frameEmitPaused = false;

    /* Start camera (fallback if Pi camera not available) */
    CameraManager.start().then(() => {
      console.log('[Main] Camera ready');
    }).catch(err => {
      console.error('[Main] Camera failed:', err);
    });

    /* Connect socket */
    SocketClient.connect();

    /* Connect Runway realtime avatar (LiveKit WebRTC) */
    AvatarManager.connect((err) => {
      if (err) console.warn('[Main] Avatar connect failed:', err);
      else console.log('[Main] Avatar connected');
    });

    /* Show main UI */
    AvatarManager.show();

    /* After brief pause, begin conversation */
    setTimeout(() => {
      _doConversation();
    }, 1200);
  }

  function _doConversation() {
    _setState(STATES.CONVERSATION);

    /* Tell backend to start FER capture */
    SocketClient.startConversation();

    /* Runway avatar runs the interview — wait for it to signal completion */
    AvatarManager.onInterviewComplete((data) => {
      if (_state !== STATES.CONVERSATION) return;
      console.log('[Main] Interview complete, name:', data.name, 'answers:', data.answers);
      ConversationUI.hide();
      SocketClient.sendInterviewComplete(data);
    });
  }

  function _doTransition(band, emotion) {
    _setState(STATES.TRANSITION);
    ConversationUI.hide();
    AvatarManager.cancelSpeech();

    /* Mirror voice phrases during generation */
    _startGenerationPhrases();

    /* Start 4-phase transition */
    TransitionEngine.start(band, emotion, (portraitUrl) => {
      _stopGenerationPhrases();
      _doReveal(portraitUrl, band);
    });
  }

  function _doReveal(portraitUrl, band) {
    _setState(STATES.REVEAL);

    const url  = portraitUrl || '/static/assets/fallback/portrait_example.jpg';
    const urls = _lastPortraitUrls.length ? _lastPortraitUrls : [url];
    PortraitReveal.showPicker(urls, band || _lastBand, () => {
      console.log('[Main] Portrait picker displayed');
    });

    /* Auto-reset after 60 seconds */
    if (_revealTimer) clearTimeout(_revealTimer);
    _revealTimer = setTimeout(() => {
      _doExit();
    }, 60000);
  }

  function _doExit() {
    _setState(STATES.EXIT);
    if (_revealTimer) { clearTimeout(_revealTimer); _revealTimer = null; }

    AvatarManager.cancelSpeech();
    ConversationUI.cancelListening();
    TransitionEngine.cancel();

    /* Tell server to drop BLE connection now that reveal is closing */
    SocketClient.socket && SocketClient.socket.emit('session_reset', {});

    PortraitReveal.hide(() => {
      setTimeout(() => _doIdle(), 800);
    });
  }

  /* ── Socket Event Handlers ────────────────────────────── */

  function _onConversationComplete(data) {
    console.log('[Main] Conversation complete:', data);
    /* Use numeric band_index for display; band string for transition colour */
    _lastBand    = data.band_index != null ? data.band_index : 0;
    _lastEmotion = data.emotion    != null ? data.emotion    : _lastEmotion;

    const dbgBand = $('dbg-band');
    if (dbgBand) dbgBand.textContent = String(_lastBand);

    ConversationUI.cancelListening();
    AvatarManager.cancelSpeech();

    /* Begin transition — pass band string for colour palette, index for display */
    _doTransition(data.band || _lastBand, _lastEmotion);
  }

  function _onPortraitReady(data) {
    console.log('[Main] Portrait ready signal:', data);
    const url  = data.image_url || data.url || data.portrait_url || data.path || '';
    const urls = data.image_urls && data.image_urls.length ? data.image_urls : [url];

    /* If we're still in transition, pass grid image to engine */
    if (_state === STATES.TRANSITION) {
      TransitionEngine.onPortraitReady(url);
      /* Store all 4 URLs so reveal can display picker */
      _lastPortraitUrls = urls;
    } else if (_state === STATES.REVEAL) {
      PortraitReveal.showPicker(urls, _lastBand);
    } else {
      _lastPortraitUrls = urls;
      _doReveal(url, _lastBand);
    }
  }

  function _onEmotionUpdate(data) {
    EmotionDisplay.update(data.emotions || data);
    _lastEmotions = data.emotions || data;

    /* Track dominant */
    let dominant = 'neutral', max = -1;
    Object.entries(_lastEmotions).forEach(([k, v]) => {
      if (v > max) { max = v; dominant = k; }
    });
    _lastEmotion = dominant;

    /* Debug */
    const dbgEmo = $('dbg-emotion');
    if (dbgEmo) dbgEmo.textContent = dominant + ' ' + (max * 100).toFixed(0) + '%';
  }

  function _onSocketConnected() {
    const dot   = $('socket-dot');
    const label = $('socket-status');
    if (dot)   dot.classList.add('active');
    if (label) label.textContent = 'CONNECTED';
    const dbgSock = $('dbg-socket');
    if (dbgSock) dbgSock.textContent = 'OK';
  }

  function _onSocketDisconnected() {
    const dot   = $('socket-dot');
    const label = $('socket-status');
    if (dot)   dot.classList.remove('active');
    if (label) label.textContent = 'DISCONNECTED';
    const dbgSock = $('dbg-socket');
    if (dbgSock) dbgSock.textContent = 'LOST';
  }

  /* ── Keyboard Shortcuts ───────────────────────────────── */

  function _handleKey(e) {
    /* Ignore if typing in input */
    if (e.target.tagName === 'INPUT') return;

    switch (e.code) {
      case 'Space':
        e.preventDefault();
        if (_state === STATES.IDLE) _enterFromIdle();
        break;

      case 'KeyR':
        e.preventDefault();
        _reset();
        break;

      case 'KeyD':
        _toggleDebug();
        break;

      case 'KeyP':
        /* Manual print trigger */
        if (_state === STATES.REVEAL) PortraitReveal.print();
        break;

      case 'KeyG':
        /* Dev: manually trigger glitch */
        AvatarManager.glitch();
        break;

      case 'Escape':
      case 'F11':
        e.preventDefault();
        fetch('/exit', { method: 'POST' });
        break;
    }
  }

  function _enterFromIdle() {
    const overlay = $('start-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
      setTimeout(() => _doArrival(), 600);
    }
  }

  function _reset() {
    console.log('[Main] Manual reset triggered');
    if (_state !== STATES.IDLE) _doExit();
  }

  function _toggleDebug() {
    _debugVisible = !_debugVisible;
    const panel = $('debug-panel');
    if (panel) panel.classList.toggle('visible', _debugVisible);
  }

  /* ── Clock ────────────────────────────────────────────── */

  function _startClock() {
    const el = $('time-display');
    if (!el) return;
    _timeInterval = setInterval(() => {
      const now  = new Date();
      const hh   = String(now.getHours()).padStart(2, '0');
      const mm   = String(now.getMinutes()).padStart(2, '0');
      const ss   = String(now.getSeconds()).padStart(2, '0');
      el.textContent = `${hh}:${mm}:${ss}`;

      /* Debug FPS refresh */
      if (_debugVisible) {
        const dbgFps = $('dbg-fps');
        if (dbgFps) dbgFps.textContent = CameraManager.getFps();
        const dbgSock = $('dbg-socket');
        if (dbgSock) dbgSock.textContent = SocketClient.connected ? 'CONNECTED' : 'DISCONNECTED';
        const dbgCam = $('dbg-camera');
        if (dbgCam) dbgCam.textContent = CameraManager.isRunning() ? 'ON' : 'OFF';
        const dbgPhase = $('dbg-phase');
        if (dbgPhase) dbgPhase.textContent = TransitionEngine.currentPhase() || '—';
      }
    }, 1000);
  }

  /* ── Camera Self-View ────────────────────────────────── */

  function _startCameraPreview() {
    const img = $('camera-preview');
    if (!img) return;
    setInterval(() => {
      const next = new Image();
      next.onload = () => { img.src = next.src; };
      next.src = '/camera/snapshot?' + Date.now();
    }, 300);
  }

  /* ── Init ─────────────────────────────────────────────── */

  function _init() {
    console.log('[Main] Mirror Mirror — initializing');

    /* Init modules */
    EmotionDisplay.init();
    AvatarManager.init();
    ConversationUI.init();
    TransitionEngine.init();
    PortraitReveal.init();
    PortraitReveal.onAbort(() => _doExit());

    /* Wire socket events */
    SocketClient.on('connected',             _onSocketConnected);
    SocketClient.on('disconnected',          _onSocketDisconnected);
    SocketClient.on('emotion_update',        _onEmotionUpdate);
    SocketClient.on('conversation_complete', _onConversationComplete);
    SocketClient.on('portrait_ready',        _onPortraitReady);
    SocketClient.on('print_status', (data) => {
      console.log('[Main] Print status:', data);

      /* As soon as printing starts, extend timer to cover full BLE transfer (~40s)
         so the 60s auto-reset never fires mid-print and disconnects the printer. */
      const status = (data && data.status) || '';
      if (status === 'connecting' || status === 'progress') {
        if (_revealTimer) clearTimeout(_revealTimer);
        _revealTimer = setTimeout(_doExit, 120000); /* 2 min safety window */
      }

      PortraitReveal.onPrintStatus(data, () => {
        /* After confirmed print, give 90s (30s ejection + 60s to choose) */
        if (_revealTimer) clearTimeout(_revealTimer);
        _revealTimer = setTimeout(_doExit, 90000);
        console.log('[Main] Reveal timer extended 90s after print');
      });
    });

    /* Click / touch anywhere on start overlay = enter + unlock audio */
    const overlay = $('start-overlay');
    if (overlay) {
      overlay.addEventListener('click', () => {
        AvatarManager.unlockAudio();
        if (_state === STATES.IDLE) _enterFromIdle();
      });
    }

    /* Unlock audio on any user interaction — keep listener active (not once) */
    document.addEventListener('click', () => AvatarManager.unlockAudio());
    document.addEventListener('keydown', () => AvatarManager.unlockAudio());

    /* Keyboard */
    document.addEventListener('keydown', _handleKey);

    /* Clock */
    _startClock();

    /* Camera self-view: poll snapshot every 100ms */
    _startCameraPreview();

    /* Pre-request microphone permission so SpeechRecognition works immediately */
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(stream => {
          stream.getTracks().forEach(t => t.stop()); // we just needed the permission grant
          console.log('[Main] Microphone permission granted');
        })
        .catch(err => console.warn('[Main] Microphone permission denied:', err));
    }

    /* Kiosk mode: auto-enter after brief splash */
    _doIdle();

    console.log('[Main] Initialized. Kiosk mode active — auto-starting momentarily.');
  }

  /* ── Boot ─────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

})(window);
