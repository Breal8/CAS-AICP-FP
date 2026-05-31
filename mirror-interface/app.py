"""
Mirror Mirror — Flask + SocketIO Main Server (Pi Installation)

Integrated with:
  - Pi Camera / OpenCV fallback  (fer + pi_camera modules)
  - Audio headset + Vosk STT     (audio_capture module)
  - Runway GMW Avatar            (avatar_client module)
  - Bluetooth Instax printer     (instax module)

Run with:
    python app.py

Or with eventlet directly:
    eventlet wsgi eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 5050)), app)
"""

import logging
import os
import threading
import time
import uuid

import eventlet
import eventlet.tpool
eventlet.monkey_patch()  # must be before other imports

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

import config
from fer_engine.detector import EmotionDetector
from utils.conversation import ConversationStateMachine
from utils.scoring import calculate_band
from api_clients.replicate_nano_banana import ReplicateNanoBananaClient as PortraitClient
from api_clients.instax import InstaxClient

# ---------------------------------------------------------------------------
# Optional Pi-native modules (graceful fallback if not available)
# ---------------------------------------------------------------------------
try:
    from pi_camera import PiCamera
    _PI_CAMERA_AVAILABLE = True
except Exception as _exc:
    _PI_CAMERA_AVAILABLE = False
    logging.getLogger(__name__).warning("[App] pi_camera not available: %s", _exc)

_AUDIO_AVAILABLE = False  # Pi STT removed — Runway avatar handles speech

# GMW avatar_client disabled — browser handles avatar via LiveKit (/avatar/connect)
_AVATAR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mirror")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PORTRAIT_CACHE_DIR = "/tmp/mirror_portraits"
os.makedirs(PORTRAIT_CACHE_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["SECRET_KEY"] = config.SECRET_KEY

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins=config.CORS_ORIGINS,
    logger=False,
    engineio_logger=False,
)

# ---------------------------------------------------------------------------
# Shared singletons
# ---------------------------------------------------------------------------
emotion_detector = EmotionDetector()
portrait_client = PortraitClient()  # google/nano-banana-2 via Replicate
instax_client = InstaxClient()

# Pi-native hardware (lazy init)
pi_camera: "PiCamera | None" = None
audio_capture: "AudioCapture | None" = None
avatar_client: "AvatarClient | None" = None

# Session storage  { session_id: ConversationStateMachine }
sessions: dict[str, ConversationStateMachine] = {}

# Map socket connection id → session id
sid_to_session: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Hardware initialisation
# ---------------------------------------------------------------------------

def _init_hardware() -> None:
    """Initialise Pi camera, audio capture, and avatar client."""
    global pi_camera, audio_capture, avatar_client

    # Pi Camera
    if _PI_CAMERA_AVAILABLE and config.PICAM_RESOLUTION:
        try:
            pi_camera = PiCamera(
                emotion_detector=emotion_detector,
                socketio=socketio,
                resolution=config.PICAM_RESOLUTION,
                framerate=config.PICAM_FRAMERATE,
                fer_interval_ms=500,
            )
            if pi_camera.start():
                logger.info("[App] Pi camera started (%s)", pi_camera.backend)
            else:
                logger.warning("[App] Pi camera failed to start")
        except Exception as exc:
            logger.error("[App] Pi camera init error: %s", exc)
            pi_camera = None

    # Audio STT removed — Runway avatar handles listening and speech natively

    # Avatar client
    if _AVATAR_AVAILABLE:
        try:
            avatar_client = AvatarClient()
            logger.info("[App] Avatar client initialised")
        except Exception as exc:
            logger.error("[App] Avatar client init error: %s", exc)
            avatar_client = None


# ---------------------------------------------------------------------------
# Helper: get or create state machine for a socket connection
# ---------------------------------------------------------------------------

def _get_machine(sid: str) -> ConversationStateMachine:
    """Return the ConversationStateMachine bound to this socket connection."""
    session_id = sid_to_session.get(sid)
    if session_id and session_id in sessions:
        return sessions[session_id]

    # Create a new session
    machine = ConversationStateMachine()
    sessions[machine.session_id] = machine
    sid_to_session[sid] = machine.session_id
    logger.info("[App] New session %s for socket %s", machine.session_id, sid)
    return machine


# ---------------------------------------------------------------------------
# Snapshot capture helper
# ---------------------------------------------------------------------------

def _detect_gender(snapshot_b64: str) -> str:
    """Detect gender from a base64 JPEG using DeepFace. Returns 'man' or 'woman'."""
    try:
        import base64 as _b64
        import io
        import numpy as np
        from PIL import Image
        from deepface import DeepFace  # type: ignore[import-untyped]

        image_bytes = _b64.b64decode(snapshot_b64)
        arr = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        result = DeepFace.analyze(arr, actions=["gender"], enforce_detection=False, silent=True)
        if isinstance(result, list):
            result = result[0]
        dominant = result.get("dominant_gender", "Woman")
        return "woman" if dominant.lower() == "woman" else "man"
    except Exception as exc:
        logger.warning("[App] Gender detection failed: %s", exc)
        return "woman"


def _crop_face_snapshot(snapshot_b64: str) -> str:
    """Crop the snapshot to a close-up of the detected face (with padding). Returns b64 JPEG."""
    try:
        import base64 as _b64
        import io
        import numpy as np
        from PIL import Image
        from deepface import DeepFace  # type: ignore[import-untyped]

        image_bytes = _b64.b64decode(snapshot_b64)
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        h, w = arr.shape[:2]

        result = DeepFace.analyze(arr, actions=["gender"], enforce_detection=True, silent=True)
        if isinstance(result, list):
            result = result[0]
        region = result.get("region", {})
        x, y, fw, fh = region.get("x", 0), region.get("y", 0), region.get("w", w), region.get("h", h)

        # Add 60% padding around the face for a natural portrait close-up
        pad_x = int(fw * 0.6)
        pad_y = int(fh * 0.6)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(w, x + fw + pad_x)
        y2 = min(h, y + fh + pad_y)

        cropped = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=90)
        result_b64 = _b64.b64encode(buf.getvalue()).decode("utf-8")
        logger.info("[App] Face crop: (%d,%d,%d,%d) → %dx%d", x1, y1, x2, y2, cropped.width, cropped.height)
        return result_b64
    except Exception as exc:
        logger.warning("[App] Face crop failed, using full frame: %s", exc)
        return snapshot_b64


def _capture_session_snapshot(machine: "ConversationStateMachine") -> None:
    """Read the latest Pi camera frame and store as base64 on the session."""
    import base64 as _b64
    import os as _os

    frame_path = "/tmp/mirror_latest_frame.jpg"
    jpeg_bytes = None

    if _os.path.exists(frame_path):
        try:
            with open(frame_path, "rb") as f:
                jpeg_bytes = f.read()
        except Exception:
            pass

    if jpeg_bytes is None and pi_camera and pi_camera._latest_jpeg:
        jpeg_bytes = pi_camera._latest_jpeg

    if jpeg_bytes:
        machine.session.snapshot_b64 = _b64.b64encode(jpeg_bytes).decode("utf-8")
        logger.info(
            "[App] Visitor snapshot captured for session=%s (%d bytes)",
            machine.session_id, len(jpeg_bytes),
        )
        machine.session.gender = _detect_gender(machine.session.snapshot_b64)
        logger.info("[App] Gender detected: %s (session=%s)", machine.session.gender, machine.session_id)
        machine.session.snapshot_b64 = _crop_face_snapshot(machine.session.snapshot_b64)
        logger.info("[App] Face snapshot cropped for session=%s", machine.session_id)
    else:
        logger.warning("[App] No camera frame available for snapshot")


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the frontend index.html."""
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/camera/snapshot")
def camera_snapshot():
    """Single JPEG frame from Pi camera — browser polls this for self-view."""
    from flask import Response, abort
    import os as _os
    # Prefer live OpenCV feed when available
    if pi_camera and pi_camera._latest_jpeg:
        return Response(pi_camera._latest_jpeg, mimetype='image/jpeg',
                        headers={'Cache-Control': 'no-store, no-cache'})
    # Fallback: picamera2 worker frame file
    frame_path = "/tmp/mirror_latest_frame.jpg"
    if _os.path.exists(frame_path):
        try:
            with open(frame_path, "rb") as f:
                data = f.read()
            return Response(data, mimetype='image/jpeg',
                            headers={'Cache-Control': 'no-store, no-cache'})
        except Exception:
            pass
    abort(503)


def _avatar_connect_blocking():
    """
    Spawns runway_connect.py as a subprocess so Runway's httpx-based SDK runs in
    a fresh Python interpreter — completely free of eventlet's monkey-patched sockets.
    Returns a dict on success or raises RuntimeError on failure.
    """
    import json
    import subprocess
    import sys

    script = os.path.join(os.path.dirname(__file__), "runway_connect.py")
    try:
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=config.RUNWAY_POLL_TIMEOUT + 30,
        )
        output = proc.stdout.strip()
        if not output:
            stderr = proc.stderr.strip()[-400:] if proc.stderr else "(no stderr)"
            raise RuntimeError(f"runway_connect produced no output. stderr: {stderr}")

        data = json.loads(output)
        if "error" in data:
            msg = data["error"]
            if "429" in msg or "rate" in msg.lower():
                raise RuntimeError("RATE_LIMIT")
            raise RuntimeError(msg)

        logger.info("[Avatar] Session ready: room=%s", data.get("roomName"))
        return data

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"runway_connect timed out after {config.RUNWAY_POLL_TIMEOUT + 30}s")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runway_connect bad JSON: {exc}")


@app.route("/avatar/connect", methods=["POST"])
def avatar_connect():
    """
    Create a Runway Realtime Session, poll until READY, consume server-side.
    Runs in a native thread via tpool to avoid eventlet DNS patching issues.
    """
    from flask import jsonify
    try:
        result = eventlet.tpool.execute(_avatar_connect_blocking)
        return jsonify(result)
    except RuntimeError as exc:
        msg = str(exc)
        logger.error("[Avatar] All retries exhausted: %s", msg)
        if "RATE_LIMIT" in msg:
            return jsonify({"error": "Runway daily limit reached. Try again tomorrow."}), 429
        return jsonify({"error": msg}), 500


@app.route("/health")
def health():
    return {
        "status": "ok",
        "sessions": len(sessions),
        "camera": pi_camera.backend if pi_camera else "off",
        "audio": audio_capture.is_running() if audio_capture else False,
        "avatar": avatar_client is not None,
        "film_remaining": instax_client.film_remaining,
    }


@app.route("/instax/reload")
def instax_reload():
    """Reset film counter to a full cartridge. Call after swapping in a fresh pack."""
    remaining = instax_client.reload_film()
    return {"status": "ok", "film_remaining": remaining}


@app.route("/exit", methods=["POST"])
def exit_kiosk():
    """Kill the Chromium kiosk window (triggered by keyboard shortcut in the browser)."""
    import subprocess
    subprocess.Popen(["pkill", "-f", "chromium.*5050"])
    return {"status": "ok"}


@app.route("/portraits/<path:filename>")
def serve_portrait(filename):
    """Serve locally cached portrait images."""
    return send_from_directory(PORTRAIT_CACHE_DIR, filename)


@app.route("/avatar/current")
def avatar_current():
    """Return the current avatar video URL for the active session."""
    if avatar_client is None:
        return {"url": None, "error": "avatar unavailable"}
    return {"url": avatar_client.get_current_media()}


# ---------------------------------------------------------------------------
# WebSocket: connect / disconnect
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)
    logger.info(
        "[WS] Client connected: socket=%s session=%s", sid, machine.session_id
    )
    emit("connected", {"session_id": machine.session_id, "state": machine.state})


@socketio.on("disconnect")
def on_disconnect():
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    session_id = sid_to_session.pop(sid, None)
    if session_id:
        sessions.pop(session_id, None)
    instax_client.disconnect_printer()
    logger.info(
        "[WS] Client disconnected: socket=%s session=%s", sid, session_id
    )


# ---------------------------------------------------------------------------
# WebSocket: frame (base64 fallback from browser camera)
# ---------------------------------------------------------------------------

@socketio.on("frame")
def on_frame(data: dict):
    """
    Client sends { "image": "<base64_jpeg_string>" }.
    Only used as fallback when Pi camera is unavailable (browser getUserMedia).
    """
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)

    # Skip if Pi camera is handling FER natively
    if pi_camera is not None and pi_camera.is_running():
        return

    base64_image = data.get("image", "")
    if not base64_image:
        logger.warning("[WS][frame] Empty image from socket=%s", sid)
        return

    result = emotion_detector.analyze_frame(
        base64_image, session_id=machine.session_id
    )

    # Feed into conversation FER window if active
    if machine.state in ("questioning", "listening", "intro"):
        machine.add_fer_sample(result["emotions"])

    payload = {
        "emotions": result["emotions"],
        "dominant": result["dominant"],
        "session_id": machine.session_id,
    }
    emit("emotion_update", payload)


# ---------------------------------------------------------------------------
# WebSocket: start_conversation
# ---------------------------------------------------------------------------

@socketio.on("start_conversation")
def on_start_conversation(_data=None):
    """
    Client signals the visitor has stepped up.
    Runway avatar handles the interview — we just start FER capture here.
    """
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)

    logger.info(
        "[WS][start_conversation] socket=%s session=%s — starting FER",
        sid, machine.session_id,
    )

    if machine.state != "idle":
        machine.reset()

    machine.session.state = "intro"
    machine.session.started_at = time.time()
    machine._open_fer_window()

    if pi_camera is not None:
        pi_camera.set_session_id(machine.session_id)

    # Capture visitor snapshot for use as portrait reference
    _capture_session_snapshot(machine)

    emit("session_started", {"session_id": machine.session_id})


# ---------------------------------------------------------------------------
# WebSocket: capture_face  (fired by browser when OMNI calls capture_face tool)
# ---------------------------------------------------------------------------

@socketio.on("capture_face")
def on_capture_face(_data=None):
    """
    OMNI called the capture_face tool during the calibration stillness beat.
    Grab the freshest Pi camera frame and store it as the session snapshot.
    """
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)
    logger.info("[WS][capture_face] socket=%s session=%s — capturing snapshot", sid, machine.session_id)
    _capture_session_snapshot(machine)
    emit("capture_ack", {"ok": True})


# ---------------------------------------------------------------------------
# WebSocket: interview_complete  (fired by browser after Runway tool call)
# ---------------------------------------------------------------------------

@socketio.on("interview_complete")
def on_interview_complete(data: dict):
    """
    Browser sends { "answers": ["...", "...", "..."] } after the Runway avatar
    calls the interview_complete client-event tool via the LiveKit data channel.
    """
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)

    visitor_name = data.get("name", "")
    answers = list(data.get("answers") or [])
    logger.info(
        "[WS][interview_complete] socket=%s session=%s name=%r answers=%r",
        sid, machine.session_id, visitor_name, [a[:60] for a in answers],
    )

    machine.session.visitor_name = visitor_name
    machine.session.answers = answers
    machine.session.state = "complete"
    machine.session.completed_at = time.time()

    # Capture snapshot now if start_conversation never fired it
    if not machine.session.snapshot_b64:
        logger.info("[App] No snapshot yet — capturing at interview_complete")
        _capture_session_snapshot(machine)

    _handle_conversation_complete(sid, machine, {
        "answers": answers,
        "fer_history": machine.session.fer_history,
    })


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Portrait local cache
# ---------------------------------------------------------------------------

def _cache_portraits_locally(session_id: str, image_urls: list) -> list:
    """
    Download each portrait URL and save to PORTRAIT_CACHE_DIR.
    Returns list of local /portraits/<filename> URLs (served by Flask).
    Falls back to original URL for any that fail.
    """
    import requests as _req
    local_urls = []
    _HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MirrorMirror/1.0)"}
    for i, url in enumerate(image_urls):
        if not url or not url.startswith("http"):
            local_urls.append(url)
            continue
        try:
            resp = eventlet.tpool.execute(
                lambda u=url: _req.get(u, timeout=20, headers=_HEADERS)
            )
            if resp.status_code == 200 and len(resp.content) > 0:
                filename = f"{session_id}_{i}.jpg"
                filepath = os.path.join(PORTRAIT_CACHE_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                local_urls.append(f"/portraits/{filename}")
                logger.info("[Gen] Portrait cached locally: %s", filename)
            else:
                logger.warning("[Gen] Portrait download empty (status=%d len=%d): %s",
                               resp.status_code, len(resp.content), url[:80])
                local_urls.append(url)
        except Exception as exc:
            logger.warning("[Gen] Portrait cache failed for %s: %s", url[:80], exc)
            local_urls.append(url)
    return local_urls


# Completion + generation flow
# ---------------------------------------------------------------------------

def _handle_conversation_complete(
    sid: str,
    machine: ConversationStateMachine,
    complete_data: dict,
) -> None:
    """
    Called when all answers have been collected.
    1. Compute band via scoring module.
    2. Emit conversation_complete.
    3. Emit generate_portrait.
    4. Run portrait generation in a background greenlet.
    5. Emit portrait_ready.
    """
    fer_history = complete_data.get("fer_history", [])
    answers = complete_data.get("answers", [])

    # Small delay so the closing statement is readable
    socketio.sleep(1.5)

    band_result = calculate_band(fer_history, answers)
    machine.session.band = band_result["band"]
    machine.session.band_index = band_result["band_index"]
    machine.session.scores = band_result["scores"]

    completion_payload = {
        "band": band_result["band"],
        "band_index": band_result["band_index"],
        "scores": band_result["scores"],
    }
    logger.info(
        "[WS] Emitting conversation_complete: band=%s index=%d",
        band_result["band"],
        band_result["band_index"],
    )
    socketio.emit("conversation_complete", completion_payload, to=sid)

    # Signal that generation is starting
    socketio.sleep(0.5)
    socketio.emit("generate_portrait", {"band": band_result["band"]}, to=sid)
    machine.begin_generation()

    # Run generation in a background greenlet (non-blocking)
    def _generate():
        logger.info(
            "[Gen] Starting portrait generation for session=%s band=%s",
            machine.session_id,
            band_result["band"],
        )
        try:
            gen_result = portrait_client.generate(
                band=band_result["band"],
                source_image_b64=machine.session.snapshot_b64,
                gender=machine.session.gender or "woman",
            )
            image_url = gen_result.get("image_url", config.FALLBACK_PORTRAIT)
            image_urls = gen_result.get("image_urls") or [image_url]
        except Exception as exc:  # noqa: BLE001
            logger.error("[Gen] Portrait generation failed: %s", exc, exc_info=True)
            image_url = config.FALLBACK_PORTRAIT
            image_urls = [image_url]

        # Cache portraits locally so printing never depends on catbox.moe
        local_urls = _cache_portraits_locally(machine.session_id, image_urls)

        machine.generation_complete(image_url)

        portrait_payload = {
            "image_url": local_urls[0] if local_urls else image_url,
            "image_urls": local_urls if local_urls else image_urls,
            "band": band_result["band"],
        }
        logger.info(
            "[WS] Emitting portrait_ready: url=%s band=%s",
            portrait_payload["image_url"],
            band_result["band"],
        )
        socketio.emit("portrait_ready", portrait_payload, to=sid)

    socketio.start_background_task(_generate)

    # Unbind camera session (FER no longer needed)
    if pi_camera is not None:
        pi_camera.set_session_id(None)


# ---------------------------------------------------------------------------
# WebSocket: print_poloroid
# ---------------------------------------------------------------------------

@socketio.on("print_poloroid")
def on_print_poloroid(data: dict = None):
    """
    Client requests a print.
    Server calls Instax client and emits print_status.
    """
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    machine = _get_machine(sid)

    image_url = (data or {}).get("image_url") or machine.session.portrait_url or config.FALLBACK_PORTRAIT
    band = machine.session.band

    logger.info(
        "[WS][print_poloroid] socket=%s session=%s image=%s band=%s",
        sid, machine.session_id, image_url, band,
    )

    def _print():
        socketio.emit("print_status", {"status": "connecting"}, to=sid)
        result = instax_client.print_photo(
            image_url=image_url,
            band=band,
            progress_cb=lambda pct: socketio.emit(
                "print_status", {"status": "progress", "progress": pct}, to=sid
            ),
        )
        logger.info("[WS] Emitting print_status: %s", result)
        socketio.emit("print_status", result, to=sid)

    socketio.start_background_task(_print)


# ---------------------------------------------------------------------------
# WebSocket: session_reset  (browser signals reveal is closing)
# ---------------------------------------------------------------------------

@socketio.on("session_reset")
def on_session_reset(_data=None):
    """Browser calls this when the reveal closes so we can drop the BLE connection."""
    from flask import request  # type: ignore[import-untyped]
    sid = request.sid  # type: ignore[attr-defined]
    logger.info("[WS][session_reset] socket=%s — disconnecting printer", sid)
    instax_client.disconnect_printer()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "Starting Mirror Mirror server on %s:%d (debug=%s)",
        config.HOST,
        config.PORT,
        config.DEBUG,
    )
    # Initialise hardware after eventlet monkey-patching
    _init_hardware()
    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
        use_reloader=False,  # reloader conflicts with eventlet monkey-patching
    )
