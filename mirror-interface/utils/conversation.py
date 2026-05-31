"""
Mirror Mirror — Conversation State Machine

States:
    idle        — waiting for visitor
    intro       — session started, intro question displayed
    questioning — iterating through questions 1..3
    listening   — waiting for visitor's answer
    complete    — all answers collected, scoring ready
    generating  — portrait generation in progress
    ready       — portrait available, ready to print

Transitions:
    idle      → intro       : start_conversation()
    intro     → questioning : next_question()   (after first "question" event)
    questioning → listening : question emitted, awaiting answer
    listening → questioning : receive_answer()  (more questions remain)
    listening → complete    : receive_answer()  (last answer received)
    complete  → generating  : begin_generation()
    generating → ready      : generation_complete()
    any → idle              : reset()
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from config import (
    CLOSING_INDEX,
    CONVERSATION_TIMEOUT,
    FER_CAPTURE_WINDOW,
    QUESTIONS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation session dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConversationSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: str = "idle"
    question_index: int = 0           # current question (0-based)
    answers: List[str] = field(default_factory=list)

    # FER capture
    fer_history: List[dict] = field(default_factory=list)
    fer_window_start: Optional[float] = None  # epoch seconds

    # Timing
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Results
    band: Optional[str] = None
    band_index: Optional[int] = None
    scores: Optional[dict] = None
    portrait_url: Optional[str] = None

    # Visitor snapshot captured at interview start (base64 JPEG)
    snapshot_b64: Optional[str] = None

    # Gender detected from snapshot ("man" or "woman")
    gender: Optional[str] = None

    # ------------------------------------------------------------------

    def is_expired(self) -> bool:
        if self.started_at is None:
            return False
        return (time.time() - self.started_at) > CONVERSATION_TIMEOUT

    def time_remaining(self) -> float:
        if self.started_at is None:
            return float(CONVERSATION_TIMEOUT)
        elapsed = time.time() - self.started_at
        return max(0.0, CONVERSATION_TIMEOUT - elapsed)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class ConversationStateMachine:
    """
    Manages a single visitor conversation session.

    Instantiate one per connected client / per session.
    """

    # Questions that expect a typed answer (all except opening and closing)
    _ANSWER_QUESTION_INDICES = [1, 2, 3]

    def __init__(self, session_id: Optional[str] = None):
        self.session = ConversationSession(
            session_id=session_id or str(uuid.uuid4())
        )
        logger.info(
            "[Conv] Session created: %s (state=idle)", self.session.session_id
        )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """
        Transition idle → intro.
        Returns the payload for the first ``question`` event (index 0).
        """
        if self.session.state != "idle":
            logger.warning(
                "[Conv] start() called in state=%s — ignoring", self.session.state
            )
            return self._current_question_payload()

        self.session.state = "intro"
        self.session.started_at = time.time()
        self.session.question_index = 0
        self._open_fer_window()

        logger.info("[Conv] %s: idle → intro", self.session.session_id)
        return self._current_question_payload()

    def next_question(self) -> Optional[dict]:
        """
        Advance to the next question after the intro (intro → questioning).
        Called after the client confirms it received the intro message.
        Returns question payload or None if no more questions.
        """
        if self.session.state == "intro":
            self.session.state = "questioning"
            self.session.question_index = 1
            self._open_fer_window()
            logger.info(
                "[Conv] %s: intro → questioning (q=%d)",
                self.session.session_id,
                self.session.question_index,
            )
            return self._current_question_payload()

        logger.warning(
            "[Conv] next_question() called in state=%s", self.session.state
        )
        return None

    def receive_answer(self, text: str) -> dict:
        """
        Accept visitor's answer while in `listening` state.

        Returns dict with:
            next_question — Optional[dict]  payload if more questions remain
            complete      — Optional[dict]  result if conversation is done
        """
        if self.session.state not in ("questioning", "listening"):
            logger.warning(
                "[Conv] receive_answer() called in state=%s — ignoring",
                self.session.state,
            )
            return {"next_question": None, "complete": None}

        self.session.state = "listening"
        answer_text = (text or "").strip()
        self.session.answers.append(answer_text)
        logger.info(
            "[Conv] %s: answer[%d]=%r",
            self.session.session_id,
            len(self.session.answers),
            answer_text[:60],
        )

        next_idx = self.session.question_index + 1

        if next_idx >= CLOSING_INDEX:
            # Closing statement then complete
            self.session.question_index = CLOSING_INDEX
            closing_payload = self._current_question_payload()
            self._transition_complete()
            return {"next_question": closing_payload, "complete": self._complete_payload()}

        # More questions
        self.session.question_index = next_idx
        self.session.state = "questioning"
        self._open_fer_window()
        logger.info(
            "[Conv] %s: → questioning (q=%d)",
            self.session.session_id,
            self.session.question_index,
        )
        return {"next_question": self._current_question_payload(), "complete": None}

    def begin_generation(self) -> None:
        """Transition complete → generating."""
        if self.session.state != "complete":
            return
        self.session.state = "generating"
        logger.info("[Conv] %s: complete → generating", self.session.session_id)

    def generation_complete(self, portrait_url: str) -> None:
        """Transition generating → ready."""
        self.session.state = "ready"
        self.session.portrait_url = portrait_url
        logger.info(
            "[Conv] %s: generating → ready (url=%s)",
            self.session.session_id,
            portrait_url,
        )

    def reset(self) -> None:
        """Reset to idle, preserve session_id."""
        sid = self.session.session_id
        self.session = ConversationSession(session_id=sid)
        logger.info("[Conv] %s: → idle (reset)", sid)

    # ------------------------------------------------------------------
    # FER window helpers
    # ------------------------------------------------------------------

    def _open_fer_window(self) -> None:
        self.session.fer_window_start = time.time()

    def should_capture_fer(self) -> bool:
        """
        Returns True while within the FER_CAPTURE_WINDOW seconds after
        the last question was emitted.
        """
        if self.session.fer_window_start is None:
            return False
        return (time.time() - self.session.fer_window_start) <= FER_CAPTURE_WINDOW

    def add_fer_sample(self, emotions: dict) -> None:
        """Record an emotion sample during an active capture window."""
        if self.should_capture_fer():
            self.session.fer_history.append(dict(emotions))

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    def _current_question_payload(self) -> dict:
        idx = self.session.question_index
        text = QUESTIONS[idx] if idx < len(QUESTIONS) else ""
        return {"text": text, "index": idx}

    def _transition_complete(self) -> None:
        self.session.state = "complete"
        self.session.completed_at = time.time()
        logger.info("[Conv] %s: → complete", self.session.session_id)

    def _complete_payload(self) -> dict:
        """Return a minimal completion signal; scoring happens outside this class."""
        return {
            "session_id": self.session.session_id,
            "answers": list(self.session.answers),
            "fer_history": list(self.session.fer_history),
        }

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self.session.state

    @property
    def session_id(self) -> str:
        return self.session.session_id
