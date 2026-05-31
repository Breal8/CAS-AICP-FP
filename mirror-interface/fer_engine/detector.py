"""
Mirror Mirror — FER Detector
Wraps the `fer` library to analyse a base64-encoded JPEG frame and return
normalised emotion scores + dominant emotion.
"""

import base64
import io
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default / fallback emotion dict (neutral face)
# ---------------------------------------------------------------------------
_DEFAULT_EMOTIONS: dict[str, float] = {
    "angry":    0.0,
    "disgust":  0.0,
    "fear":     0.0,
    "happy":    0.0,
    "sad":      0.0,
    "surprise": 0.0,
    "neutral":  1.0,
}


def _neutral_result(session_id: str = "unknown") -> dict:
    return {
        "emotions": dict(_DEFAULT_EMOTIONS),
        "dominant": "neutral",
        "session_id": session_id,
        "face_detected": False,
    }


class EmotionDetector:
    """
    Analyses individual video frames for facial emotion.

    Usage:
        detector = EmotionDetector()
        result = detector.analyze_frame(base64_jpeg_string, session_id)
    """

    def __init__(self):
        self._detector = None
        self._ready = False
        self._init_detector()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_detector(self) -> None:
        try:
            import cv2
            from fer.fer import FER  # type: ignore
            self._detector = FER(mtcnn=False)  # mtcnn=True crashes TF Lite on Pi 5
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            self._ready = True
            logger.info("[FER] Detector initialised (mtcnn=False, CLAHE enabled)")
        except ImportError:
            logger.warning(
                "[FER] `fer` library not installed — running in stub mode. "
                "Install it with: pip install fer"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[FER] Failed to initialise detector: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_ndarray(
        self,
        frame: np.ndarray,
        session_id: str = "unknown",
    ) -> dict:
        """
        Analyse a raw numpy RGB frame (e.g. from picamera2).

        Returns:
            dict with keys: emotions, dominant, session_id, face_detected
        """
        if not self._ready or self._detector is None:
            return _neutral_result(session_id)

        try:
            detections = self._detector.detect_emotions(self._enhance(frame))

            if not detections:
                return _neutral_result(session_id)

            best = max(detections, key=lambda d: d["box"][2] * d["box"][3])
            raw_emotions: dict[str, float] = best["emotions"]
            emotions = self._normalise(raw_emotions)
            dominant = max(emotions, key=emotions.get)

            return {
                "emotions": emotions,
                "dominant": dominant,
                "session_id": session_id,
                "face_detected": True,
            }
        except Exception as exc:
            logger.error("[FER] analyze_ndarray error: %s", exc, exc_info=True)
            return _neutral_result(session_id)

    def analyze_frame(
        self,
        base64_image: str,
        session_id: str = "unknown",
    ) -> dict:
        """
        Analyse a base64-encoded JPEG frame.

        Parameters
        ----------
        base64_image:
            Raw base64 string (no data-URI prefix required, but tolerated).
        session_id:
            Session identifier passed through into the result dict.

        Returns
        -------
        dict with keys:
            emotions      — dict[str, float] all 7 scores (sum ≈ 1.0)
            dominant      — str label of the highest-scoring emotion
            session_id    — str
            face_detected — bool
        """
        if not self._ready or self._detector is None:
            logger.debug("[FER] Stub mode — returning neutral")
            result = _neutral_result(session_id)
            return result

        try:
            frame = self._decode_frame(base64_image)
            if frame is None:
                return _neutral_result(session_id)

            detections = self._detector.detect_emotions(self._enhance(frame))

            if not detections:
                logger.debug("[FER] No face detected in frame")
                return _neutral_result(session_id)

            # Use the detection with the largest bounding box
            best = max(detections, key=lambda d: d["box"][2] * d["box"][3])
            raw_emotions: dict[str, float] = best["emotions"]

            # Normalise so all values sum to 1.0
            emotions = self._normalise(raw_emotions)
            dominant = max(emotions, key=emotions.get)  # type: ignore[arg-type]

            logger.debug(
                "[FER] session=%s dominant=%s scores=%s",
                session_id,
                dominant,
                {k: round(v, 3) for k, v in emotions.items()},
            )

            return {
                "emotions": emotions,
                "dominant": dominant,
                "session_id": session_id,
                "face_detected": True,
            }

        except Exception as exc:  # noqa: BLE001
            logger.error("[FER] analyze_frame error: %s", exc, exc_info=True)
            return _neutral_result(session_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enhance(self, frame: np.ndarray) -> np.ndarray:
        """CLAHE on luminance to fix underexposed faces behind the mirror."""
        import cv2
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        l = self._clahe.apply(l)
        return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB)

    @staticmethod
    def _decode_frame(base64_image: str) -> Optional[np.ndarray]:
        """Decode a base64 string (with or without data-URI prefix) to an ndarray."""
        try:
            from PIL import Image  # type: ignore

            # Strip data-URI prefix if present
            if "," in base64_image:
                base64_image = base64_image.split(",", 1)[1]

            image_bytes = base64.b64decode(base64_image)
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            return np.array(image)
        except Exception as exc:  # noqa: BLE001
            logger.error("[FER] Frame decode error: %s", exc)
            return None

    @staticmethod
    def _normalise(emotions: dict[str, float]) -> dict[str, float]:
        """Ensure all 7 canonical emotion keys exist and values sum to 1.0."""
        canonical = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
        filled = {k: float(emotions.get(k, 0.0)) for k in canonical}
        total = sum(filled.values())
        if total > 0:
            return {k: v / total for k, v in filled.items()}
        filled["neutral"] = 1.0
        return filled
