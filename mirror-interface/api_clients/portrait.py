"""
Mirror Mirror — Portrait Generation

Primary (face available): Replicate InstantID — preserves visitor's face identity.
Fallback / no face:        Legnext AI (Midjourney) — band-style artistic portrait.

API base (Midjourney): https://api.legnext.ai/api
API base (Replicate):  https://api.replicate.com/v1
"""

import logging
import random
import time
import requests
from typing import Optional

from config import (
    FALLBACK_PORTRAIT,
    MOCK_PORTRAIT,
    NEXTLEG_API_KEY,
    NEXTLEG_API_URL,
    PORTRAIT_MOCK_MAX_DELAY,
    PORTRAIT_MOCK_MIN_DELAY,
)

logger = logging.getLogger(__name__)

LEGNEXT_BASE = "https://api.legnext.ai/api"

# ---------------------------------------------------------------------------
# Midjourney prompts per band — 2:3 ratio for Polaroid/Instax printing
# Use {gender} placeholder — replaced at generation time with "man" or "woman"
# ---------------------------------------------------------------------------
BAND_PROMPTS: dict[str, str] = {
    "renaissance":     "a rococo portrait of {gender} --sref 634594906 --v 7 --ar 2:3",
    "botanique":       "a beautiful floral portrait of {gender} --sref 1984928975 --v 7 --ar 2:3",
    "bioluminescence": "a beautiful luminicance portrait of {gender} --sref 3458093130 --v 7 --ar 2:3",
    "surveillance":    (
        "A portrait of {gender} Early facial recognition technology(FERET), developed by the US Department of Defence, "
        "compiled a data-base of 14,126 35mm portrait photographs in which participants signed a release form approved "
        "by a university ethics board. Although these images lacked diversity, they are described by Crawford as "
        '"surprisingly beautiful - high-resolution photographs captured in the style of formal portraiture"(pg. 104-105), '
        "suggesting an early relationship between artistry and image databases. Limited, semi-ethical, databases such as "
        "FERET all but disappeared with the emergence of the internet and social media. In an incredibly short period of "
        "time, users of social media began uploading countless images to 'free' social media applications, essentially "
        "giving away their ownership of said images in the fine print of the terms and conditions. There is nothing "
        "'free' about social media; users are paying with their data. On an average day in 2019, approximately 350 "
        "million photographs were uploaded of Facebook and 500 million tweets were sent.(pg. 106). While AI tech "
        "developers are frequently and rightfully accused of ethical misuse of data, users who willingly provide said "
        "data share the same responsibility. --sref 1141642173 --v 7 --ar 2:3"
    ),
    "geometric":       "a geometric portrait of {gender} --sref 927300585 --v 7 --ar 2:3",
    "glitch":          (
        "portrait of {gender} being collapsing into fragments, glitch slices, rainbow signal trails breaking apart, "
        "head and torso dissolving into noise, surreal digital disintegration, cinematic dark background "
        "--sref 42049937 --sw 50 --v 7 --ar 2:3"
    ),
}


class PortraitClient:
    def __init__(self):
        self.mock = MOCK_PORTRAIT
        self._headers = {
            "Authorization": f"Bearer {NEXTLEG_API_KEY}",
            "Content-Type": "application/json",
        }

        from api_clients.replicate_instantid_photorealistic import ReplicateInstantIDPhotorealisticClient
        self._replicate = ReplicateInstantIDPhotorealisticClient()

        if self.mock:
            logger.info("[Portrait] Running in MOCK mode")
        elif self._replicate.available():
            logger.info("[Portrait] grandlineai/instant-id-photorealistic pipeline ready")
        else:
            logger.info("[Portrait] Midjourney only (no Replicate key)")

    def generate(
        self,
        band: str,
        source_image_b64: Optional[str] = None,
        extra_prompt: str = "",
        gender: str = "woman",
    ) -> dict:
        if self.mock:
            return self._mock_generate(band)

        # If we have a face and Replicate key, use PuLID+FLUX directly —
        # it handles both face identity and band style in one pass.
        if source_image_b64 and self._replicate.available():
            logger.info("[Portrait] Face available — using grandlineai InstantID (band=%s gender=%s)", band, gender)
            result = self._replicate.generate(band, source_image_b64, gender)
            if result:
                return result
            logger.warning("[Portrait] grandlineai failed — falling back to Midjourney")

        # No face or PuLID unavailable: Midjourney band-style portrait.
        return self._legnext_generate(band, source_image_b64, extra_prompt, gender)

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _mock_generate(self, band: str) -> dict:
        delay = random.uniform(PORTRAIT_MOCK_MIN_DELAY, PORTRAIT_MOCK_MAX_DELAY)
        logger.info("[Portrait][MOCK] band=%s delay=%.1fs", band, delay)
        time.sleep(delay)
        return {"image_url": FALLBACK_PORTRAIT, "band": band}

    # ------------------------------------------------------------------
    # Image upload for face reference
    # ------------------------------------------------------------------

    def _upload_snapshot(self, source_image_b64: str) -> Optional[str]:
        """Upload a base64 JPEG to a public host and return a direct URL for Midjourney."""
        import base64
        image_bytes = base64.b64decode(source_image_b64)

        # catbox.moe — direct CDN URL, trusted by Midjourney's image fetcher
        try:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": ("snapshot.jpg", image_bytes, "image/jpeg")},
                timeout=30,
            )
            if resp.status_code == 200 and resp.text.startswith("https://"):
                url = resp.text.strip()
                logger.info("[Portrait] Snapshot uploaded (catbox): %s", url)
                return url
        except Exception as exc:
            logger.warning("[Portrait] catbox upload failed: %s", exc)

        # Fallback: litterbox.catbox.moe (ephemeral, 24h)
        try:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "24h"},
                files={"fileToUpload": ("snapshot.jpg", image_bytes, "image/jpeg")},
                timeout=30,
            )
            if resp.status_code == 200 and resp.text.startswith("https://"):
                url = resp.text.strip()
                logger.info("[Portrait] Snapshot uploaded (litterbox): %s", url)
                return url
        except Exception as exc:
            logger.warning("[Portrait] litterbox upload failed: %s", exc)

        # Last resort: 0x0.st
        try:
            resp = requests.post(
                "https://0x0.st",
                files={"file": ("snapshot.jpg", image_bytes, "image/jpeg")},
                timeout=30,
                verify=False,
            )
            if resp.status_code == 200:
                url = resp.text.strip()
                if url.startswith("http"):
                    logger.info("[Portrait] Snapshot uploaded (0x0.st): %s", url)
                    return url
        except Exception as exc:
            logger.warning("[Portrait] 0x0.st upload failed: %s", exc)

        return None

    # ------------------------------------------------------------------
    # Legnext AI (Midjourney)
    # ------------------------------------------------------------------

    def _legnext_generate(
        self,
        band: str,
        source_image_b64: Optional[str],
        extra_prompt: str,
        gender: str = "woman",
    ) -> dict:
        band_prompt = BAND_PROMPTS.get(band, BAND_PROMPTS["bioluminescence"])
        band_prompt = band_prompt.replace("{gender}", gender)
        if extra_prompt:
            band_prompt = f"{extra_prompt}, {band_prompt}"

        # Try with face reference first; if Midjourney rejects it, retry without
        face_url = None
        if source_image_b64:
            face_url = self._upload_snapshot(source_image_b64)
            if face_url:
                logger.info("[Portrait] Face reference URL: %s", face_url)

        for attempt, use_face in enumerate([True, False]):
            if use_face and not face_url:
                continue

            payload: dict = {"text": band_prompt}
            if use_face and face_url:
                payload["image_url"] = face_url
                payload["image_weight"] = 1.2

            try:
                logger.info("[Portrait] Submitting to Legnext — band=%s face=%s", band, use_face and bool(face_url))
                logger.info("[Portrait] Full prompt: %s (image_url=%s)", band_prompt, face_url if use_face else None)
                resp = requests.post(
                    f"{LEGNEXT_BASE}/v1/diffusion",
                    headers=self._headers,
                    json=payload,
                    timeout=30,
                    verify=False,
                )
                resp.raise_for_status()
                data = resp.json()
                job_id = data.get("job_id")
                if not job_id:
                    raise RuntimeError(f"No job_id in response: {data}")

                logger.info("[Portrait] Job submitted — job_id=%s", job_id)
                result = self._poll(job_id, band)
                return result

            except RuntimeError as exc:
                msg = str(exc)
                if attempt == 0:
                    logger.warning("[Portrait] Face reference attempt failed (%s) — retrying without", msg)
                    continue
                logger.error("[Portrait] Legnext error: %s", exc)
                return self._mock_generate(band)
            except Exception as exc:
                logger.error("[Portrait] Legnext error: %s", exc, exc_info=True)
                return self._mock_generate(band)

        return self._mock_generate(band)

    def _poll(self, job_id: str, band: str, timeout: int = 300) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                resp = requests.get(
                    f"{LEGNEXT_BASE}/v1/job/{job_id}",
                    headers=self._headers,
                    timeout=15,
                    verify=False,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "")
                logger.info("[Portrait] Poll — job_id=%s status=%s", job_id, status)

                if status == "completed":
                    output = data.get("output", {})
                    image_urls = output.get("image_urls") or []
                    image_url = image_urls[0] if image_urls else output.get("image_url", "")
                    if image_url:
                        logger.info("[Portrait] Done — %d images, first: %s", len(image_urls), image_url[:80])
                        return {"image_url": image_url, "image_urls": image_urls, "band": band}
                    raise RuntimeError(f"Completed but no image_url: {data}")

                if status == "failed":
                    err = data.get("error", {}).get("message", "unknown")
                    raise RuntimeError(f"Job failed: {err}")

            except requests.RequestException as exc:
                logger.warning("[Portrait] Poll request error: %s", exc)

        raise RuntimeError(f"Legnext poll timed out after {timeout}s")
