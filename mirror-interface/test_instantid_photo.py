"""
test_instantid_photo.py — Camera test for grandlineai/instant-id-photorealistic

Captures a frame from the running mirror app (or directly from the camera),
then runs it through the new photorealistic InstantID model on Replicate.

Usage:
    cd mirror-interface
    python test_instantid_photo.py [--band BAND] [--gender GENDER] [--device DEV]

Options:
    --band      renaissance | botanique | bioluminescence |
                surveillance | geometric | glitch  (default: bioluminescence)
    --gender    man | woman  (default: woman)
    --device    Camera device, e.g. /dev/video8 (default: from config)
    --no-face   Skip camera capture; send a blank test (will likely fail — just for API check)
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
logger = logging.getLogger("test_instantid_photo")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import REPLICATE_API_KEY, CAMERA_DEVICE  # noqa: E402
from api_clients.replicate_instantid_photorealistic import ReplicateInstantIDPhotorealisticClient  # noqa: E402
from api_clients.gender import detect_gender as _detect_gender_b64  # noqa: E402


def detect_gender(jpeg_bytes: bytes) -> str:
    """Detect gender from raw JPEG bytes. Wraps the shared b64 utility."""
    return _detect_gender_b64(base64.b64encode(jpeg_bytes).decode())


# ── camera capture ────────────────────────────────────────────────────────────

def _capture_from_app(host: str = "http://localhost:5050") -> bytes:
    """Grab latest frame from the running mirror app's /camera/snapshot endpoint."""
    import requests as _req
    logger.info("Fetching snapshot from mirror app at %s/camera/snapshot…", host)
    resp = _req.get(f"{host}/camera/snapshot", timeout=10)
    resp.raise_for_status()
    logger.info("Snapshot fetched — %.1f KB", len(resp.content) / 1024)
    return resp.content


def _capture_opencv(device) -> bytes:
    """Grab a single frame directly via OpenCV."""
    import cv2
    import re

    dev = device
    if isinstance(device, str) and re.match(r"^/dev/video(\d+)$", device):
        dev = int(re.match(r"^/dev/video(\d+)$", device).group(1))

    logger.info("Opening camera device %s via OpenCV…", device)
    cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera device {device}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # Flush a few frames so auto-exposure settles
    for _ in range(8):
        cap.read()
        time.sleep(0.05)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera read failed")

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
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
    time.sleep(1.5)
    arr = cam.capture_array("main")
    cam.stop()
    cam.close()

    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    logger.info("Captured frame: %dx%d", img.width, img.height)
    return buf.getvalue()


def capture_snapshot(device) -> bytes:
    """Try mirror app endpoint first, then picamera2, then OpenCV."""
    try:
        return _capture_from_app()
    except Exception as exc:
        logger.warning("Mirror app snapshot failed (%s) — trying camera directly", exc)

    try:
        import importlib
        if importlib.util.find_spec("picamera2") is not None:
            return _capture_picamera2()
    except Exception as exc:
        logger.warning("picamera2 failed (%s) — falling back to OpenCV", exc)

    return _capture_opencv(device)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Test grandlineai/instant-id-photorealistic + camera")
    parser.add_argument("--band", default="bioluminescence",
                        choices=["renaissance", "botanique", "bioluminescence",
                                 "surveillance", "geometric", "glitch"])
    parser.add_argument("--gender", default=None, choices=["man", "woman"],
                        help="Override gender (default: auto-detect from camera)")
    parser.add_argument("--device", default=None, help="Camera device (default: from config)")
    parser.add_argument("--no-face", action="store_true")
    args = parser.parse_args()

    device = args.device or CAMERA_DEVICE

    logger.info("=== InstantID Photorealistic — camera test ===")
    logger.info("Model : grandlineai/instant-id-photorealistic")
    logger.info("Band  : %s", args.band)
    logger.info("Key   : %s…", REPLICATE_API_KEY[:12] if REPLICATE_API_KEY else "(none)")

    # ── 1. Capture ────────────────────────────────────────────────────────────
    if args.no_face:
        logger.error("No face image — cannot test InstantID (requires a face input)")
        sys.exit(1)

    try:
        jpeg_bytes = capture_snapshot(device)
        snapshot_b64 = base64.b64encode(jpeg_bytes).decode()
        logger.info("Snapshot encoded — %.1f KB", len(jpeg_bytes) / 1024)

        snap_path = os.path.join(ROOT, "test_snapshot_photo.jpg")
        with open(snap_path, "wb") as f:
            f.write(jpeg_bytes)
        logger.info("Snapshot saved → %s", snap_path)
    except Exception as exc:
        logger.error("Camera capture failed: %s", exc)
        sys.exit(1)

    # ── 2. Gender detection ───────────────────────────────────────────────────
    if args.gender:
        gender = args.gender
        logger.info("Gender: %s (manual override)", gender)
    else:
        logger.info("Detecting gender from snapshot…")
        gender = detect_gender(jpeg_bytes)
        logger.info("Gender: %s (auto-detected)", gender)

    # ── 3. Generate ───────────────────────────────────────────────────────────
    client = ReplicateInstantIDPhotorealisticClient()
    if not client.available():
        logger.error("REPLICATE_API_KEY not set — cannot run test")
        sys.exit(1)

    logger.info("Submitting to Replicate…")
    t0 = time.time()
    result = client.generate(band=args.band, source_image_b64=snapshot_b64, gender=gender)
    elapsed = time.time() - t0

    # ── 4. Report ─────────────────────────────────────────────────────────────
    if result is None:
        logger.error("Generation failed — check logs above")
        sys.exit(1)

    logger.info("=== Done in %.1fs ===", elapsed)
    urls = result.get("image_urls", [result.get("image_url")])
    for i, url in enumerate(urls, 1):
        logger.info("Image %d: %s", i, url)

    print("\n✓ Results:")
    for url in urls:
        print(" ", url)

    # Auto-open first result in browser
    try:
        import webbrowser
        webbrowser.open(urls[0])
        logger.info("Opened first result in browser")
    except Exception:
        pass


if __name__ == "__main__":
    main()
