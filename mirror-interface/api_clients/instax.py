"""
Mirror Mirror — Instax Bluetooth Print Client
Uses ~/InstaxBLE/InstaxBLE.py when MOCK_INSTAX=false.

Tracks remaining film via a persistent counter (INSTAX_FILM_CAPACITY photos per
cartridge). When the counter reaches 0 the print is rejected immediately.
Call reload_film() or hit /instax/reload after swapping in a fresh cartridge.
"""

import io
import logging
import os
import sys
import time
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from config import MOCK_INSTAX, INSTAX_BLE_NAME, INSTAX_FILM_CAPACITY, INSTAX_FILM_STATE_FILE

logger = logging.getLogger(__name__)

_INSTAX_LIB = os.path.expanduser("~/InstaxBLE")


def _read_film_count() -> int:
    try:
        return int(open(INSTAX_FILM_STATE_FILE).read().strip())
    except Exception:
        return INSTAX_FILM_CAPACITY


def _write_film_count(n: int) -> None:
    try:
        with open(INSTAX_FILM_STATE_FILE, "w") as f:
            f.write(str(n))
    except Exception as exc:
        logger.warning("[Instax] Could not save film count: %s", exc)


class InstaxClient:
    def __init__(self, printer_address: Optional[str] = None):
        self.mock = MOCK_INSTAX
        self.printer_address = printer_address or INSTAX_BLE_NAME
        self._printer = None
        self._film_remaining = _read_film_count()
        logger.info(
            "[Instax] mode=%s address=%s film_remaining=%d",
            "MOCK" if self.mock else "REAL",
            self.printer_address,
            self._film_remaining,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def print_photo(self, image_url: str, band: Optional[str] = None, progress_cb=None) -> dict:
        if self.mock:
            return self._mock_print(image_url, band, progress_cb)
        return self._real_print(image_url, band, progress_cb)

    def reload_film(self) -> int:
        """Reset the film counter to a full cartridge. Call after swapping film."""
        self._film_remaining = INSTAX_FILM_CAPACITY
        _write_film_count(self._film_remaining)
        logger.info("[Instax] Film reloaded — %d photos available", self._film_remaining)
        return self._film_remaining

    @property
    def film_remaining(self) -> int:
        return self._film_remaining

    def disconnect_printer(self) -> None:
        """Explicitly close the BLE connection (call on session end)."""
        if self._printer is not None:
            try:
                self._printer.disconnect()
                logger.info("[Instax] Printer disconnected")
            except Exception as exc:
                logger.warning("[Instax] Disconnect error: %s", exc)
            self._printer = None

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _mock_print(self, image_url: str, band: Optional[str], progress_cb=None) -> dict:
        if self._film_remaining <= 0:
            logger.warning("[Instax][MOCK] Out of film")
            return {"status": "error", "error": "No film left — reload cartridge (0 of 10 remaining)"}

        logger.info("[Instax][MOCK] image=%s band=%s film_remaining=%d", image_url, band, self._film_remaining)
        stages = [(15, "connecting"), (30, "sending"), (60, "sending"),
                  (80, "printing"), (95, "printing"), (100, "done")]
        for pct, _ in stages:
            time.sleep(2.0)
            if progress_cb:
                progress_cb(pct)

        self._film_remaining -= 1
        _write_film_count(self._film_remaining)
        logger.info("[Instax][MOCK] Print complete — film_remaining=%d", self._film_remaining)
        return {"status": "mock_printed", "image_url": image_url, "band": band,
                "film_remaining": self._film_remaining}

    # ------------------------------------------------------------------
    # Real BLE print
    # ------------------------------------------------------------------

    def _real_print(self, image_url: str, band: Optional[str], progress_cb=None) -> dict:
        try:
            # Check software film counter before touching BLE
            if self._film_remaining <= 0:
                raise RuntimeError(
                    f"No film left — reload cartridge (0 of {INSTAX_FILM_CAPACITY} remaining)"
                )

            sys.path.insert(0, _INSTAX_LIB)
            from InstaxBLE import InstaxBLE  # type: ignore

            # Always disconnect any lingering connection before a fresh print
            self.disconnect_printer()

            # Prepare image (InstaxBLE re-compresses internally, so JPEG quality matters less)
            formatted = self._format_for_instax(image_url)
            img_rgb = cv2.cvtColor(formatted, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            buf = io.BytesIO()
            pil_img.save(buf, format='JPEG', quality=75)
            buf.seek(0)
            logger.info("[Instax] Image prepared: %d bytes", len(buf.getvalue()))

            logger.info(
                "[Instax] Connecting to %s… (film_remaining=%d)",
                self.printer_address, self._film_remaining,
            )
            self._printer = InstaxBLE(
                device_address=self.printer_address,
                print_enabled=True,
                quiet=True,
            )
            self._printer.connect()
            logger.info("[Instax] Connected")

            if progress_cb:
                progress_cb(15)

            # Send image — InstaxBLE sends first packet immediately, rest via notification callbacks
            self._printer.print_image(buf)

            # Guard: if print_image returned without queuing packets something went wrong
            packets_queued = len(getattr(self._printer, "packetsForPrinting", []))
            if packets_queued == 0:
                raise RuntimeError("print_image queued 0 packets — printer may have rejected the image")

            logger.info("[Instax] Print started — %d packets queued, waiting for drain", packets_queued)
            if progress_cb:
                progress_cb(25)

            # Wait for all BLE packets to be sent (notification-driven, ~1s per packet)
            start = time.time()
            drain_deadline = time.time() + 90
            last_pct = 25
            while time.time() < drain_deadline:
                packets_left = len(getattr(self._printer, "packetsForPrinting", []))
                if packets_left == 0:
                    break
                elapsed = time.time() - start
                pct = int(25 + min(elapsed / 25.0, 1.0) * 70)  # 25→95 over ~25s
                if pct != last_pct and progress_cb:
                    progress_cb(pct)
                    last_pct = pct
                time.sleep(1)

            elapsed_total = time.time() - start
            logger.info("[Instax] Packets drained in %.1fs — print complete", elapsed_total)
            if progress_cb:
                progress_cb(100)

            # Decrement counter before disconnect so a concurrent session_reset
            # cannot set _printer=None and cause a crash here
            self._film_remaining -= 1
            _write_film_count(self._film_remaining)
            logger.info("[Instax] film_remaining=%d", self._film_remaining)

            # Disconnect in a background thread so it doesn't delay the printed response
            import threading
            threading.Thread(target=self.disconnect_printer, daemon=True).start()

            return {
                "status": "printed",
                "image_url": image_url,
                "band": band,
                "film_remaining": self._film_remaining,
            }

        except Exception as exc:
            logger.error("[Instax] Print failed: %s", exc, exc_info=True)
            self.disconnect_printer()
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Image formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_for_instax(image_url: str) -> np.ndarray:
        """Crop/resize to 600×800 (3:4 portrait) as required by InstaxBLE."""
        PORTRAIT_CACHE_DIR = "/tmp/mirror_portraits"
        if image_url.startswith("/static/"):
            abs_path = os.path.join(os.path.dirname(__file__), "..", image_url.lstrip("/"))
            img = cv2.imread(os.path.normpath(abs_path))
        elif image_url.startswith("/portraits/"):
            # Locally cached portrait — resolve directly from filesystem
            filename = image_url.split("/portraits/", 1)[1]
            img = cv2.imread(os.path.join(PORTRAIT_CACHE_DIR, filename))
        elif os.path.isabs(image_url):
            img = cv2.imread(image_url)
        else:
            import requests as req
            resp = req.get(image_url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; MirrorMirror/1.0)"})
            if resp.status_code != 200 or len(resp.content) == 0:
                raise RuntimeError(f"Image download failed (status={resp.status_code} len={len(resp.content)}): {image_url}")
            arr = np.frombuffer(resp.content, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if img is None:
            raise RuntimeError(f"Could not load image: {image_url}")

        h, w = img.shape[:2]
        target_ratio = 3 / 4
        if (w / h) > target_ratio:
            new_w = int(h * target_ratio)
            x = (w - new_w) // 2
            img = img[:, x:x + new_w]
        else:
            new_h = int(w / target_ratio)
            y = (h - new_h) // 2
            img = img[y:y + new_h, :]

        return cv2.resize(img, (600, 800))
