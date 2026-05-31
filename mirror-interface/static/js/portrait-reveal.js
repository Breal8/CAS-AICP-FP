/**
 * portrait-reveal.js
 * Portrait picker: show all 4 Midjourney variants, let visitor choose, then print.
 */

(function (global) {
  'use strict';

  const BAND_LABELS = ['I', 'II', 'III', 'IV', 'V', 'VI'];

  let _container       = null;
  let _pickerGrid      = null;
  let _mainImg         = null;
  let _printBtn        = null;
  let _printFill       = null;
  let _printLabel      = null;
  let _portraitStatus  = null;
  let _bottomBar       = null;
  let _portraitPips    = null;
  let _postPrintEl     = null;
  let _printAgainBtn   = null;
  let _abortBtn        = null;
  let _doneBtn         = null;
  let _abortCallback   = null;

  let _selectedIndex   = 0;
  let _imageUrls       = [];
  let _autoTimer       = null;

  function _initDOM() {
    _container      = document.getElementById('portrait-container');
    _pickerGrid     = document.getElementById('portrait-picker-grid');
    _mainImg        = document.getElementById('portrait-image');
    _printBtn       = document.getElementById('portrait-print-btn');
    _printFill      = _printBtn  ? _printBtn.querySelector('.print-progress-fill') : null;
    _printLabel     = _printBtn  ? _printBtn.querySelector('.print-btn-label')     : null;
    _portraitStatus = document.getElementById('portrait-status');
    _bottomBar      = document.getElementById('portrait-bottom-bar');
    _portraitPips   = document.querySelectorAll('.portrait-pip');

    if (_printBtn) {
      _printBtn.addEventListener('click', () => _doPrint());
    }

    _postPrintEl  = document.getElementById('post-print-actions');
    _printAgainBtn = document.getElementById('print-again-btn');
    _abortBtn      = document.getElementById('abort-print-btn');
    _doneBtn       = document.getElementById('done-btn');

    if (_printAgainBtn) {
      _printAgainBtn.addEventListener('click', () => {
        _postPrintEl && _postPrintEl.classList.remove('visible');
        _resetPrintBtn();
        if (_printBtn) _printBtn.classList.add('visible');
        if (_doneBtn)  _doneBtn.classList.add('visible');
      });
    }
    if (_abortBtn) {
      _abortBtn.addEventListener('click', () => {
        _postPrintEl && _postPrintEl.classList.remove('visible');
        if (typeof _abortCallback === 'function') _abortCallback();
      });
    }
    if (_doneBtn) {
      _doneBtn.addEventListener('click', () => {
        if (typeof _abortCallback === 'function') _abortCallback();
      });
    }
  }

  function _setPrintProgress(pct, label) {
    if (_printFill)  _printFill.style.width  = pct + '%';
    if (_printLabel) _printLabel.textContent = label;
  }

  function _resetPrintBtn() {
    _setPrintProgress(0, 'PRINT THIS ONE');
    if (_printBtn) { _printBtn.disabled = false; }
  }

  function _setBandPips(activeBand) {
    _portraitPips.forEach((pip, i) => {
      pip.classList.toggle('active', i === activeBand);
    });
    document.querySelectorAll('.band-pip').forEach((pip, i) => {
      pip.classList.toggle('active', i === activeBand);
    });
  }

  function _selectVariant(index) {
    _selectedIndex = index;
    const url = _imageUrls[index];
    if (!url) return;

    /* Highlight selected thumb */
    if (_pickerGrid) {
      _pickerGrid.querySelectorAll('.picker-thumb').forEach((el, i) => {
        el.classList.toggle('selected', i === index);
      });
    }

    /* Update main display */
    if (_mainImg) {
      _mainImg.style.opacity = '0';
      const next = new Image();
      next.onload = () => {
        _mainImg.src = next.src;
        _mainImg.style.transition = 'opacity 0.5s ease';
        _mainImg.style.opacity = '1';
      };
      next.src = url;
    }
  }

  let _postPrintTimer = null;

  function _showPostPrint() {
    if (_postPrintTimer) { clearTimeout(_postPrintTimer); _postPrintTimer = null; }
    if (_printBtn)    _printBtn.classList.remove('visible');
    if (_doneBtn)     _doneBtn.classList.remove('visible');
    if (_postPrintEl) _postPrintEl.classList.add('visible');
  }

  function _doPrint() {
    const url = _imageUrls[_selectedIndex];
    if (!url) { console.warn('[PortraitReveal] No image URL to print'); return; }
    console.log('[PortraitReveal] Printing variant', _selectedIndex, url);
    if (_printBtn) _printBtn.disabled = true;
    _setPrintProgress(10, 'CONNECTING…');
    /* Show PRINT ANOTHER after 25s from click regardless of printer status */
    _postPrintTimer = setTimeout(_showPostPrint, 25000);
    if (global.SocketClient && global.SocketClient.connected) {
      global.SocketClient.printPolaroid(url);
    } else {
      console.error('[PortraitReveal] Socket not connected — cannot print');
      _setPrintProgress(0, 'NOT CONNECTED');
      if (_printBtn) _printBtn.disabled = false;
    }
  }

  /* ── Public API ───────────────────────────────────────── */
  const PortraitReveal = {

    init() {
      _initDOM();
    },

    /**
     * Show the 4-portrait picker.
     * @param {string[]} imageUrls  - Array of 1–4 portrait URLs
     * @param {number}   band       - Numeric band index (0–5)
     * @param {Function} [onReady]
     */
    showPicker(imageUrls, band, onReady) {
      if (!_container) return;

      _imageUrls     = imageUrls || [];
      _selectedIndex = 0;

      /* Build picker thumbs */
      if (_pickerGrid) {
        _pickerGrid.innerHTML = '';
        _imageUrls.forEach((url, i) => {
          const thumb = document.createElement('div');
          thumb.className = 'picker-thumb' + (i === 0 ? ' selected' : '');
          const img = document.createElement('img');
          img.src = url;
          img.alt = `Variant ${i + 1}`;
          thumb.appendChild(img);
          thumb.addEventListener('click', () => _selectVariant(i));
          _pickerGrid.appendChild(thumb);
        });
        _pickerGrid.classList.add('visible');
      }

      /* Show main image — set src and make visible; no opacity fade to avoid onload race */
      if (_mainImg) {
        _mainImg.style.transition = '';
        _mainImg.style.opacity    = '1';
        _mainImg.style.filter     = 'blur(0px)';
        _mainImg.classList.remove('blurred');
        _mainImg.src = _imageUrls[0] || '';
        if (typeof onReady === 'function') onReady();
      }

      /* Show container */
      _container.classList.add('visible');
      _container.style.opacity = '1';
      _setBandPips(band || 0);

      /* Show print button + done button */
      _resetPrintBtn();
      if (_printBtn) _printBtn.classList.add('visible');
      if (_doneBtn)  _doneBtn.classList.add('visible');

      /* Status + bottom bar */
      if (_portraitStatus) _portraitStatus.classList.add('visible');
      if (_bottomBar) setTimeout(() => _bottomBar.classList.add('visible'), 1200);

      /* Debug */
      const dbgBand = document.getElementById('dbg-band');
      if (dbgBand) dbgBand.textContent = BAND_LABELS[band] || String(band);
    },

    /* Legacy alias so existing call sites still work */
    show(imageUrl, band, onReady) {
      this.showPicker([imageUrl], band, onReady);
    },

    hide(onHidden) {
      if (!_container) return;

      /* Clear auto-select timer */
      if (_autoTimer) { clearTimeout(_autoTimer); _autoTimer = null; }

      _container.style.transition = 'opacity 0.8s ease';
      _container.style.opacity    = '0';
      if (_portraitStatus) _portraitStatus.classList.remove('visible');
      if (_bottomBar)      _bottomBar.classList.remove('visible');
      if (_printBtn)       _printBtn.classList.remove('visible');
      if (_doneBtn)        _doneBtn.classList.remove('visible');
      if (_pickerGrid)     _pickerGrid.classList.remove('visible');
      if (_postPrintEl)    _postPrintEl.classList.remove('visible');

      setTimeout(() => {
        _container.classList.remove('visible');
        _container.style.opacity    = '0';
        _container.style.transition = '';
        if (_mainImg) {
          _mainImg.src           = '';
          _mainImg.style.opacity = '0';
        }
        if (_pickerGrid) _pickerGrid.innerHTML = '';
        _imageUrls = [];
        _setBandPips(-1);
        if (typeof onHidden === 'function') onHidden();
      }, 820);
    },

    print() {
      _doPrint();
    },

    /** Called by main.js when socket emits print_status */
    onPrintStatus(data, onPrinted) {
      const status = (data && data.status) || '';
      console.log('[PortraitReveal] print_status:', status, data);
      switch (status) {
        case 'connecting':
          _setPrintProgress(10, 'CONNECTING…');
          break;
        case 'progress': {
          const raw = data.progress || 0;
          let label;
          if (raw < 30)       label = 'CONNECTING…';
          else if (raw < 78)  label = 'SENDING…';
          else                label = 'PRINTING…';
          _setPrintProgress(raw, label);
          break;
        }
        case 'printed':
        case 'mock_printed':
          _setPrintProgress(100, 'PRINTED ✓');
          if (typeof onPrinted === 'function') onPrinted();
          /* 3s for mechanical ejection, then show PRINT ANOTHER (cancels the 25s fallback) */
          if (_postPrintTimer) { clearTimeout(_postPrintTimer); _postPrintTimer = null; }
          setTimeout(_showPostPrint, 3000);
          break;
        case 'error':
          _setPrintProgress(0, 'ERROR — RETRY?');
          if (_printBtn) _printBtn.disabled = false;
          setTimeout(_resetPrintBtn, 4000);
          break;
        default:
          _resetPrintBtn();
      }
    },

    isVisible() {
      return _container && _container.classList.contains('visible');
    },

    onAbort(cb) {
      _abortCallback = cb;
    },
  };

  global.PortraitReveal = PortraitReveal;

})(window);
