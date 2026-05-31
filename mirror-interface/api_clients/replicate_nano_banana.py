"""
Mirror Mirror — Styled portrait via google/nano-banana-2

Google's fast image generation model built on Gemini 3.1 Flash Image.
Supports text-to-image and image editing with up to 14 reference images,
multi-image fusion, and character consistency.

Install:  pip install replicate
          (or use requests directly with REPLICATE_API_KEY, as here)

Model:    google/nano-banana-2
API:      https://replicate.com/google/nano-banana-2/api
Endpoint: POST https://api.replicate.com/v1/models/google/nano-banana-2/predictions
"""

import base64
import io
import logging
import os
import time
import requests
from typing import Optional

from config import REPLICATE_API_KEY

logger = logging.getLogger(__name__)

REPLICATE_BASE = "https://api.replicate.com/v1"
MODEL_ENDPOINT = f"{REPLICATE_BASE}/models/google/nano-banana-2/predictions"

# When a style image is present in band_styles/, image 1 = face, image 2 = style reference.
# When no style image is found, falls back to image 1 = face only, text-driven style.
# {gender} is substituted at runtime.

STYLE_PROMPTS_WITH_REF: dict[str, str] = {
    "renaissance": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, colors, brushwork, "
        "lighting and artistic technique shown in image 2. "
        "The result should look like the {gender} from image 1 painted in the style of image 2."
    ),
    "botanique": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, colors, composition "
        "and artistic technique shown in image 2. "
        "The result should look like the {gender} from image 1 depicted in the style of image 2."
    ),
    "renaissance-botanique": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait blending the visual style, colors and artistic techniques "
        "of both image 2 references — classical portraiture fused with botanical illustration. "
        "The result should look like the {gender} from image 1 rendered in this hybrid style."
    ),
    "bioluminescence": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, colors, lighting "
        "and artistic technique shown in image 2. "
        "The result should look like the {gender} from image 1 in the style of image 2."
    ),
    "surveillance": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, colors, effects "
        "and artistic treatment shown in image 2. "
        "The result should look like the {gender} from image 1 depicted in the style of image 2."
    ),
    "geometric": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, colors, graphic elements "
        "and artistic treatment shown in image 2. "
        "The result should look like the {gender} from image 1 depicted in the style of image 2."
    ),
    "glitch": (
        "Take the face and identity of the {gender} in image 1. "
        "Recreate them as a portrait rendered in the exact same visual style, glitch effects, colors "
        "and artistic treatment shown in image 2. "
        "The result should look like the {gender} from image 1 depicted in the style of image 2."
    ),
}

STYLE_PROMPTS_TEXT_ONLY: dict[str, str] = {
    "renaissance": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Rococo oil painting in the style of Fragonard and Boucher, 18th century aristocratic "
        "portraiture. Three-quarter profile, contemplative expression, silk brocade jacket, "
        "pastel floral background, soft candlelight, oil on canvas, museum quality."
    ),
    "botanique": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Double exposure portrait, strict left side profile. Vintage botanical illustrations fused "
        "into the silhouette — poppies and marigolds blooming from the hair, leaves weaving through. "
        "19th century herbarium plate aesthetic, aged paper background, warm earthy palette."
    ),
    "renaissance-botanique": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Classical oil portrait fused with vintage botanical illustration — Renaissance brushwork and "
        "warm candlelight, with botanical elements woven into the composition: flowers, leaves and "
        "vines threading through the figure. Aged paper tones meet rich oil paint, museum quality."
    ),
    "bioluminescence": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Cinematic bioluminescent sci-fi portrait, tight close-up. Futuristic illuminated suit with "
        "glowing cyan tubing. Deep black background, intense electric-blue emissive lighting, "
        "cobalt neon-blue palette. Highly detailed."
    ),
    "surveillance": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Cyberpunk surveillance portrait, retro-futuristic VHS glitch aesthetic. Frontal close-up, "
        "CRT scanlines, analog grain, RGB displacement. Neon cyan and red highlights, "
        "red alphanumeric overlays, facial recognition atmosphere. Highly detailed."
    ),
    "geometric": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Futuristic geometric portrait, cyberpunk editorial collage. Photorealism fused with vector "
        "abstraction, pure black background. Interlocking polygonal shards around the face, "
        "electric blue, magenta and orange accents. Highly detailed."
    ),
    "glitch": (
        "Portrait of a {gender} with the face of the uploaded image 1. "
        "Glitch art digital corruption portrait, heavy datamosh. Severe RGB chromatic aberration, "
        "horizontal displaced pixel bands, pixel sorting streaks, VHS tracking errors. Face partially "
        "fragmenting into pixel shards, dark background with neon highlights. Highly detailed."
    ),
}

# Aspect ratio per band — nano-banana-2 accepts standard ratio strings
BAND_ASPECT_RATIOS: dict[str, str] = {
    "renaissance":             "2:3",
    "botanique":              "2:3",
    "renaissance-botanique":  "2:3",
    "bioluminescence": "2:3",
    "surveillance":   "2:3",
    "geometric":      "2:3",
    "glitch":         "2:3",
}

DEFAULT_ASPECT_RATIO = "2:3"


class ReplicateNanoBananaClient:
    """
    Client for google/nano-banana-2 on Replicate.

    Uses the /models/google/nano-banana-2/predictions endpoint (no version hash —
    Replicate serves the latest deployment automatically for this model family).
    """

    def __init__(self):
        self.api_key = REPLICATE_API_KEY
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",  # ask Replicate to respond synchronously when fast enough
        }

    def available(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        band: str,
        source_image_b64: str,
        gender: Optional[str] = None,
        num_outputs: int = 4,
    ) -> Optional[dict]:
        """
        Generate styled portraits using the visitor's photo as a character reference.

        Fires num_outputs predictions in parallel (each call returns 1 image).
        Returns {"image_url": str, "image_urls": list[str], "band": str} or None.
        """
        if not self.available():
            logger.warning("[NanoBanana] No API key — skipping")
            return None

        if gender is None:
            from api_clients.gender import detect_gender
            gender = detect_gender(source_image_b64)

        face_data_url = f"data:image/jpeg;base64,{self._crop_portrait(source_image_b64)}"
        style_data_urls = self._load_style_images(band)
        aspect_ratio = BAND_ASPECT_RATIOS.get(band, DEFAULT_ASPECT_RATIO)

        if not style_data_urls:
            logger.warning("[NanoBanana] No style images found for band=%s — generation may lack style reference", band)

        image_input = [face_data_url] + style_data_urls
        style_refs = [f"image {i}" for i in range(2, 2 + len(style_data_urls))]
        ref_str = (", ".join(style_refs[:-1]) + " and " + style_refs[-1]) if len(style_refs) > 1 else (style_refs[0] if style_refs else "image 2")
        template = STYLE_PROMPTS_WITH_REF.get(band, STYLE_PROMPTS_WITH_REF["bioluminescence"])
        prompt = template.format(gender=gender).replace("image 2", ref_str)
        logger.info("[NanoBanana] band=%s gender=%s style_images=%d", band, gender, len(style_data_urls))

        def _submit_one() -> Optional[str]:
            payload = {
                "input": {
                    "prompt":        prompt,
                    "image_input":   image_input,
                    "aspect_ratio":  aspect_ratio,
                    "resolution":    "1K",
                    "output_format": "jpg",
                },
            }
            try:
                resp = requests.post(
                    MODEL_ENDPOINT,
                    headers=self._headers,
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                output = data.get("output")

                if status == "succeeded" and output:
                    return output if isinstance(output, str) else output[0]

                pid = data.get("id")
                if pid and status not in ("failed", "canceled"):
                    result = self._poll(pid)
                    if result:
                        return result["image_urls"][0]

                logger.error("[NanoBanana] Prediction failed: %s", data.get("error"))
            except Exception as exc:
                logger.error("[NanoBanana] Request error: %s", exc, exc_info=True)
            return None

        logger.info(
            "[NanoBanana] Submitting %d predictions — band=%s gender=%s",
            num_outputs, band, gender,
        )

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_outputs) as pool:
            futures = [pool.submit(_submit_one) for _ in range(num_outputs)]
            urls = [f.result() for f in concurrent.futures.as_completed(futures)]

        urls = [u for u in urls if u]
        if not urls:
            logger.error("[NanoBanana] All predictions failed")
            return None

        logger.info("[NanoBanana] Done — %d/%d images", len(urls), num_outputs)
        return {"image_url": urls[0], "image_urls": urls, "band": band}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_style_images(self, band: str) -> list[str]:
        """
        Return data-URLs for all style reference images for this band.

        Looks for band_styles/{band}/1.jpg, 2.png, etc. — any numeric filename,
        sorted numerically. Falls back to a flat band_styles/{band}.jpg for
        backwards compatibility. Returns an empty list if nothing is found.
        """
        styles_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "band_styles")
        band_dir = os.path.join(styles_dir, band)
        exts = ("jpg", "jpeg", "png", "webp")

        paths = []
        if os.path.isdir(band_dir):
            entries = []
            for fname in os.listdir(band_dir):
                name, ext = os.path.splitext(fname)
                if ext.lstrip(".").lower() in exts:
                    try:
                        entries.append((int(name), os.path.join(band_dir, fname)))
                    except ValueError:
                        entries.append((999, os.path.join(band_dir, fname)))
            paths = [p for _, p in sorted(entries)]

        # flat-file fallback
        if not paths:
            for ext in exts:
                p = os.path.join(styles_dir, f"{band}.{ext}")
                if os.path.exists(p):
                    paths = [p]
                    break

        result = []
        for path in paths:
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            mime = "image/png" if ext == "png" else "image/webp" if ext == "webp" else "image/jpeg"
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            result.append(f"data:{mime};base64,{data}")
        return result

    def _crop_portrait(self, source_image_b64: str) -> str:
        """Centre-crop to 2:3 portrait ratio."""
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(source_image_b64))).convert("RGB")
        w, h = img.size
        target_w = min(w, h * 2 // 3)
        target_h = target_w * 3 // 2
        left = (w - target_w) // 2
        top = max(0, (h - target_h) // 2)
        img = img.crop((left, top, left + target_w, top + target_h))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode()

    def _poll(self, prediction_id: str, timeout: int = 300) -> Optional[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                resp = requests.get(
                    f"{REPLICATE_BASE}/predictions/{prediction_id}",
                    headers=self._headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                logger.info("[NanoBanana] Poll — id=%s status=%s", prediction_id, status)

                if status == "succeeded":
                    output = data.get("output") or []
                    urls = output if isinstance(output, list) else [output]
                    logger.info(
                        "[NanoBanana] Done — %d images, first: %s",
                        len(urls), urls[0] if urls else "?",
                    )
                    return {"image_url": urls[0], "image_urls": urls, "band": None}

                if status in ("failed", "canceled"):
                    logger.error("[NanoBanana] Job %s: %s", status, data.get("error"))
                    return None

            except requests.RequestException as exc:
                logger.warning("[NanoBanana] Poll error: %s", exc)

        logger.error("[NanoBanana] Poll timed out after %ds", timeout)
        return None
