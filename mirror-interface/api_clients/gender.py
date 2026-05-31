"""Shared gender detection utility for portrait API clients."""

import base64
import io
import logging

logger = logging.getLogger(__name__)


def detect_gender(source_image_b64: str) -> str:
    """
    Detect gender from a base64 JPEG using DeepFace.
    Returns 'man' or 'woman'. Falls back to 'woman' on any error.
    """
    try:
        import numpy as np
        from PIL import Image
        from deepface import DeepFace  # type: ignore[import-untyped]

        arr = np.array(Image.open(io.BytesIO(base64.b64decode(source_image_b64))).convert("RGB"))
        result = DeepFace.analyze(arr, actions=["gender"], enforce_detection=False, silent=True)
        if isinstance(result, list):
            result = result[0]
        dominant = result.get("dominant_gender", "Woman")
        scores = result.get("gender", {})
        gender = "man" if dominant.lower() == "man" else "woman"
        logger.info(
            "[Gender] Detected: %s  (Man %.0f%% / Woman %.0f%%)",
            gender,
            scores.get("Man", 0),
            scores.get("Woman", 0),
        )
        return gender
    except Exception as exc:
        logger.warning("[Gender] Detection failed (%s) — defaulting to 'woman'", exc)
        return "woman"
