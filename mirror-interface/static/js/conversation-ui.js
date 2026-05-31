/**
 * conversation-ui.js
 * Subtitle display for the Mirror Mirror conversation overlay.
 *
 * The Runway avatar (Nina) handles all speech and listening.
 * This module only shows/hides the conversation overlay.
 */

(function (global) {
  'use strict';

  let _overlay    = null;
  let _subtitleEl = null;

  function _initDOM() {
    _overlay    = document.getElementById('conversation-overlay');
    _subtitleEl = document.getElementById('question-subtitle');
  }

  const ConversationUI = {

    init() {
      _initDOM();
    },

    show() {
      if (_overlay) _overlay.classList.add('visible');
    },

    hide() {
      if (_overlay) _overlay.classList.remove('visible');
      if (_subtitleEl) _subtitleEl.textContent = '';
    },

    showQuestion(text) {
      if (!_subtitleEl) return;
      _subtitleEl.style.opacity = '0';
      _subtitleEl.style.transition = 'opacity 0.5s';
      setTimeout(() => {
        _subtitleEl.textContent = text;
        _subtitleEl.style.opacity = '1';
      }, 200);
    },

    clearSubtitle() {
      if (_subtitleEl) {
        _subtitleEl.style.transition = 'opacity 0.4s';
        _subtitleEl.style.opacity = '0';
        setTimeout(() => { _subtitleEl.textContent = ''; }, 400);
      }
    },

    /* No-ops kept for call-site compatibility */
    cancelListening() {},
    listenForAnswer() {},
    onVoiceAnswer()  {},
    hideListening()  {},
  };

  global.ConversationUI = ConversationUI;

})(window);
