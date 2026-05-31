"""
Mirror Mirror — Face-preserving portrait via Replicate

Two-step pipeline when a band LoRA is available:
  Step 1 — PuLID+FLUX: lock in visitor's face identity (1 neutral portrait)
  Step 2 — Band LoRA img2img: apply Midjourney-trained style (4 outputs)

Single-step fallback (no LoRA for band):
  PuLID+FLUX with band style prompt — 4 outputs, 2:3 ratio

Models:
  bytedance/pulid      — face identity injection into FLUX
  breal8/glitch        — Midjourney glitch style LoRA
  breal8/futuristicskin — Midjourney futuristic skin style LoRA
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
PULID_VERSION = "43d309c37ab4e62361e5e29b8e9e867fb2dcbcec77ae91206a8d95ac5dd451a0"
INSTANTID_VERSION = "2e4785a4d80dadf580077b2244c8d7c05d8e3faac04a04c02d8e099dd2876789"

# InstantID style prompts per band — SDXL-optimised language
# Face identity is locked by InstantID's IdentityNet; prompt drives the aesthetic.
INSTANTID_STYLE_PROMPTS: dict[str, str] = {
    "renaissance": (
        "rococo oil portrait, painterly brushwork, three-quarter profile, "
        "portrait of a {gender} gazing softly upward, contemplative serene expression, "
        "silk brocade jacket with loose floral motifs, pastel pink roses, cream peonies, pale blue ground, "
        "delicate gold filigree at high open collar, luminous skin, soft directional light, "
        "slate blue atmospheric painterly background, oil on canvas, "
        "in the style of Fragonard and Boucher, 18th century aristocratic portraiture, museum quality, fine art"
    ),
    "botanique": (
        "double exposure portrait, profile view, portrait of a {gender} with curly hair, "
        "eyes closed, serene contemplative expression, soft open-collar linen shirt unbuttoned at the throat, "
        "silhouette of head and shoulders filled with vintage botanical illustrations, "
        "poppies marigolds hydrangeas and trailing foliage, "
        "flowers and leaves blooming from inside the figure, emerging through the hair and shoulders, "
        "half the torso dissolving into botanical overlay, "
        "cream textured paper background, painterly canvas grain, "
        "muted earth palette with red and orange accents, "
        "hard silhouette edge against negative space, "
        "double exposure photography, mixed media editorial illustration, contemporary fine art"
    ),
    "bioluminescence": (
        # STYLE + SHOT
        "cinematic bioluminescent sci-fi portrait of a {gender}, tight centered close-up portrait, "
        "eye-level framing, shallow depth of field, strong subject isolation against darkness, subtle cinematic grain and bloom, "
        # SUBJECT
        "expressive hopeful smile and wide reflective eyes, realistic skin texture under monochromatic lighting, "
        "futuristic illuminated suit with glowing cyan tubing and circuitry patterns, soft illuminated hair edges, "
        # SCENE
        "dark futuristic interior, minimal visible environment, deep black and navy background, immersive sci-fi atmosphere, "
        # CINEMATOGRAPHY
        "intense electric-blue emissive lighting, cyan glow reflections across face and clothing, "
        "strong edge lighting with soft diffusion, monochromatic cobalt neon-blue palette, atmospheric low-key contrast, "
        # LENS EFFECTS
        "50mm cinematic portrait compression, soft bloom around bright illuminated elements, "
        "smooth bokeh in background shadows, fine highlight rolloff on glowing suit materials, "
        # VISUAL DETAILS
        "bright cyan illuminated tubing wrapping shoulders and chest, reflective translucent synthetic fabric, "
        "glossy specular highlights and glowing seams, facial reflections from nearby light sources, "
        "soft atmospheric glow surrounding illuminated suit components, highly detailed"
    ),
    "surveillance": (
        # STYLE + SHOT
        "cyberpunk surveillance portrait of a {gender}, retro-futuristic VHS glitch aesthetic, corrupted biometric scan visual language, "
        "tight frontal close-up framing, symmetrical centered composition, heavy CRT scanlines and analog grain, "
        "RGB displacement and digital tearing, "
        # SCENE
        "dark digital interface environment, floating fragmented surveillance typography, "
        "hacked system aesthetic with corrupted overlays, "
        # CINEMATOGRAPHY
        "harsh electronic monitor lighting, neon cyan and saturated red highlights, high contrast shadows and digital bloom, "
        # LENS EFFECTS
        "chromatic aberration, RGB channel separation, VHS warping and horizontal interference, "
        "pixel noise and compression artifacts, "
        # VISUAL DETAILS
        "red alphanumeric overlays scattered across frame, scanline texture covering entire composition, "
        "corrupted data fragments and flickering symbols, facial recognition surveillance atmosphere, "
        "distorted typography integrated into portrait, layered static signal breakup and analog glitch artifacts, highly detailed"
    ),
    "geometric": (
        # STYLE + SHOT
        "futuristic geometric portrait of a {gender}, cyberpunk editorial collage aesthetic, "
        "high-contrast minimalist poster design, fusion of photorealism and vector abstraction, "
        "close-up low-angle portrait, three-quarter profile composition, centralized asymmetrical framing, "
        "strong negative space against black background, crisp ultra-clean digital finish, "
        # SUBJECT + SCENE
        "monochrome realistic facial rendering, sharp jawline and soft illuminated skin texture, "
        "abstract geometric integration across head and body, pure black studio void background, "
        "floating abstract composition, graphic poster presentation, "
        # CINEMATOGRAPHY
        "dramatic directional lighting from upper left, high-contrast grayscale face rendering, "
        "saturated electric blue magenta orange white and red accents, deep blacks with luminous color blocking, "
        # LENS EFFECTS
        "portrait lens compression, sharp facial detail retention, clean edge separation, "
        "digital compositing precision, subtle glossy reflections on geometric planes, "
        # VISUAL DETAILS
        "interlocking polygonal shards surrounding face, layered vector paper-like structures, "
        "angular crystalline clothing forms, neon color fragmentation integrated into hair, "
        "smooth skin texture contrasted with hard-edged geometry, "
        "dynamic asymmetrical balance with sculptural abstraction, highly detailed"
    ),
    "glitch": (
        # STYLE + SHOT
        "post-human digital fragmentation portrait of a {gender}, contemporary glitch-art datamoshing aesthetic, "
        "data-corruption visual language, profile three-quarter view facing right, "
        "medium close-up framing on head and shoulders, dynamic horizontal fragmentation with severe lateral pixel stretching, "
        # SUBJECT
        "neutral to contemplative expression with eyes partially obscured by digital tearing, "
        "physical form transitioning from solid stone-like texture to fragmented data, "
        # SCENE
        "vast dark void environment, minimalist backdrop emphasising subject disintegration, "
        "atmosphere of a system error and memory being erased in real-time, "
        # CINEMATOGRAPHY
        "cool desaturated base lighting reminiscent of moonlight or dim studio, "
        "high-key digital highlights within glitch fragments, "
        "deep moody shadows providing physical weight against digital noise, "
        # LENS EFFECTS
        "extreme RGB displacement red green and blue channel separation, "
        "horizontal signal smearing and interlaced digital artifacts, "
        "subtle film grain and micro-noise textures across solid surfaces, "
        # VISUAL DETAILS
        "vibrant rainbow glitch bars cutting horizontally across face and neck, "
        "deconstructed anatomy pieces of shoulder and jaw flaking into digital dust, "
        "hyper-realistic hair and skin pores clashing with flat 8-bit color block corruption, "
        "motion trails suggesting rapid lateral movement or failing transmission signal, "
        "face clearly visible, highly detailed"
    ),
}

# PuLID style prompts — used for bands without a LoRA, or as neutral base for LoRA step
BAND_STYLE_PROMPTS: dict[str, str] = {
    "renaissance":     (
        "rococo oil painting portrait, warm golden candlelight, renaissance masterpiece, "
        "gilded ornate frame, rich fabric, classical composition, highly detailed, cinematic"
    ),
    "botanique":       (
        "botanical portrait, lush floral crown and vines, soft watercolour wash, "
        "botanical illustration, organic, diffused natural light, painterly"
    ),
    "bioluminescence": (
        "bioluminescent portrait, deep-ocean ethereal glow, blue-green luminescence, "
        "otherworldly magical light, dark background, glowing skin, cinematic, detailed"
    ),
    "surveillance":    (
        "formal institutional portrait, high-resolution documentary photography, "
        "FERET database aesthetic, neutral expression, stark studio lighting, archival"
    ),
    "geometric":       (
        "geometric abstract portrait, constructivist art, sharp mathematical patterns, "
        "bold primary colours, cold precision, Mondrian-inspired, highly detailed"
    ),
    "glitch":          (
        "glitch-art portrait, digital signal corruption, RGB channel split, "
        "pixel fragmentation, cyberpunk neon, head dissolving into noise, cinematic"
    ),
}

NEUTRAL_PROMPT = (
    "portrait of a {gender}, natural studio lighting, photorealistic, "
    "highly detailed face, sharp focus, neutral background"
)

NEGATIVE_PROMPT = (
    "blurry, bad quality, deformed, ugly, bad anatomy, watermark, text, "
    "extra limbs, disfigured, lowres, jpeg artefacts"
)

# Per-band negative prompts — merged with NEGATIVE_PROMPT at generation time
BAND_NEGATIVE_PROMPTS: dict[str, str] = {
    "botanique": (
        "military uniform, epaulettes, red piping, buttoned tunic, high collar, ornate jacket, "
        "frame, decorative border, flowers around figure, symmetrical composition, "
        "dense background, photograph of person with flowers, frontal portrait, direct gaze, "
        "sharp photorealistic skin, worst quality, low quality, blurry, distorted face, watermark, "
    ),
    "renaissance": (
        "sepia, amber, monochrome warm tones, photorealistic, photograph, military uniform, "
        "dense symmetrical embroidery, carved frame, architectural background, frontal pose, direct gaze, stiff, "
    ),
}


class ReplicatePortraitClient:
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
    ) -> Optional[dict]:
        """
        Generate 4 face-preserving portraits in the given band style.

        If the band has a trained LoRA, uses two-step pipeline:
          PuLID (face lock) → LoRA img2img (Midjourney style)
        Otherwise uses PuLID directly with a style prompt.

        Returns {"image_url": str, "image_urls": list[str], "band": str} or None.
        """
        if not self.available():
            logger.warning("[Replicate] No API key — skipping")
            return None

        if gender is None:
            gender = detect_gender(source_image_b64)

        # InstantID: face identity locked via IdentityNet, style driven by prompt
        result = self._instantid_styled(band, source_image_b64, gender)
        if result:
            return result

        # Fallback: PuLID+FLUX
        logger.warning("[Replicate] InstantID failed — falling back to PuLID")
        return self._pulid_generate(band, source_image_b64, gender, num_samples=4)

    # ------------------------------------------------------------------
    # InstantID styled portrait
    # ------------------------------------------------------------------

    def _instantid_styled(
        self,
        band: str,
        source_image_b64: str,
        gender: str,
    ) -> Optional[dict]:
        """InstantID: face locked by IdentityNet, style driven by band prompt. 4 outputs."""
        style_template = INSTANTID_STYLE_PROMPTS.get(band, INSTANTID_STYLE_PROMPTS["bioluminescence"])
        prompt = style_template.format(gender=gender)
        face_data_url = f"data:image/jpeg;base64,{self._crop_portrait(source_image_b64)}"

        payload = {
            "version": INSTANTID_VERSION,
            "input": {
                "image":                        face_data_url,
                "prompt":                       prompt,
                "negative_prompt":              BAND_NEGATIVE_PROMPTS.get(band, "illustration, drawing, cartoon, painting, neon, cyberpunk, digital art, anime, rendered, ") + NEGATIVE_PROMPT,
                "sdxl_weights":                 "RealVisXL_V4.0_Lightning",
                "num_outputs":                  4,
                "num_inference_steps":          30,
                "guidance_scale":               7.0,
                "ip_adapter_scale":             0.9,
                "controlnet_conditioning_scale": 0.9,
                "enhance_nonface_region":       True,
                "disable_safety_checker":       True,
            },
        }

        try:
            logger.info("[Replicate] InstantID styled — band=%s gender=%s", band, gender)
            resp = requests.post(
                f"{REPLICATE_BASE}/predictions",
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            pid = data.get("id")
            status = data.get("status")
            output = data.get("output")

            if status == "succeeded" and output:
                urls = output if isinstance(output, list) else [output]
                logger.info("[Replicate] InstantID done (sync) — %d images", len(urls))
                processed = self._apply_crt(urls, band)
                return processed or {"image_url": urls[0], "image_urls": urls, "band": band}

            if pid and status not in ("failed", "canceled"):
                result = self._poll(pid, band)
                if result:
                    processed = self._apply_crt(result["image_urls"], band)
                    return processed or result
                return result

            logger.error("[Replicate] InstantID failed immediately: %s", data.get("error"))
            return None

        except Exception as exc:
            logger.error("[Replicate] InstantID request error: %s", exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # PuLID single-step
    # ------------------------------------------------------------------

    def _pulid_generate(
        self,
        band: Optional[str],
        source_image_b64: str,
        gender: str,
        num_samples: int = 4,
        prompt_override: Optional[str] = None,
    ) -> Optional[dict]:
        if prompt_override:
            prompt = prompt_override
        else:
            style = BAND_STYLE_PROMPTS.get(band, BAND_STYLE_PROMPTS["bioluminescence"])
            prompt = f"portrait of a {gender}, {style}"

        face_data_url = f"data:image/jpeg;base64,{self._crop_portrait(source_image_b64)}"

        payload = {
            "version": PULID_VERSION,
            "input": {
                "main_face_image":  face_data_url,
                "prompt":           prompt,
                "negative_prompt":  NEGATIVE_PROMPT,
                "num_samples":      num_samples,
                "num_steps":        20,
                "cfg_scale":        1.2,
                "identity_scale":   1.0,
                "image_width":      512,
                "image_height":     768,
                "output_format":    "jpg",
                "output_quality":   90,
            },
        }

        try:
            logger.info("[Replicate] Submitting PuLID — band=%s gender=%s samples=%d", band, gender, num_samples)
            resp = requests.post(
                f"{REPLICATE_BASE}/predictions",
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            pid = data.get("id")
            status = data.get("status")
            output = data.get("output")

            if status == "succeeded" and output:
                urls = output if isinstance(output, list) else [output]
                logger.info("[Replicate] PuLID done (sync) — %d images", len(urls))
                return {"image_url": urls[0], "image_urls": urls, "band": band}

            if pid and status not in ("failed", "canceled"):
                return self._poll(pid, band)

            logger.error("[Replicate] PuLID failed immediately: %s", data.get("error"))
            return None

        except Exception as exc:
            logger.error("[Replicate] PuLID request error: %s", exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # CRT post-processing
    # ------------------------------------------------------------------

    def _apply_crt(self, urls: list, band: str) -> Optional[dict]:
        """Download InstantID outputs, apply CRT effect, upload processed images."""
        from api_clients.crt_effect import apply_to_bytes
        processed_urls = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                raw = apply_to_bytes(resp.content, band=band)
                hosted = self._upload_catbox(raw)
                if hosted:
                    processed_urls.append(hosted)
                    logger.info("[Replicate] CRT processed → %s", hosted[:70])
                else:
                    processed_urls.append(url)  # keep original if upload fails
            except Exception as exc:
                logger.warning("[Replicate] CRT processing failed for %s: %s", url[:60], exc)
                processed_urls.append(url)

        if not processed_urls:
            return None
        return {"image_url": processed_urls[0], "image_urls": processed_urls, "band": band}

    def _upload_catbox(self, image_bytes: bytes) -> Optional[str]:
        """Upload bytes to catbox.moe and return the public URL."""
        try:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": ("portrait.jpg", image_bytes, "image/jpeg")},
                timeout=30,
            )
            if resp.status_code == 200 and resp.text.startswith("https://"):
                return resp.text.strip()
        except Exception as exc:
            logger.warning("[Replicate] catbox upload failed: %s", exc)

        # Fallback: litterbox (24h)
        try:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "24h"},
                files={"fileToUpload": ("portrait.jpg", image_bytes, "image/jpeg")},
                timeout=30,
            )
            if resp.status_code == 200 and resp.text.startswith("https://"):
                return resp.text.strip()
        except Exception as exc:
            logger.warning("[Replicate] litterbox upload failed: %s", exc)

        return None

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
                logger.info("[Replicate] Poll — id=%s status=%s", prediction_id, status)

                if status == "succeeded":
                    output = data.get("output") or []
                    urls = output if isinstance(output, list) else [output]
                    logger.info("[Replicate] Done — %d images, first: %s", len(urls), urls[0] if urls else "?")
                    return {"image_url": urls[0], "image_urls": urls, "band": band}

                if status in ("failed", "canceled"):
                    logger.error("[Replicate] Job %s: %s", status, data.get("error"))
                    return None

            except requests.RequestException as exc:
                logger.warning("[Replicate] Poll error: %s", exc)

        logger.error("[Replicate] Poll timed out after %ds", timeout)
        return None
