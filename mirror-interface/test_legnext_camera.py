"""
test_legnext_camera.py — Standalone test for Legnext AI + camera capture.

Captures a frame from the camera (picamera2 or OpenCV),
uploads the snapshot, then calls the Legnext API with --oref <face_url>.

Usage:
    cd mirror-interface
    python test_legnext_camera.py [--band BAND] [--gender GENDER] [--no-face]

Options:
    --band      One of: renaissance, botanique, bioluminescence,
                        surveillance, geometric, glitch  (default: bioluminescence)
    --gender    man | woman  (default: woman)
    --no-face   Skip camera capture; generate without face reference
"""

import argparse
import base64
import io
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_legnext")

# ── resolve project root ──────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import NEXTLEG_API_KEY, NEXTLEG_API_URL  # noqa: E402
from api_clients.portrait import PortraitClient, BAND_PROMPTS  # noqa: E402


# ── camera capture ────────────────────────────────────────────────────────────

def _capture_from_app(host: str = "http://localhost:5050") -> bytes:
    """Grab latest frame from the running mirror app's /camera/snapshot endpoint."""
    import requests as _req
    logger.info("Fetching snapshot from mirror app at %s/camera/snapshot…", host)
    resp = _req.get(f"{host}/camera/snapshot", timeout=10)
    resp.raise_for_status()
    logger.info("Snapshot fetched — %.1f KB", len(resp.content) / 1024)
    return resp.content


def _capture_opencv(device: "str | int" = 0) -> bytes:
    """Grab a single frame directly via OpenCV (use when mirror app is NOT running)."""
    import cv2  # type: ignore[import-untyped]
    import re

    # V4L2 backend needs an integer index; convert /dev/videoN → N
    dev: "str | int" = device
    if isinstance(device, str) and re.match(r"^/dev/video(\d+)$", device):
        dev = int(re.match(r"^/dev/video(\d+)$", device).group(1))

    logger.info("Opening camera device %s (index %s) via OpenCV…", device, dev)
    cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera device {device}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    for _ in range(5):
        cap.read()
        time.sleep(0.05)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera read failed")

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    logger.info("Captured frame: %dx%d", frame.shape[1], frame.shape[0])
    return buf.tobytes()


def _capture_picamera2() -> bytes:
    """Grab a single frame via picamera2 (Raspberry Pi hardware camera)."""
    from picamera2 import Picamera2
    from PIL import Image
    import numpy as np

    logger.info("Capturing via picamera2…")
    cam = Picamera2()
    cfg = cam.create_still_configuration(main={"size": (1280, 720)})
    cam.configure(cfg)
    cam.start()
    time.sleep(1.5)  # allow auto-exposure to settle
    arr = cam.capture_array("main")
    cam.stop()
    cam.close()

    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    logger.info("Captured frame: %dx%d", img.width, img.height)
    return buf.getvalue()


def capture_snapshot() -> bytes:
    """Try picamera2 first, fall back to OpenCV."""
    try:
        import importlib
        if importlib.util.find_spec("picamera2") is not None:
            return _capture_picamera2()
    except Exception as exc:
        logger.warning("picamera2 capture failed (%s) — falling back to OpenCV", exc)

    from config import CAMERA_DEVICE
    return _capture_opencv(CAMERA_DEVICE)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Test Legnext AI + camera --oref")
    parser.add_argument("--band", default="bioluminescence", choices=list(BAND_PROMPTS.keys()))
    parser.add_argument("--gender", default="woman", choices=["man", "woman"])
    parser.add_argument("--device", default="/dev/video8", help="Camera device (default: /dev/video8 Aukey)")
    parser.add_argument("--no-face", action="store_true", help="Generate without face reference")
    parser.add_argument("--replicate-key", default="", help="Replicate API key (or set REPLICATE_API_KEY env)")
    args = parser.parse_args()

    # Inject Replicate key before importing PortraitClient
    import os
    if args.replicate_key:
        os.environ["REPLICATE_API_KEY"] = args.replicate_key
        import config as _cfg
        _cfg.REPLICATE_API_KEY = args.replicate_key

    logger.info("=== Mirror portrait test ===")
    logger.info("Band  : %s", args.band)
    logger.info("Gender: %s", args.gender)
    logger.info("API   : %s", NEXTLEG_API_URL)
    logger.info("Key   : %s…", NEXTLEG_API_KEY[:12])

    # ── 1. Camera capture ────────────────────────────────────────────────────
    snapshot_b64: str | None = None
    if not args.no_face:
        try:
            # Use the running mirror app's endpoint (it holds the device lock)
            jpeg_bytes = _capture_from_app()
            snapshot_b64 = base64.b64encode(jpeg_bytes).decode()
            logger.info("Snapshot encoded — %.1f KB", len(jpeg_bytes) / 1024)

            # Save locally so you can inspect it
            snap_path = os.path.join(ROOT, "test_snapshot.jpg")
            with open(snap_path, "wb") as f:
                f.write(jpeg_bytes)
            logger.info("Snapshot saved → %s", snap_path)
        except Exception as exc:
            logger.error("Camera capture failed: %s", exc)
            logger.warning("Continuing without face reference")
            snapshot_b64 = None
    else:
        logger.info("--no-face: skipping camera capture")

    # ── 2. Upload + generate ─────────────────────────────────────────────────
    client = PortraitClient()
    logger.info("Calling PortraitClient.generate()…")
    t0 = time.time()

    result = client.generate(
        band=args.band,
        source_image_b64=snapshot_b64,
        gender=args.gender,
    )

    elapsed = time.time() - t0
    logger.info("=== Result (%.1fs) ===", elapsed)
    logger.info("Band      : %s", result.get("band"))
    logger.info("Image URL : %s", result.get("image_url"))

    extra_urls = result.get("image_urls", [])
    if len(extra_urls) > 1:
        logger.info("All URLs  :")
        for url in extra_urls:
            logger.info("  %s", url)

    if result.get("image_url") == "/static/assets/fallback/portrait_example.jpg":
        logger.warning("Received fallback portrait — generation likely failed (check logs above)")
        sys.exit(1)

    print("\n✓ image_url:", result.get("image_url"))


if __name__ == "__main__":
    main()
