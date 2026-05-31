"""
Mirror Mirror — Band post-processing effects.

Each band applies a PIL-based color grade + texture pass on top of the
InstantID portrait. Parameters for each band live in the CONFIG dicts
below — tune them without touching the rendering functions.

apply_band_effect(img, band) is the main entry point.
apply_to_bytes(image_bytes, band) wraps it for raw bytes in / bytes out.
"""

import io
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance


# ── Font helpers ───────────────────────────────────────────────────────────────
_MONO_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
]
_CORRUPT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789↓↑+×◆▲▼►◄░▒▓"

def _load_font(size: int):
    for path in _MONO_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _corrupt(n: int) -> str:
    return "".join(random.choice(_CORRUPT_CHARS) for _ in range(n))


# ══════════════════════════════════════════════════════════════════════════════
# Band configs
# ══════════════════════════════════════════════════════════════════════════════

# ── Glitch ────────────────────────────────────────────────────────────────────
GLITCH_CONFIG = {
    "scanline_dim":         0.78,
    "scanline_flash_count": (3, 7),
    "scanline_flash_boost": 1.5,
    "chroma_shift_divisor": 45,
    "chroma_shift_min":     8,
    "band_count":           (8, 18),
    "band_height":          (1, 12),
    "band_lateral":         (-80, 80),
    "band_boost":           1.2,
    "red_mul":              1.15,
    "green_mul":            0.92,
    "blue_mul":             1.05,
    "grain_sigma":          8.0,
    "text_size_divisor":    28,
    "text_color":           (255, 0, 255),
    "text_pad":             12,
}

# ── Surveillance ──────────────────────────────────────────────────────────────
SURVEILLANCE_CONFIG = {
    "desaturate":           0.25,   # ImageEnhance.Color multiplier (0=BW, 1=full)
    "contrast":             1.7,
    "scanline_dim":         0.82,   # subtle scan lines
    "scanline_flash_count": (1, 3),
    "scanline_flash_boost": 1.2,
    "chroma_shift_px":      4,      # chromatic aberration pixels
    "band_count":           (3, 7),
    "band_height":          (1, 6),
    "band_lateral":         (-12, 12),
    "red_mul":              0.88,
    "green_mul":            1.08,   # slight cyan/green surveillance tint
    "blue_mul":             1.04,
    "grain_sigma":          14.0,
    "vignette_strength":    0.55,
    "text_size_divisor":    30,
    "text_color":           (0, 255, 180),
    "text_pad":             10,
    "text_top":             "FERET  REC ●  {hh}:{mm}:{ss}  CAM-{cam:02d}",
    "text_bottom":          "ID: {corrupt}   CONF: {conf:.2f}   FRAME: {frame:05d}",
}

# ── Bioluminescence ───────────────────────────────────────────────────────────
BIOLUMINESCENCE_CONFIG = {
    "red_mul":              0.78,
    "green_mul":            1.12,
    "blue_mul":             1.22,
    "contrast":             1.5,
    "vignette_strength":    0.72,   # strong dark border to isolate subject
    "bloom_radius":         8,      # blur radius for glow effect
    "bloom_strength":       0.30,   # blend amount of bloom layer
    "grain_sigma":          3.5,
}

# ── Renaissance ───────────────────────────────────────────────────────────────
RENAISSANCE_CONFIG = {
    "red_mul":              1.12,
    "green_mul":            1.04,
    "blue_mul":             0.82,
    "contrast":             1.35,
    "saturation":           1.10,
    "vignette_strength":    0.60,
    "vignette_color":       (20, 10, 0),  # warm dark corners
    "grain_sigma":          4.0,
}

# ── Botanique ─────────────────────────────────────────────────────────────────
BOTANIQUE_CONFIG = {
    "red_mul":              1.06,
    "green_mul":            1.03,
    "blue_mul":             0.90,
    "contrast":             1.15,
    "saturation":           0.88,   # slightly muted / vintage
    "vignette_strength":    0.45,
    "vignette_color":       (30, 20, 5),
    "grain_sigma":          6.5,    # paper/film grain
}

# ── Geometric ─────────────────────────────────────────────────────────────────
GEOMETRIC_CONFIG = {
    "desaturate":           0.08,   # near-monochrome
    "contrast":             1.55,
    "grain_sigma":          5.0,
    "accent_color":         (217, 66, 86),  # red accent shards
    "accent_opacity":       0.12,
}


# ══════════════════════════════════════════════════════════════════════════════
# Shared primitives
# ══════════════════════════════════════════════════════════════════════════════

def _channel_multiply(arr: np.ndarray, r: float, g: float, b: float) -> np.ndarray:
    arr = arr.copy()
    arr[:, :, 0] = np.clip(arr[:, :, 0] * r, 0, 255)
    arr[:, :, 1] = np.clip(arr[:, :, 1] * g, 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] * b, 0, 255)
    return arr


def _add_grain(arr: np.ndarray, sigma: float) -> np.ndarray:
    return np.clip(arr + np.random.normal(0, sigma, arr.shape), 0, 255)


def _vignette(img: Image.Image, strength: float, color: tuple = (0, 0, 0)) -> Image.Image:
    """Rectangular edge vignette — darkens corners without creating an oval."""
    w, h = img.size
    arr = np.array(img, dtype=np.float32)
    xs = np.abs(np.linspace(-1, 1, w))
    ys = np.abs(np.linspace(-1, 1, h))
    xx, yy = np.meshgrid(xs, ys)
    # max of both axes = Chebyshev distance → rectangular gradient, no oval
    dist = np.maximum(xx, yy)
    alpha = np.clip((dist ** 1.8) * strength, 0, 1)[..., np.newaxis]
    overlay = np.array(color, dtype=np.float32)
    result = arr * (1 - alpha) + overlay * alpha
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


def ImageChops_invert(mask: Image.Image) -> Image.Image:
    """Invert a grayscale mask."""
    arr = 255 - np.array(mask)
    return Image.fromarray(arr.astype(np.uint8))


def _bloom(img: Image.Image, radius: int, strength: float) -> Image.Image:
    """Additive glow on bright regions."""
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    arr_orig = np.array(img, dtype=np.float32)
    arr_blur = np.array(blurred, dtype=np.float32)
    bright_mask = (arr_orig.max(axis=2, keepdims=True) / 255.0) ** 2
    result = np.clip(arr_orig + arr_blur * bright_mask * strength, 0, 255)
    return Image.fromarray(result.astype(np.uint8))


def _scanlines(arr: np.ndarray, cfg: dict) -> np.ndarray:
    arr = arr.copy()
    arr[::2] *= cfg["scanline_dim"]
    n_flash = random.randint(*cfg["scanline_flash_count"])
    h = arr.shape[0]
    for _ in range(n_flash):
        y = random.randint(0, h - 3)
        arr[y:y + 2] = np.clip(arr[y:y + 2] * cfg["scanline_flash_boost"], 0, 255)
    return arr


def _dropout_bands(arr: np.ndarray, cfg: dict) -> np.ndarray:
    arr = arr.copy()
    h = arr.shape[0]
    n = random.randint(*cfg["band_count"])
    for _ in range(n):
        y = random.randint(0, h - cfg["band_height"][1])
        bh = random.randint(*cfg["band_height"])
        lat = random.randint(*cfg["band_lateral"])
        arr[y:y + bh] = np.roll(arr[y:y + bh], lat, axis=1)
        arr[y:y + bh] = np.clip(arr[y:y + bh] * cfg.get("band_boost", 1.3), 0, 255)
    return arr


def _chroma_shift(arr: np.ndarray, shift: int) -> np.ndarray:
    r = np.roll(arr[:, :, 0],  shift, axis=1)
    g = arr[:, :, 1].copy()
    b = np.roll(arr[:, :, 2], -shift, axis=1)
    return np.stack([r, g, b], axis=2)


# ══════════════════════════════════════════════════════════════════════════════
# Band effect functions
# ══════════════════════════════════════════════════════════════════════════════

def _glitch(img: Image.Image) -> Image.Image:
    cfg = GLITCH_CONFIG
    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    shift = max(cfg["chroma_shift_min"], w // cfg["chroma_shift_divisor"])
    arr = _chroma_shift(arr, shift)
    arr = _scanlines(arr, cfg)
    arr = _dropout_bands(arr, cfg)
    arr = _channel_multiply(arr, cfg["red_mul"], cfg["green_mul"], cfg["blue_mul"])
    arr = _add_grain(arr, cfg["grain_sigma"])

    img = Image.fromarray(arr.astype(np.uint8))
    draw = ImageDraw.Draw(img)
    font_size = max(13, w // cfg["text_size_divisor"])
    font = _load_font(font_size)
    pad = cfg["text_pad"]
    color = cfg["text_color"]

    top = f"{_corrupt(6)}  {'↓' if random.random()>.4 else '+'} + +    {random.randint(10,29)} {random.randint(10,23):02d}:{random.randint(0,59):02d}..."
    bottom = f"{random.randint(10,99)} {random.randint(10,99)} {_corrupt(3)}   {'.'*random.randint(3,7)}   {_corrupt(4)}"
    draw.text((pad, pad), top, fill=color, font=font)
    draw.text((pad, h - font_size - pad), bottom, fill=color, font=font)
    return img


def _surveillance(img: Image.Image) -> Image.Image:
    cfg = SURVEILLANCE_CONFIG
    h, w = img.size[1], img.size[0]

    img = ImageEnhance.Color(img).enhance(cfg["desaturate"])
    img = ImageEnhance.Contrast(img).enhance(cfg["contrast"])

    arr = np.array(img, dtype=np.float32)
    shift = cfg["chroma_shift_px"]
    arr = _chroma_shift(arr, shift)
    arr = _scanlines(arr, cfg)
    arr = _dropout_bands(arr, cfg)
    arr = _channel_multiply(arr, cfg["red_mul"], cfg["green_mul"], cfg["blue_mul"])
    arr = _add_grain(arr, cfg["grain_sigma"])

    img = Image.fromarray(arr.astype(np.uint8))
    img = _vignette(img, cfg["vignette_strength"])

    draw = ImageDraw.Draw(img)
    font_size = max(13, w // cfg["text_size_divisor"])
    font = _load_font(font_size)
    pad = cfg["text_pad"]
    color = cfg["text_color"]

    top = cfg["text_top"].format(
        hh=random.randint(0,23), mm=random.randint(0,59), ss=random.randint(0,59),
        cam=random.randint(1,16),
    )
    bottom = cfg["text_bottom"].format(
        corrupt=_corrupt(8), conf=random.uniform(0.85, 0.99),
        frame=random.randint(1000, 99999),
    )
    draw.text((pad, pad), top, fill=color, font=font)
    draw.text((pad, h - font_size - pad), bottom, fill=color, font=font)
    return img


def _bioluminescence(img: Image.Image) -> Image.Image:
    cfg = BIOLUMINESCENCE_CONFIG
    img = ImageEnhance.Contrast(img).enhance(cfg["contrast"])
    arr = np.array(img, dtype=np.float32)
    arr = _channel_multiply(arr, cfg["red_mul"], cfg["green_mul"], cfg["blue_mul"])
    arr = _add_grain(arr, cfg["grain_sigma"])
    img = Image.fromarray(arr.astype(np.uint8))
    img = _bloom(img, cfg["bloom_radius"], cfg["bloom_strength"])
    img = _vignette(img, cfg["vignette_strength"])
    return img


def _renaissance(img: Image.Image) -> Image.Image:
    cfg = RENAISSANCE_CONFIG
    img = ImageEnhance.Color(img).enhance(cfg["saturation"])
    img = ImageEnhance.Contrast(img).enhance(cfg["contrast"])
    arr = np.array(img, dtype=np.float32)
    arr = _channel_multiply(arr, cfg["red_mul"], cfg["green_mul"], cfg["blue_mul"])
    arr = _add_grain(arr, cfg["grain_sigma"])
    img = Image.fromarray(arr.astype(np.uint8))
    img = _vignette(img, cfg["vignette_strength"], cfg.get("vignette_color", (0, 0, 0)))
    return img


def _botanique(img: Image.Image) -> Image.Image:
    cfg = BOTANIQUE_CONFIG
    img = ImageEnhance.Color(img).enhance(cfg["saturation"])
    img = ImageEnhance.Contrast(img).enhance(cfg["contrast"])
    arr = np.array(img, dtype=np.float32)
    arr = _channel_multiply(arr, cfg["red_mul"], cfg["green_mul"], cfg["blue_mul"])
    arr = _add_grain(arr, cfg["grain_sigma"])
    img = Image.fromarray(arr.astype(np.uint8))
    img = _vignette(img, cfg["vignette_strength"], cfg.get("vignette_color", (0, 0, 0)))
    return img


def _geometric(img: Image.Image) -> Image.Image:
    cfg = GEOMETRIC_CONFIG
    img = ImageEnhance.Color(img).enhance(cfg["desaturate"])
    img = ImageEnhance.Contrast(img).enhance(cfg["contrast"])
    arr = np.array(img, dtype=np.float32)
    arr = _add_grain(arr, cfg["grain_sigma"])
    # Subtle accent colour wash
    accent = np.array(cfg["accent_color"], dtype=np.float32)
    arr = np.clip(arr + accent * cfg["accent_opacity"], 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

_BAND_FN = {
    "glitch":          _glitch,
    "surveillance":    _surveillance,
    "bioluminescence": _bioluminescence,
    "renaissance":     _renaissance,
    "botanique":       _botanique,
    "geometric":       _geometric,
}


def _crop_2x3(img: Image.Image) -> Image.Image:
    """Centre-crop to 2:3 portrait ratio."""
    w, h = img.size
    target_w = min(w, h * 2 // 3)
    target_h = target_w * 3 // 2
    left = (w - target_w) // 2
    top = max(0, (h - target_h) // 2)
    return img.crop((left, top, left + target_w, top + target_h))


def apply_band_effect(img: Image.Image, band: str) -> Image.Image:
    """Crop to 2:3 then apply the band post-processing effect. Returns a new PIL Image."""
    img = _crop_2x3(img.convert("RGB"))
    fn = _BAND_FN.get(band)
    if fn is None:
        return img
    return fn(img)


def apply_to_bytes(image_bytes: bytes, band: str = "glitch", output_format: str = "JPEG") -> bytes:
    """Apply band effect to raw image bytes. Returns processed image bytes."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    out = apply_band_effect(img, band)
    buf = io.BytesIO()
    out.save(buf, format=output_format, quality=90)
    return buf.getvalue()
