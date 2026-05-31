"""
camera_worker.py — Standalone Pi camera capture process.
Runs picamera2 outside of eventlet, writes latest JPEG to a shared file.
Started by app.py via subprocess; communicates via filesystem.
"""

import io
import os
import signal
import sys
import time
import logging

import numpy as np

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

FRAME_PATH = "/tmp/mirror_latest_frame.jpg"
FPS = 15
FRAME_INTERVAL = 1.0 / FPS

# Colour correction — load from config, fall back to neutral if unavailable
try:
    sys.path.insert(0, os.path.dirname(__file__))
    from config import CAMERA_CC_R, CAMERA_CC_G, CAMERA_CC_B
except Exception:
    CAMERA_CC_R = CAMERA_CC_G = CAMERA_CC_B = 1.0

_CC = np.array([CAMERA_CC_R, CAMERA_CC_G, CAMERA_CC_B], dtype=np.float32)


def _apply_cc(arr: np.ndarray) -> np.ndarray:
    """Apply per-channel colour correction and clip to uint8."""
    out = (arr.astype(np.float32) * _CC).clip(0, 255).astype(np.uint8)
    return out

_running = True


def _sigterm(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


def main():
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cfg = cam.create_preview_configuration(
            main={"format": "RGB888", "size": (320, 240)},
        )
        cam.configure(cfg)
        cam.start()
        time.sleep(1.0)  # warm-up
        logger.info("Camera started (picamera2)")
    except Exception as exc:
        logger.error("Failed to start camera: %s", exc)
        sys.exit(1)

    from PIL import Image
    import numpy as np

    while _running:
        t0 = time.time()
        try:
            frame = cam.capture_array("main")
            frame = _apply_cc(frame)
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            with open(FRAME_PATH + ".tmp", "wb") as f:
                f.write(buf.getvalue())
            import os
            os.replace(FRAME_PATH + ".tmp", FRAME_PATH)  # atomic swap
        except Exception as exc:
            logger.warning("Capture error: %s", exc)

        elapsed = time.time() - t0
        wait = FRAME_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)

    cam.stop()
    cam.close()
    logger.info("Camera worker stopped")


if __name__ == "__main__":
    main()
