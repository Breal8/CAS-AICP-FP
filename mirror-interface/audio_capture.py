"""
Mirror Mirror — Audio Capture + Moonshine STT

Captures audio from the Jabra headset via pw-record (PipeWire),
detects utterance boundaries by silence, then runs Moonshine
on each complete utterance.

Emits socket events:
    stt_partial  — interim (when utterance starts, for visual feedback)
    stt_final    — final transcript after silence, used as the answer
"""

import logging
import math
import struct
import subprocess
import threading
import time
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_SAMPLE_RATE     = 16000
DEFAULT_CHUNK_MS        = 30       # ms per read chunk
DEFAULT_SILENCE_SECONDS = 1.5      # utterance ends after this much silence
DEFAULT_SILENCE_THRESH  = 500      # RMS energy below this = silence
MIN_UTTERANCE_SECONDS   = 1.0      # ignore utterances shorter than this

# Jabra headset PipeWire source name (confirmed via pw-cli ls Node)
JABRA_PW_TARGET = (
    "alsa_input.usb-0b0e_Jabra_Link_370_30507578BB4D-00.mono-fallback"
)


def _rms(data: bytes) -> float:
    """RMS energy of 16-bit signed LE PCM bytes."""
    n = len(data) // 2
    if n == 0:
        return 0.0
    shorts = struct.unpack(f"<{n}h", data[: n * 2])
    return math.sqrt(sum(s * s for s in shorts) / n)


class AudioCapture:
    """
    Continuous audio capture with Moonshine STT and silence-based
    utterance detection.

    Emits SocketIO:
        'stt_partial'  — speech started (visual cue only, text may be empty)
        'stt_final'    — finished utterance with transcript text
    """

    def __init__(
        self,
        socketio: Any,
        text_callback: Any = None,   # unused, kept for API compat
        device: str = "default",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        chunk_duration_ms: int = DEFAULT_CHUNK_MS,
        silence_seconds: float = DEFAULT_SILENCE_SECONDS,
        vosk_model_path: str = "",   # unused, kept for API compat
    ):
        self.socketio = socketio
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_duration_ms
        self.silence_seconds = silence_seconds

        self._chunk_samples = int(sample_rate * chunk_duration_ms / 1000)
        self._chunk_bytes = self._chunk_samples * 2  # 16-bit

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # pw-record subprocess
        self._pw_proc: Optional[subprocess.Popen] = None

        # Utterance accumulation
        self._in_utterance = False
        self._utterance_buf: list[bytes] = []
        self._last_speech_time = 0.0

        # Moonshine transcriber (loaded once)
        self._transcriber = None
        self._transcriber_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True

        if not self._load_moonshine():
            return False

        if not self._init_audio():
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info(
            "[Audio] Started (moonshine, rate=%d, silence=%.1fs)",
            self.sample_rate, self.silence_seconds,
        )
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._release()
        self._running = False
        logger.info("[Audio] Stopped")

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _load_moonshine(self) -> bool:
        try:
            import moonshine_voice as mv
            from moonshine_voice.transcriber import Transcriber

            logger.info("[Audio] Loading Moonshine model...")
            model_path, model_arch = mv.get_model_for_language("en")
            self._transcriber = Transcriber(model_path, model_arch)
            logger.info("[Audio] Moonshine ready (model=%s)", model_path)
            return True
        except ImportError:
            logger.error("[Audio] moonshine-voice not installed")
            return False
        except Exception as exc:
            logger.error("[Audio] Moonshine load error: %s", exc)
            return False

    def _init_audio(self) -> bool:
        try:
            cmd = [
                "pw-record",
                f"--target={JABRA_PW_TARGET}",
                f"--rate={self.sample_rate}",
                "--channels=1",
                "--format=s16",
                "-",
            ]
            self._pw_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            logger.info("[Audio] pw-record started (pid=%d)", self._pw_proc.pid)
            return True
        except Exception as exc:
            logger.error("[Audio] pw-record failed: %s", exc)
            self._pw_proc = None
            return False

    def _release(self) -> None:
        if self._pw_proc is not None:
            try:
                self._pw_proc.terminate()
                self._pw_proc.wait(timeout=3)
            except Exception:
                pass
            self._pw_proc = None
        if self._transcriber is not None:
            try:
                self._transcriber.close()
            except Exception:
                pass
            self._transcriber = None

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        if self._pw_proc is None:
            return

        while not self._stop_event.is_set():
            try:
                data = self._pw_proc.stdout.read(self._chunk_bytes)
                if not data:
                    logger.warning("[Audio] pw-record closed")
                    break
            except Exception as exc:
                logger.warning("[Audio] read error: %s", exc)
                time.sleep(0.05)
                continue

            self._process_chunk(data)

    def _process_chunk(self, data: bytes) -> None:
        energy = _rms(data)
        now = time.time()

        if energy > DEFAULT_SILENCE_THRESH:
            self._last_speech_time = now
            if not self._in_utterance:
                self._in_utterance = True
                self._utterance_buf = []
                logger.debug("[Audio] Utterance start (rms=%.0f)", energy)
                if self.socketio:
                    self.socketio.emit("stt_partial", {"text": "…"})
            self._utterance_buf.append(data)
        else:
            if self._in_utterance:
                self._utterance_buf.append(data)  # keep trailing silence for context
                if (now - self._last_speech_time) >= self.silence_seconds:
                    self._in_utterance = False
                    self._finalize_utterance()

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def _finalize_utterance(self) -> None:
        raw = b"".join(self._utterance_buf)
        self._utterance_buf = []

        duration = len(raw) / (self.sample_rate * 2)
        if duration < MIN_UTTERANCE_SECONDS:
            logger.debug("[Audio] Utterance too short (%.2fs), skipping", duration)
            return

        logger.info("[Audio] Transcribing %.1fs utterance...", duration)

        # Convert raw int16 LE → float32 [-1, 1]
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        def _transcribe():
            try:
                with self._transcriber_lock:
                    transcript = self._transcriber.transcribe_without_streaming(
                        pcm.tolist(),
                        sample_rate=self.sample_rate,
                    )
                text = " ".join(line.text for line in transcript.lines).strip()

                if text:
                    logger.info("[Audio] Moonshine: %r", text[:80])
                    if self.socketio:
                        self.socketio.emit("stt_final", {"text": text})
                        self.socketio.emit("stt_partial", {"text": text, "final": True})
                else:
                    logger.debug("[Audio] Moonshine returned empty")
            except Exception as exc:
                logger.error("[Audio] Transcribe error: %s", exc)

        threading.Thread(target=_transcribe, daemon=True).start()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_silence_threshold(self, rms: int) -> None:
        global DEFAULT_SILENCE_THRESH
        DEFAULT_SILENCE_THRESH = rms

    @property
    def vosk_available(self) -> bool:
        return self._transcriber is not None
