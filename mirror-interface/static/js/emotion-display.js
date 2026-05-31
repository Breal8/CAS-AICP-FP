/**
 * emotion-display.js
 * Real-time 7-emotion bar UI
 * Mirror Mirror Installation
 */

(function (global) {
  'use strict';

  const EMOTIONS = ['angry', 'disgust', 'fear', 'happy', 'sad', 'surprise', 'neutral'];

  /* Pre-cache DOM nodes */
  let _rows = {};  /* { emotion: { row, fill, label, value } } */

  function _init() {
    EMOTIONS.forEach(name => {
      const row   = document.querySelector(`[data-emotion="${name}"]`);
      if (!row) return;
      _rows[name] = {
        row,
        label: row.querySelector('.emotion-label'),
        fill:  row.querySelector('.emotion-bar-fill'),
        value: row.querySelector('.emotion-value'),
      };
    });
  }

  /* ── Public API ───────────────────────────────────────── */
  const EmotionDisplay = {

    /**
     * Initialize (call once after DOM ready).
     */
    init() {
      _init();
    },

    /**
     * Update bars with a new emotion reading.
     * @param {Object} emotions - { angry: 0.1, happy: 0.8, ... }
     */
    update(emotions) {
      if (!emotions || typeof emotions !== 'object') return;

      /* Find dominant emotion */
      let dominantName  = 'neutral';
      let dominantValue = -1;
      EMOTIONS.forEach(name => {
        const v = emotions[name] != null ? emotions[name] : 0;
        if (v > dominantValue) {
          dominantValue = v;
          dominantName  = name;
        }
      });

      EMOTIONS.forEach(name => {
        const el = _rows[name];
        if (!el) return;

        const raw  = emotions[name] != null ? emotions[name] : 0;
        const pct  = Math.max(0, Math.min(1, raw));
        const isDom = (name === dominantName);

        /* Bar fill width */
        el.fill.style.width = (pct * 100).toFixed(1) + '%';

        /* Numeric label */
        el.value.textContent = pct.toFixed(2);

        /* Dominant highlighting */
        el.row.classList.toggle('dominant', isDom);
        el.label.classList.toggle('dominant', isDom);
      });
    },

    /**
     * Reset all bars to zero.
     */
    reset() {
      const empty = {};
      EMOTIONS.forEach(n => { empty[n] = 0; });
      this.update(empty);
    },

    /**
     * Return dominant emotion name from last update.
     * Reads live from DOM widths (cheap).
     */
    getDominant() {
      let best = 'neutral', bestW = -1;
      EMOTIONS.forEach(name => {
        const el = _rows[name];
        if (!el) return;
        const w = parseFloat(el.fill.style.width) || 0;
        if (w > bestW) { bestW = w; best = name; }
      });
      return best;
    },
  };

  global.EmotionDisplay = EmotionDisplay;

})(window);
