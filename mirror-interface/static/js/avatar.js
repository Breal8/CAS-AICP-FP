/**
 * avatar.js — Runway Realtime Avatar via LiveKit WebRTC
 *
 * The Runway avatar (Nina) handles the full conversation:
 * listening to the visitor, speaking the questions, and calling
 * the interview_complete tool when done.
 *
 * This module manages the LiveKit connection and fires a callback
 * when the avatar signals interview_complete over the data channel.
 */

(function (global) {
  'use strict';

  const LIVEKIT_CDN = 'https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.min.js';

  let _avatarFrame   = null;
  let _avatarVideo   = null;
  let _room          = null;
  let _connected     = false;
  let _lkLoaded      = false;

  let _interviewCompleteCallback = null;

  /* ── Load LiveKit SDK once ────────────────────────────── */
  function _loadLiveKit(cb) {
    if (_lkLoaded) { cb(); return; }
    const s = document.createElement('script');
    s.src = LIVEKIT_CDN;
    s.onload = () => { _lkLoaded = true; cb(); };
    s.onerror = () => console.error('[Avatar] Failed to load LiveKit SDK');
    document.head.appendChild(s);
  }

  function _initDOM() {
    _avatarFrame = document.getElementById('avatar-frame');
    _avatarVideo = document.getElementById('avatar-video');

    if (_avatarVideo) {
      _avatarVideo.autoplay    = true;
      _avatarVideo.playsInline = true;
      _avatarVideo.muted       = false;
      Object.assign(_avatarVideo.style, {
        width: '100%', height: '100%', objectFit: 'cover',
      });
    }
  }

  /* ── Debug helper ─────────────────────────────────────── */
  function _dbg(key, val) {
    const el = document.getElementById('dbg-tts');
    if (el) el.textContent = val;
    console.log('[Avatar]', key, val);
  }

  /* ── Data channel: parse Runway client_event messages ─── */
  function _handleDataReceived(payload) {
    try {
      const message = JSON.parse(new TextDecoder().decode(payload));
      if (message?.type !== 'client_event') return;

      console.log('[Avatar] client_event:', message.tool, message.args);

      if (message.tool === 'capture_face') {
        console.log('[Avatar] capture_face — requesting snapshot');
        if (global.SocketClient && global.SocketClient.connected) {
          global.SocketClient.socket.emit('capture_face', {});
        }
        return;
      }

      if (message.tool === 'interview_complete') {
        const args = message.args || {};
        const name = args.answer_name || '';
        const answers = [
          args.answer_1 || '',
          args.answer_2 || '',
          args.answer_3 || '',
          args.answer_4 || '',
          args.answer_5 || '',
        ];
        console.log('[Avatar] interview_complete — name:', name, 'answers:', answers);
        if (typeof _interviewCompleteCallback === 'function') {
          _interviewCompleteCallback({ name, answers });
        }
      }
    } catch (e) {
      /* not JSON or unrecognised — ignore */
    }
  }

  /* ── LiveKit connection ───────────────────────────────── */
  async function _connect(serverUrl, token) {
    const LK = window.LivekitClient;
    if (!LK) { console.error('[Avatar] LiveKit not loaded'); _dbg('lk', 'SDK missing'); return; }

    if (_room) { try { await _room.disconnect(); } catch (_) {} }

    _room = new LK.Room({ adaptiveStream: false, dynacast: false });

    _room.on(LK.RoomEvent.DataReceived, (payload) => {
      _handleDataReceived(payload);
    });

    _room.on(LK.RoomEvent.TrackSubscribed, (track, pub, participant) => {
      console.log('[Avatar] TrackSubscribed kind=%s participant=%s', track.kind, participant.identity);
      if (track.kind === LK.Track.Kind.Video && _avatarVideo) {
        track.attach(_avatarVideo);
        _avatarVideo.classList.add('visible');
        _avatarVideo.play().catch(e => console.warn('[Avatar] video.play():', e));
        _dbg('lk', 'video live');
      }
      if (track.kind === LK.Track.Kind.Audio) {
        const el = track.attach();
        el.autoplay = true;
        el.muted    = false;
        el.volume   = 1;
        document.body.appendChild(el);
        el.play().catch(() => {
          /* Autoplay blocked — startAudio() will unblock on next gesture */
        });
        /* Re-unlock audio context now that a track has arrived */
        if (_room) _room.startAudio().catch(() => {});
        console.log('[Avatar] Audio track live from', participant.identity);
        _dbg('lk', 'audio live');
      }
    });

    _room.on(LK.RoomEvent.TrackUnsubscribed, (track, pub, participant) => {
      console.log('[Avatar] TrackUnsubscribed kind=%s participant=%s', track.kind, participant.identity);
    });

    _room.on(LK.RoomEvent.ParticipantConnected, (p) => {
      console.log('[Avatar] Participant connected:', p.identity);
      _dbg('lk', 'participant: ' + p.identity);
    });

    _room.on(LK.RoomEvent.ParticipantDisconnected, (p) => {
      console.log('[Avatar] Participant left:', p.identity);
    });

    _room.on(LK.RoomEvent.Disconnected, (reason) => {
      _connected = false;
      _dbg('lk', 'disconnected: ' + reason);
    });

    _room.on(LK.RoomEvent.ConnectionStateChanged, (state) => {
      _dbg('lk', state);
      console.log('[Avatar] LiveKit state:', state);
    });

    _room.on(LK.RoomEvent.AudioPlaybackStatusChanged, () => {
      if (_room.canPlaybackAudio) {
        console.log('[Avatar] Audio playback enabled');
        _dbg('lk', 'audio unlocked');
      } else {
        console.warn('[Avatar] Audio playback blocked — autoplay policy');
        _dbg('lk', 'audio BLOCKED');
      }
    });

    try {
      await _room.connect(serverUrl, token);
      _connected = true;
      const participants = Array.from(_room.remoteParticipants.values());
      console.log('[Avatar] Connected, room=%s participants=%d', _room.name, participants.length);
      _dbg('lk', 'connected (' + participants.length + ' participants)');

      /* Publish local microphone so the avatar can hear the visitor */
      try {
        await _room.localParticipant.setMicrophoneEnabled(true);
        console.log('[Avatar] Microphone published to LiveKit room');
        _dbg('lk', 'mic live');
      } catch (micErr) {
        console.warn('[Avatar] Microphone enable failed:', micErr);
        _dbg('lk', 'mic FAILED: ' + micErr.message);
      }

      /* Attempt to unlock audio (requires prior user gesture in strict browsers) */
      _room.startAudio().then(() => {
        _dbg('lk', 'audio ok');
      }).catch(() => {
        _dbg('lk', 'audio needs gesture');
      });

    } catch (err) {
      console.error('[Avatar] LiveKit connect failed:', err);
      _dbg('lk', 'FAILED: ' + err.message);
      throw err;
    }
  }

  /* Call this after a user gesture to unlock audio if it was blocked */
  function _unlockAudio() {
    if (_room) {
      _room.startAudio().catch(() => {});
    }
  }

  /* ── Fetch credentials ────────────────────────────────── */
  function _startSession(callback) {
    fetch('/avatar/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          console.error('[Avatar] Connect error:', data.error);
          if (typeof callback === 'function') callback(new Error(data.error));
          return;
        }
        _loadLiveKit(() => {
          _connect(data.serverUrl, data.token)
            .then(() => { if (typeof callback === 'function') callback(null); })
            .catch(err => {
              console.error('[Avatar] LiveKit error:', err);
              if (typeof callback === 'function') callback(err);
            });
        });
      })
      .catch(err => {
        console.error('[Avatar] Fetch error:', err);
        if (typeof callback === 'function') callback(err);
      });
  }

  /* ── Public API ───────────────────────────────────────── */
  const AvatarManager = {

    init() {
      _initDOM();
    },

    connect(callback) {
      _startSession(callback);
    },

    show() {
      if (_avatarFrame) {
        _avatarFrame.style.transition = 'opacity 1.2s ease';
        _avatarFrame.style.opacity = '1';
      }
    },

    hide() {
      if (_avatarFrame) {
        _avatarFrame.style.transition = 'opacity 0.8s ease';
        _avatarFrame.style.opacity = '0';
      }
    },

    disconnect() {
      if (_room) {
        _room.disconnect().catch(() => {});
        _room = null;
        _connected = false;
      }
    },

    /** Register callback fired when Nina calls interview_complete tool. */
    onInterviewComplete(cb) {
      _interviewCompleteCallback = cb;
    },

    isConnected()   { return _connected; },
    isSpeaking()    { return false; },
    unlockAudio()   { _unlockAudio(); },
    glitch()        {},
    playVideoUrl()  {},
    setImage()      {},
    speak(text, cb) { if (typeof cb === 'function') cb(); }, /* no-op — avatar speaks itself */
    cancelSpeech()  {},
    ttsSupported()  { return false; },
  };

  global.AvatarManager = AvatarManager;

})(window);
