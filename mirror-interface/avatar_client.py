"""
Mirror Mirror — Runway GMW Avatar Client

Manages interaction with the Runway ML API for Generative World Models (GMW).
The avatar character is pre-configured via the Runway dashboard and has
built-in voice synthesis.

This client handles:
1. Sending visitor messages to the Runway GMW endpoint
2. Polling for avatar video/response completion
3. Caching recent avatar frames for the frontend
4. Serving the current avatar media URL to the browser

Usage:
    from avatar_client import AvatarClient
    avatar = AvatarClient()
    result = avatar.send_message("Would you let a machine know you better?")
    # result = {"video_url": "https://cdn.runway...", "status": "ready"}
"""

import logging
import time
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = 3.0
MAX_POLL_SECONDS = 120.0


class AvatarClient:
    """
    Client for Runway GMW avatar generation.

    The character is pre-configured in the Runway dashboard.
    This client sends visitor dialogue and retrieves the generated
    video/audio response.
    """

    def __init__(self):
        self.api_key = getattr(config, "RUNWAY_GMW_API_KEY", config.RUNWAY_API_KEY)
        self.endpoint = getattr(
            config, "RUNWAY_GMW_ENDPOINT", "https://api.runwayml.com/v1"
        )
        self.character_id = getattr(config, "RUNWAY_CHARACTER_ID", "")
        self.mock = getattr(config, "MOCK_GMW", config.MOCK_RUNWAY)

        # Cache
        self._last_video_url: Optional[str] = None
        self._last_response: Optional[dict] = None

        if self.mock:
            logger.info("[Avatar] Running in MOCK mode")
        else:
            logger.info(
                "[Avatar] GMW endpoint=%s character=%s",
                self.endpoint,
                self.character_id[:8] + "..." if self.character_id else "default",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_message(
        self,
        message: str,
        session_id: Optional[str] = None,
        emotion_context: Optional[str] = None,
    ) -> dict:
        """
        Send a visitor message to the avatar and retrieve the response video.

        Parameters
        ----------
        message:
            The visitor's spoken text (or the question being asked).
        session_id:
            Optional session identifier for continuity.
        emotion_context:
            Optional dominant emotion label for expression hints.

        Returns
        -------
        dict:  {"video_url": str, "status": str, "duration": float}
        """
        if self.mock:
            return self._mock_response(message, emotion_context)
        return self._real_send(message, session_id, emotion_context)

    def get_current_media(self) -> Optional[str]:
        """Return the URL of the most recently generated avatar video."""
        return self._last_video_url

    def reset_conversation(self, session_id: Optional[str] = None) -> dict:
        """Reset the avatar's conversational context for a new visitor."""
        if self.mock:
            logger.info("[Avatar][MOCK] Conversation reset")
            return {"status": "reset"}

        # If Runway API supports context reset, call it here
        # Otherwise, context management happens via session_id on each call
        logger.info("[Avatar] Conversation reset for session=%s", session_id)
        return {"status": "reset"}

    # ------------------------------------------------------------------
    # Mock implementation
    # ------------------------------------------------------------------

    def _mock_response(self, message: str, emotion: Optional[str]) -> dict:
        """Return a placeholder response for development/testing."""
        delay = 2.0  # fast mock for dev loop
        logger.info(
            "[Avatar][MOCK] Simulating response for: %r (delay=%.1fs)",
            message[:60],
            delay,
        )
        time.sleep(delay)

        # Fallback to a static avatar loop or placeholder
        url = "/static/assets/fallback/avatar_loop.mp4"
        self._last_video_url = url
        return {
            "video_url": url,
            "status": "ready",
            "duration": 3.0,
            "mock": True,
        }

    # ------------------------------------------------------------------
    # Real implementation
    # ------------------------------------------------------------------

    def _real_send(
        self,
        message: str,
        session_id: Optional[str],
        emotion_context: Optional[str],
    ) -> dict:
        """
        Call the Runway GMW API.

        NOTE: Runway's exact GMW API spec may differ from the Gen-3 endpoint.
        This skeleton uses the documented Runway HTTP pattern. Adjust headers,
        payload keys, and polling logic once the real API contract is known.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

        payload: dict[str, Any] = {
            "characterId": self.character_id,
            "message": message,
        }
        if session_id:
            payload["sessionId"] = session_id
        if emotion_context:
            payload["emotionHint"] = emotion_context

        try:
            # Step 1: Submit the message
            logger.info("[Avatar] Submitting message to GMW: %r", message[:80])
            resp = requests.post(
                f"{self.endpoint}/characters/{self.character_id}/generate",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            task_id = data.get("id") or data.get("taskId")
            if not task_id:
                raise RuntimeError(f"No task ID in GMW response: {data}")

            # Step 2: Poll for completion
            video_url = self._poll_task(task_id, headers)
            self._last_video_url = video_url

            return {
                "video_url": video_url,
                "status": "ready",
                "duration": data.get("duration", 3.0),
            }

        except requests.exceptions.RequestException as exc:
            logger.error("[Avatar] GMW API request failed: %s", exc)
            # Return a degraded response so the installation doesn't hang
            return {
                "video_url": config.FALLBACK_PORTRAIT,
                "status": "error",
                "error": str(exc),
            }

    def _poll_task(self, task_id: str, headers: dict) -> str:
        """Poll a Runway task until it succeeds or times out."""
        start = time.time()
        while time.time() - start < MAX_POLL_SECONDS:
            time.sleep(POLL_INTERVAL_SECONDS)

            try:
                resp = requests.get(
                    f"{self.endpoint}/tasks/{task_id}",
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "").upper()
                logger.debug("[Avatar] Poll task=%s status=%s", task_id, status)

                if status in ("SUCCEEDED", "COMPLETED", "READY"):
                    # Extract video URL from response
                    output = data.get("output", {})
                    if isinstance(output, list):
                        url = output[0]
                    elif isinstance(output, dict):
                        url = output.get("url") or output.get("videoUrl", "")
                    else:
                        url = str(output)
                    logger.info("[Avatar] Task complete: %s", url[:80])
                    return url

                if status in ("FAILED", "CANCELLED", "ERROR"):
                    raise RuntimeError(f"GMW task {task_id} failed: {data}")

            except requests.exceptions.RequestException as exc:
                logger.warning("[Avatar] Poll error: %s", exc)
                continue

        raise RuntimeError(f"GMW task {task_id} timed out after {MAX_POLL_SECONDS}s")
