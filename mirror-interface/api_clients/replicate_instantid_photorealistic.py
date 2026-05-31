"""
Mirror Mirror — Face-preserving portrait via grandlineai/instant-id-photorealistic

Uses the Replicate model grandlineai/instant-id-photorealistic for photorealistic
face-locked portrait generation with InstantID IdentityNet + style prompts.

Model: grandlineai/instant-id-photorealistic
API:   https://replicate.com/grandlineai/instant-id-photorealistic
"""

import base64
import io
import logging
import time
import requests
from typing import Optional

from config import REPLICATE_API_KEY
from api_clients.gender import detect_gender

logger = logging.getLogger(__name__)

REPLICATE_BASE = "https://api.replicate.com/v1"
INSTANTID_PHOTO_VERSION = "03914a0c3326bf44383d0cd84b06822618af879229ce5d1d53bef38d93b68279"

# Style prompts per band — photorealistic language, identity locked by IdentityNet
STYLE_PROMPTS: dict[str, str] = {
    "renaissance": (
        "rococo oil portrait, painterly brushwork, three-quarter profile, "
        "portrait of a {gender} gazing softly upward, contemplative serene expression, "
        "silk brocade jacket with loose floral motifs, pastel pink roses, cream peonies, pale blue ground, "
        "delicate gold filigree at high open collar, luminous skin, soft directional light, "
        "slate blue atmospheric painterly background, oil on canvas, "
        "in the style of Fragonard and Boucher, 18th century aristocratic portraiture, museum quality, fine art"
    ),
    "botanique": (
        "strict left side profile portrait, head turned 90 degrees facing right, "
        "only one ear visible, only half of face visible, "
        "{gender} with curly chestnut hair{beard}, eyes gently closed, soft subtle smile, peaceful contemplative expression, "
        "double exposure photograph fused with vintage botanical illustration, "
        "flowers and leaves growing directly OUT of hair and scalp, "
        "orange poppies and marigolds blooming from curls, red hydrangea cluster emerging from collar, "
        "lush green leaves and ferns weaving through hair fibers, "
        "painted botanical specimens seamlessly integrated into the figure itself, "
        "structured dark military jacket with high stand collar, red collar trim, flowers bursting from inside the jacket, "
        "19th century herbarium plate aesthetic, antique scientific illustration style, painterly textured edges, "
        "warm earthy palette of burnt orange terracotta deep forest green olive cream ochre, "
        "soft diffuse natural lighting, cream off-white textured aged paper background, "
        "figure embedded into paper texture, subtle paper grain across entire image including face, "
        "halftone print texture overlay, editorial collage aesthetic, contemplative witness mood, "
        "museum quality natural history illustration meets photographic portrait, sharp focus, 8k"
    ),
    "bioluminescence": (
        "cinematic bioluminescent sci-fi portrait of a {gender}, tight centered close-up portrait, "
        "eye-level framing, shallow depth of field, strong subject isolation against darkness, "
        "expressive hopeful smile and wide reflective eyes, realistic skin texture under monochromatic lighting, "
        "futuristic illuminated suit with glowing cyan tubing and circuitry patterns, "
        "dark futuristic interior, deep black and navy background, immersive sci-fi atmosphere, "
        "intense electric-blue emissive lighting, cyan glow reflections across face and clothing, "
        "strong edge lighting with soft diffusion, monochromatic cobalt neon-blue palette, "
        "50mm cinematic portrait compression, soft bloom around bright illuminated elements, "
        "bright cyan illuminated tubing wrapping shoulders and chest, reflective translucent synthetic fabric, "
        "glossy specular highlights and glowing seams, facial reflections from nearby light sources, highly detailed"
    ),
    "surveillance": (
        "cyberpunk surveillance portrait of a {gender}, retro-futuristic VHS glitch aesthetic, "
        "tight frontal close-up framing, symmetrical centered composition, heavy CRT scanlines and analog grain, "
        "RGB displacement and digital tearing, dark digital interface environment, "
        "floating fragmented surveillance typography, hacked system aesthetic with corrupted overlays, "
        "harsh electronic monitor lighting, neon cyan and saturated red highlights, high contrast shadows, "
        "chromatic aberration, RGB channel separation, VHS warping and horizontal interference, "
        "red alphanumeric overlays scattered across frame, scanline texture covering entire composition, "
        "corrupted data fragments and flickering symbols, facial recognition surveillance atmosphere, highly detailed"
    ),
    "geometric": (
        "futuristic geometric portrait of a {gender}, cyberpunk editorial collage aesthetic, "
        "high-contrast minimalist poster design, fusion of photorealism and vector abstraction, "
        "close-up low-angle portrait, three-quarter profile composition, centralized asymmetrical framing, "
        "strong negative space against black background, crisp ultra-clean digital finish, "
        "monochrome realistic facial rendering, sharp jawline and soft illuminated skin texture, "
        "abstract geometric integration across head and body, pure black studio void background, "
        "dramatic directional lighting from upper left, high-contrast grayscale face rendering, "
        "saturated electric blue magenta orange white and red accents, "
        "interlocking polygonal shards surrounding face, layered vector paper-like structures, "
        "angular crystalline clothing forms, neon color fragmentation integrated into hair, highly detailed"
    ),
    "glitch": (
        "glitch art digital corruption portrait, heavy datamosh aesthetic, "
        "severe RGB chromatic aberration with cyan magenta red color channels split and offset by 30 pixels across face and hair, "
        "horizontal bands of displaced pixels slicing across entire image, "
        "pixel sorting streaks extending sideways from hair and shoulder edges, "
        "JPEG compression artifacts, scan line interference, VHS tracking errors, broken video signal aesthetic, "
        "three quarter view portrait of {gender} with curly dark hair{beard} neutral severe expression mouth closed, "
        "face partially intact partially fragmenting into pixel shards, "
        "shoulders disintegrating into crumbling debris and broken fragments, "
        "dark near-black background with electric neon highlights bursting through the corruption, "
        "monochrome charcoal grey skin tone base with vivid neon cyan magenta lime green hot pink color spikes only on glitch artifacts, "
        "dramatic high contrast hard side rim lighting from left, deep shadows, "
        "cyberpunk trickster fractured identity, broken transmission, corrupted file aesthetic, "
        "no glasses, bare shoulders, sharp jagged shattered edges, 8k"
    ),
}

NEGATIVE_PROMPT = (
    "blurry, bad quality, deformed, ugly, bad anatomy, watermark, text, "
    "extra limbs, disfigured, lowres, jpeg artefacts"
)

BAND_NEGATIVE_PROMPTS: dict[str, str] = {
    "botanique": (
        "front-facing, looking at camera, three-quarter view, both eyes visible, full face visible, "
        "head facing forward, glasses, eyewear, sunglasses, white shirt, plain shirt, modern clothing, "
        "t-shirt, casual clothing, flowers floating separately around figure, flowers next to figure, "
        "flowers as background decoration, botanical frame, isolated cutout, visible cutout edges, "
        "collage seams, figure pasted on background, harsh lighting, neon, digital glitch, cyberpunk, "
        "sci-fi, surveillance, cold blue palette, plain background, flat background, modern graphic design, "
        "vector art, cartoon, anime, blurry, low quality, distorted face, deformed, extra limbs, "
        "text, watermark, signature, frame border, sad expression, frowning, "
    ),
    "renaissance": (
        "sepia, amber, monochrome warm tones, photorealistic, photograph, military uniform, "
        "dense symmetrical embroidery, carved frame, architectural background, frontal pose, direct gaze, stiff, "
    ),
    "glitch": (
        "clean portrait, studio photography, soft lighting, soft shadows, smooth skin, "
        "intact whole figure, clean background, grey gradient background, plain background, "
        "beauty lighting, professional headshot, glasses, eyewear, sunglasses, "
        "warm tones, golden hour, natural lighting, "
        "marble bust, sculptural stone, statue, plaster, classical sculpture, photorealistic skin texture only, "
        "no glitch, no chromatic aberration, no datamosh, no pixel artifacts, "
        "botanical, flowers, organic, peaceful expression, smile, eyes closed, "
        "blurry, low quality, distorted face anatomy, deformed features beyond intentional glitch, "
        "extra limbs, text, watermark, signature, logo, "
    ),
}

# Per-band generation parameters — overrides the defaults in generate()
BAND_PARAMS: dict[str, dict] = {
    "botanique": {
        "ip_adapter_scale":              0.5,
        "controlnet_conditioning_scale": 0.45,
        "guidance_scale":                7.0,
        "num_inference_steps":           45,
    },
    "glitch": {
        "ip_adapter_scale":              0.35,
        "controlnet_conditioning_scale": 0.4,
        "guidance_scale":                8.5,
        "num_inference_steps":           48,
    },
}

# Gender-specific additions inserted into the prompt via {beard} placeholder
GENDER_ADDITIONS: dict[str, dict[str, str]] = {
    "botanique": {
        "man":   ", short beard",
        "woman": "",
    },
    "glitch": {
        "man":   ", short beard",
        "woman": "",
    },
}


class ReplicateInstantIDPhotorealisticClient:
    def __init__(self):
        self.api_key = REPLICATE_API_KEY
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
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
        Generate face-preserving portraits in the given band style.

        Uses grandlineai/instant-id-photorealistic (Juggernaut-XL, photorealistic weights).
        Each prediction yields 1 image; fires num_outputs predictions and collects all URLs.
        Returns {"image_url": str, "image_urls": list[str], "band": str} or None.
        """
        if not self.available():
            logger.warning("[InstantID-Photo] No API key — skipping")
            return None

        if gender is None:
            gender = detect_gender(source_image_b64)

        style_template = STYLE_PROMPTS.get(band, STYLE_PROMPTS["bioluminescence"])
        beard = GENDER_ADDITIONS.get(band, {}).get(gender, "")
        prompt = style_template.format(gender=gender, beard=beard)
        negative = BAND_NEGATIVE_PROMPTS.get(band, "") + NEGATIVE_PROMPT
        face_data_url = f"data:image/jpeg;base64,{self._crop_portrait(source_image_b64)}"

        # Per-band parameter overrides, falling back to global defaults
        params = BAND_PARAMS.get(band, {})

        def _submit_one() -> Optional[str]:
            payload = {
                "version": INSTANTID_PHOTO_VERSION,
                "input": {
                    "image":                         face_data_url,
                    "prompt":                        prompt,
                    "negative_prompt":               negative,
                    "width":                         640,
                    "height":                        960,
                    "num_inference_steps":           params.get("num_inference_steps", 30),
                    "guidance_scale":                params.get("guidance_scale", 5.0),
                    "ip_adapter_scale":              params.get("ip_adapter_scale", 0.8),
                    "controlnet_conditioning_scale": params.get("controlnet_conditioning_scale", 0.8),
                },
            }
            try:
                resp = requests.post(
                    f"{REPLICATE_BASE}/predictions",
                    headers=self._headers,
                    json=payload,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                output = data.get("output")

                if status == "succeeded" and output:
                    return output if isinstance(output, str) else output[0]

                pid = data.get("id")
                if pid and status not in ("failed", "canceled"):
                    result = self._poll(pid, band)
                    if result:
                        return result["image_urls"][0]

                logger.error("[InstantID-Photo] Prediction failed: %s", data.get("error"))
            except Exception as exc:
                logger.error("[InstantID-Photo] Request error: %s", exc, exc_info=True)
            return None

        logger.info("[InstantID-Photo] Submitting %d predictions — band=%s gender=%s", num_outputs, band, gender)

        # Fire all predictions in parallel using threads
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_outputs) as pool:
            futures = [pool.submit(_submit_one) for _ in range(num_outputs)]
            urls = [f.result() for f in concurrent.futures.as_completed(futures)]

        urls = [u for u in urls if u]
        if not urls:
            logger.error("[InstantID-Photo] All predictions failed")
            return None

        logger.info("[InstantID-Photo] Done — %d/%d images", len(urls), num_outputs)
        return {"image_url": urls[0], "image_urls": urls, "band": band}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    def _poll(self, prediction_id: str, band: Optional[str], timeout: int = 300) -> Optional[dict]:
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
                logger.info("[InstantID-Photo] Poll — id=%s status=%s", prediction_id, status)

                if status == "succeeded":
                    output = data.get("output") or []
                    urls = output if isinstance(output, list) else [output]
                    logger.info("[InstantID-Photo] Done — %d images, first: %s", len(urls), urls[0] if urls else "?")
                    return {"image_url": urls[0], "image_urls": urls, "band": band}

                if status in ("failed", "canceled"):
                    logger.error("[InstantID-Photo] Job %s: %s", status, data.get("error"))
                    return None

            except requests.RequestException as exc:
                logger.warning("[InstantID-Photo] Poll error: %s", exc)

        logger.error("[InstantID-Photo] Poll timed out after %ds", timeout)
        return None
