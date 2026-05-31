"""
Mirror Mirror — Pi Camera Capture Module

Uses picamera2 on Raspberry Pi for hardware camera access.
Falls back to OpenCV VideoCapture on non-Pi environments (dev/testing).
Feeds frames directly to the FER engine and optionally broadcasts
to WebSocket clients for preview.

Usage:
    from pi_camera import PiCamera
    camera = PiCamera(emotion_detector, socketio)
    camera.start()
    ...
    camera.stop()
"""

import io
import logging
import os
import sys
import threading
import time
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (mirrors config.py for local import safety)
# ---------------------------------------------------------------------------
DEFAULT_RESOLUTION = (320, 240)
DEFAULT_FRAMERATE = 15
FRAME_INTERVAL_MS = 500  # ms between FER analysis frames


class PiCamera:
    """
    Camera capture loop that feeds frames to the emotion detector.

    On Raspberry Pi: uses picamera2 (hardware ISP pipeline, efficient).
    On other platforms: falls back to OpenCV VideoCapture.
    """

    def __init__(
        self,
        emotion_detector: Any,
        socketio: Any,
        resolution: tuple[int, int] = DEFAULT_RESOLUTION,
        framerate: int = DEFAULT_FRAMERATE,
        fer_interval_ms: int = FRAME_INTERVAL_MS,
    ):
        self.emotion_detector = emotion_detector
        self.socketio = socketio
        self.resolution = resolution
        self.framerate = framerate
        self.fer_interval_ms = fer_interval_ms

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # picamera2 / cv2 handle
        self._picam2 = None
        self._cv_cap = None
        self._backend = "none"

        # Session tracking for FER (shared with app.py sessions)
        self._active_session_id: Optional[str] = None

        # Performance tracking
        self._frame_count = 0
        self._last_fer_time = 0.0

        # Latest JPEG frame cached for non-blocking snapshot endpoint
        self._latest_jpeg: Optional[bytes] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._worker_proc = None  # camera_worker subprocess

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the capture loop. Returns True on success."""
        if self._running:
            logger.warning("[Camera] Already running")
            return True

        # Try picamera2 first (Pi hardware), unless forced to OpenCV
        import config as _cfg
        if not _cfg.FORCE_OPENCV and self._init_picamera2():
            self._backend = "picamera2"
        elif self._init_opencv():
            self._backend = "opencv"
        else:
            logger.error("[Camera] No camera backend available")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._running = True

        logger.info(
            "[Camera] Started (%s) %dx%d @ %d fps, FER every %d ms",
            self._backend,
            self.resolution[0],
            self.resolution[1],
            self.framerate,
            self.fer_interval_ms,
        )
        return True

    def stop(self) -> None:
        """Stop the capture loop and release resources."""
        if not self._running:
            return

        logger.info("[Camera] Stopping...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

        self._release()
        self._running = False
        logger.info("[Camera] Stopped")

    # ------------------------------------------------------------------
    # Session binding
    # ------------------------------------------------------------------

    def set_session_id(self, session_id: Optional[str]) -> None:
        """Bind/unbind the current conversation session for FER tagging."""
        self._active_session_id = session_id
        logger.debug("[Camera] Session bound: %s", session_id)

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------

    FRAME_PATH = "/tmp/mirror_latest_frame.jpg"

    def _init_picamera2(self) -> bool:
        """Launch camera_worker.py as a subprocess (avoids eventlet deadlock)."""
        import subprocess
        worker = os.path.join(os.path.dirname(__file__), "camera_worker.py")
        if not os.path.exists(worker):
            return False
        try:
            # Verify picamera2 is importable before launching worker
            import importlib
            if importlib.util.find_spec("picamera2") is None:
                return False

            self._worker_proc = subprocess.Popen(
                [sys.executable, worker],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Wait up to 5s for first frame file to appear
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if os.path.exists(self.FRAME_PATH):
                    break
                time.sleep(0.2)

            if not os.path.exists(self.FRAME_PATH):
                logger.warning("[Camera] worker started but no frame file yet")

            logger.info("[Camera] camera_worker subprocess started (pid=%d)", self._worker_proc.pid)
            return True
        except Exception as exc:
            logger.warning("[Camera] picamera2 worker unavailable: %s", exc)
            return False

    def _init_opencv(self) -> bool:
        """Fallback: OpenCV VideoCapture (dev laptops, USB webcams)."""
        try:
            import cv2  # type: ignore[import-untyped]
            import config as _cfg

            dev = _cfg.CAMERA_DEVICE
            self._cv_cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            if not self._cv_cap.isOpened():
                logger.warning("[Camera] OpenCV: cannot open %s", dev)
                self._cv_cap = None
                return False

            # MJPEG allows higher resolutions on USB webcams
            self._cv_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            self._cv_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self._cv_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self._cv_cap.set(cv2.CAP_PROP_FPS, self.framerate)

            logger.info("[Camera] OpenCV fallback initialised")
            return True
        except Exception as exc:
            logger.debug("[Camera] OpenCV unavailable: %s", exc)
            self._cv_cap = None
            return False

    def _release(self) -> None:
        """Release camera resources."""
        if self._worker_proc is not None:
            try:
                self._worker_proc.terminate()
                self._worker_proc.wait(timeout=3)
            except Exception as exc:
                logger.warning("[Camera] worker stop error: %s", exc)
            self._worker_proc = None

        if self._picam2 is not None:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception as exc:
                logger.warning("[Camera] picamera2 release error: %s", exc)
            self._picam2 = None

        if self._cv_cap is not None:
            self._cv_cap.release()
            self._cv_cap = None

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """FER dispatch loop."""
        _none_count = 0
        while not self._stop_event.is_set():
            frame = self._capture_frame()

            if frame is None:
                _none_count += 1
                if _none_count == 1 or _none_count % 100 == 0:
                    logger.warning("[Camera] capture returned None (count=%d)", _none_count)
                time.sleep(0.05)
                continue
            _none_count = 0

            # Encode JPEG for non-blocking snapshot endpoint
            try:
                from PIL import Image
                img = Image.fromarray(frame)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=60)
                self._latest_jpeg = buf.getvalue()
            except Exception as exc:
                logger.debug("[Camera] JPEG cache error: %s", exc)

            self._frame_count += 1
            now = time.time() * 1000

            # Run FER on this frame if interval elapsed
            if now - self._last_fer_time >= self.fer_interval_ms:
                self._last_fer_time = now
                self._process_fer(frame)

            time.sleep(0.05)  # ~20 Hz polling

    def _capture_frame(self) -> Optional[np.ndarray]:
        """Capture one frame from the active backend."""
        if self._backend == "picamera2":
            # Read latest JPEG from worker subprocess file, decode to RGB array for FER
            try:
                if os.path.exists(self.FRAME_PATH):
                    with open(self.FRAME_PATH, "rb") as f:
                        data = f.read()
                    self._latest_jpeg = data
                    from PIL import Image
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    return np.array(img)
            except Exception as exc:
                logger.debug("[Camera] frame read error: %s", exc)
            return None

        if self._backend == "opencv" and self._cv_cap is not None:
            import cv2

            ret, frame = self._cv_cap.read()
            if not ret:
                return None
            # OpenCV gives BGR — convert to RGB for FER
            if len(frame.shape) == 3 and frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Resize if needed
            h, w = frame.shape[:2]
            if (w, h) != self.resolution:
                frame = cv2.resize(frame, self.resolution)
            return frame

        return None

    # ------------------------------------------------------------------
    # FER processing
    # ------------------------------------------------------------------

    def _process_fer(self, frame: np.ndarray) -> None:
        """Analyse frame with FER and broadcast result."""
        if self.emotion_detector is None or not self.emotion_detector._ready:
            return

        try:
            result = self.emotion_detector.analyze_ndarray(frame, self._active_session_id)
            if self.socketio:
                self.socketio.emit("emotion_update", result)
        except Exception as exc:
            logger.error("[Camera] FER processing error: %s", exc)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def get_frame_for_preview(self) -> Optional[bytes]:
        """Return the latest cached JPEG frame (non-blocking)."""
        return self._latest_jpeg

    def _get_frame_for_preview_slow(self) -> Optional[bytes]:
        """Capture a single frame and return as JPEG bytes (blocking, for reference)."""
        frame = self._capture_frame()
        if frame is None:
            return None
        try:
            from PIL import Image

            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            return buf.getvalue()
        except Exception as exc:
            logger.warning("[Camera] Preview encode error: %s", exc)
            return None

    def is_running(self) -> bool:
        return self._running

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def frame_count(self) -> int:
        return self._frame_count
