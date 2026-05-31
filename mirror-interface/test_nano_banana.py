"""
test_nano_banana.py — Camera test for google/nano-banana-2

Captures a frame from the running mirror app (or directly from the camera),
then runs it through the Nano Banana 2 model on Replicate.

Usage:
    cd mirror-interface
    python test_nano_banana.py [--band BAND] [--gender GENDER] [--device DEV]

Options:
    --band      renaissance | botanique | bioluminescence |
                surveillance | geometric | glitch  (default: geometric)
    --gender    man | woman  (default: auto-detect)
    --device    Camera device, e.g. /dev/video8 (default: from config)
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
logger = logging.getLogger("test_nano_banana")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import REPLICATE_API_KEY, CAMERA_DEVICE  # noqa: E402
from api_clients.replicate_nano_banana import ReplicateNanoBananaClient  # noqa: E402
from api_clients.gender import detect_gender as _detect_gender_b64  # noqa: E402


def detect_gender(jpeg_bytes: bytes) -> str:
    return _detect_gender_b64(base64.b64encode(jpeg_bytes).decode())


# ── camera capture ────────────────────────────────────────────────────────────

def _capture_from_app(host: str = "http://localhost:5050") -> bytes:
    import requests as _req
    logger.info("Fetching snapshot from mirror app at %s/camera/snapshot…", host)
    resp = _req.get(f"{host}/camera/snapshot", timeout=10)
    resp.raise_for_status()
    logger.info("Snapshot fetched — %.1f KB", len(resp.content) / 1024)
    return resp.content


def _capture_opencv(device) -> bytes:
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
    from picamera2 import Picamera2
    from PIL import Image

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
    parser = argparse.ArgumentParser(description="Test google/nano-banana-2 + camera")
    parser.add_argument("--band", default="geometric",
                        choices=["renaissance", "botanique", "bioluminescence",
                                 "surveillance", "geometric", "glitch"])
    parser.add_argument("--gender", default=None, choices=["man", "woman"],
                        help="Override gender (default: auto-detect from camera)")
    parser.add_argument("--device", default=None, help="Camera device (default: from config)")
    args = parser.parse_args()

    device = args.device or CAMERA_DEVICE

    logger.info("=== Nano Banana 2 — camera test ===")
    logger.info("Model : google/nano-banana-2")
    logger.info("Band  : %s", args.band)
    logger.info("Key   : %s…", REPLICATE_API_KEY[:12] if REPLICATE_API_KEY else "(none)")

    # ── 1. Capture ────────────────────────────────────────────────────────────
    print("\nGet in position — capturing in:")
    for i in range(5, 0, -1):
        print(f"  {i}…", flush=True)
        time.sleep(1)
    print("  Capturing now!\n")

    try:
        jpeg_bytes = capture_snapshot(device)
        snapshot_b64 = base64.b64encode(jpeg_bytes).decode()
        logger.info("Snapshot encoded — %.1f KB", len(jpeg_bytes) / 1024)

        snap_path = os.path.join(ROOT, "test_snapshot_nano_banana.jpg")
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
    client = ReplicateNanoBananaClient()
    if not client.available():
        logger.error("REPLICATE_API_KEY not set — cannot run test")
        sys.exit(1)

    logger.info("Submitting to Replicate…")
    t0 = time.time()
    result = client.generate(band=args.band, source_image_b64=snapshot_b64, gender=gender, num_outputs=1)
    elapsed = time.time() - t0

    # ── 4. Report ─────────────────────────────────────────────────────────────
    if result is None:
        logger.error("Generation failed — check logs above")
        sys.exit(1)

    logger.info("=== Done in %.1fs ===", elapsed)
    urls = result.get("image_urls", [result.get("image_url")])
    for i, url in enumerate(urls, 1):
        logger.info("Image %d: %s", i, url)

    print("\nResults:")
    for url in urls:
        print(" ", url)

    # Download and open locally
    try:
        import requests as _req
        import subprocess
        for i, url in enumerate(urls, 1):
            out_path = os.path.join(ROOT, f"test_result_nano_banana_{i}.jpg")
            r = _req.get(url, timeout=30)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(r.content)
            logger.info("Saved → %s", out_path)
            if i == 1:
                subprocess.Popen(["xdg-open", out_path])
    except Exception as exc:
        logger.warning("Could not save/open image locally: %s", exc)


if __name__ == "__main__":
    main()
