"""
Mirror Mirror — Runway API Client

Mock mode: returns a placeholder image URL after a random 5-10 s delay.
Real mode: TODO — uncomment and fill in the actual Runway Gen-3 / Gen-2 call.
"""

import logging
import random
import time
from typing import Optional

from config import (
    FALLBACK_PORTRAIT,
    MOCK_RUNWAY,
    PORTRAIT_MOCK_MAX_DELAY,
    PORTRAIT_MOCK_MIN_DELAY,
    RUNWAY_API_KEY,
    RUNWAY_API_URL,
)

logger = logging.getLogger(__name__)


class RunwayClient:
    """
    Generates a stylised portrait video / image via Runway ML.

    Usage::

        client = RunwayClient()
        result = client.generate(
            prompt="bioluminescent portrait, ultra-detailed",
            input_image_url="https://...",
            band="bioluminescence",
        )
        # result = {"image_url": "...", "band": "bioluminescence"}
    """

    def __init__(self):
        self.api_key = RUNWAY_API_KEY
        self.base_url = RUNWAY_API_URL
        self.mock = MOCK_RUNWAY

        if self.mock:
            logger.info("[Runway] Running in MOCK mode")
        else:
            logger.info("[Runway] Running in REAL mode (key=%s...)", self.api_key[:8])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        input_image_url: Optional[str] = None,
        band: str = "bioluminescence",
    ) -> dict:
        """
        Generate a stylised portrait.

        Parameters
        ----------
        prompt:
            Text prompt describing the desired style.
        input_image_url:
            Optional base image URL (used in real mode).
        band:
            LoRA band name for logging / real-mode LoRA selection.

        Returns
        -------
        dict:  {"image_url": str, "band": str}
        """
        if self.mock:
            return self._mock_generate(band)
        return self._real_generate(prompt, input_image_url, band)

    # ------------------------------------------------------------------
    # Mock implementation
    # ------------------------------------------------------------------

    def _mock_generate(self, band: str) -> dict:
        delay = random.uniform(PORTRAIT_MOCK_MIN_DELAY, PORTRAIT_MOCK_MAX_DELAY)
        logger.info(
            "[Runway][MOCK] Simulating generation for band=%s (delay=%.1fs)",
            band,
            delay,
        )
        time.sleep(delay)
        image_url = FALLBACK_PORTRAIT
        logger.info("[Runway][MOCK] Done — returning %s", image_url)
        return {"image_url": image_url, "band": band}

    # ------------------------------------------------------------------
    # Real implementation (TODO)
    # ------------------------------------------------------------------

    def _real_generate(
        self,
        prompt: str,
        input_image_url: Optional[str],
        band: str,
    ) -> dict:
        """
        TODO: Implement real Runway Gen-3 Alpha API call.

        Runway Gen-3 Alpha Turbo endpoint reference:
        https://docs.runwayml.com/

        Example skeleton (uncomment and fill in):

            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Runway-Version": "2024-11-06",
            }
            payload = {
                "model": "gen3a_turbo",
                "promptText": prompt,
                "promptImage": input_image_url,
                "duration": 5,
                "ratio": "768:1280",
            }
            response = requests.post(
                f"{self.base_url}/image_to_video",
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            task_id = response.json()["id"]

            # Poll until complete
            while True:
                time.sleep(3)
                poll = requests.get(
                    f"{self.base_url}/tasks/{task_id}",
                    headers=headers,
                    timeout=15,
                )
                poll.raise_for_status()
                data = poll.json()
                if data["status"] == "SUCCEEDED":
                    return {"image_url": data["output"][0], "band": band}
                if data["status"] in ("FAILED", "CANCELLED"):
                    raise RuntimeError(f"Runway task {task_id} failed: {data}")
        """
        logger.warning(
            "[Runway] Real mode called but not implemented — falling back to mock"
        )
        return self._mock_generate(band)
