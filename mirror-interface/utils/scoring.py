"""
Mirror Mirror — Band Scoring
Maps visitor responses to one of two portrait bands (Renaissance / Botanique)
using keyword counting, with a FER tiebreaker.

Band decision:
  r_score = keyword hits from RENAISSANCE_WORDS
  b_score = keyword hits from BOTANIC_WORDS

  r_score >= b_score + 2  →  "renaissance"
  b_score >= r_score + 2  →  "botanique"
  otherwise               →  "renaissance-botanique" (mix)
"""

import logging
from typing import List

import numpy as np

from config import (
    BANDS,
    BOTANIC_WORDS,
    EMOTION_BAND_WEIGHTS,
    RENAISSANCE_WORDS,
)

logger = logging.getLogger(__name__)

RENAISSANCE = "renaissance"
BOTANIQUE    = "botanique"
MIX          = "renaissance-botanique"


# ---------------------------------------------------------------------------
# Keyword counting
# ---------------------------------------------------------------------------

def count_keywords(responses: List[str], keywords: List[str]) -> int:
    """Count total keyword hits across all responses (case-insensitive)."""
    combined = " ".join(responses).lower()
    return sum(1 for kw in keywords if kw in combined)


# ---------------------------------------------------------------------------
# FER tiebreaker
# ---------------------------------------------------------------------------

def _fer_band(fer_history: List[dict]) -> str:
    """Return the band most supported by FER history, or MIX if flat."""
    n_bands = len(BANDS)
    if not fer_history:
        return MIX

    accumulated = [0.0] * n_bands
    for frame_emotions in fer_history:
        for emotion, score in frame_emotions.items():
            weights = EMOTION_BAND_WEIGHTS.get(emotion)
            if weights is None:
                continue
            for i, w in enumerate(weights):
                accumulated[i] += score * w

    total = sum(accumulated) or 1.0
    normalised = [v / total for v in accumulated]
    idx = int(np.argmax(normalised))
    return BANDS[idx]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calculate_band(
    fer_history: List[dict],
    answers: List[str],
) -> dict:
    """
    Determine the final portrait band for a completed session.

    Parameters
    ----------
    fer_history : list of emotion dicts captured during the conversation.
    answers     : list of free-text visitor answers (one per probe).

    Returns
    -------
    dict with keys:
        band       — str  ("renaissance", "botanique", or "renaissance-botanique")
        band_index — int  (0, 1, or 2)
        scores     — dict  {band_name: keyword_count}
    """
    r_score = count_keywords(answers, RENAISSANCE_WORDS)
    b_score = count_keywords(answers, BOTANIC_WORDS)

    if r_score >= b_score + 2:
        band = RENAISSANCE
    elif b_score >= r_score + 2:
        band = BOTANIQUE
    else:
        # Keyword scores are too close — use FER as tiebreaker
        fer_winner = _fer_band(fer_history)
        band = fer_winner if fer_winner != MIX else MIX

    band_index = BANDS.index(band)

    logger.info(
        "[Scoring] band=%s (index=%d) | renaissance_hits=%d | botanic_hits=%d",
        band, band_index, r_score, b_score,
    )

    return {
        "band": band,
        "band_index": band_index,
        "scores": {
            RENAISSANCE: r_score,
            BOTANIQUE:   b_score,
            MIX:         0,
        },
    }
