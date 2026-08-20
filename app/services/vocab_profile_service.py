"""
Vocab profile service.

Builds a per-user, per-language profile of which lemmas they:
  - Already know well (known)
  - Are in active learning (learning)
  - Have due for review soon (due_soon)

Used by the chat orchestrator to constrain AI replies and pick which
"stretch" words to introduce. Backed by user_flashcard_progress (FSRS state).

Lemmas are lazily backfilled on first read for users whose cards don't
yet have a lemma column populated.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Set

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.user_flashcard_progress import UserFlashcardProgress
from app.services.lemmatizer import lemmatize_word

# FSRS state values
_STATE_NEW = 0
_STATE_LEARNING = 1
_STATE_REVIEW = 2
_STATE_RELEARNING = 3

# Threshold: stability ≥ N days means we consider the lemma "known"
_KNOWN_STABILITY_DAYS = 21.0


@dataclass
class VocabProfile:
    user_id: int
    language: str
    known_lemmas: Set[str] = field(default_factory=set)
    learning_lemmas: Set[str] = field(default_factory=set)
    due_soon_lemmas: Set[str] = field(default_factory=set)
    total_cards: int = 0
    mean_stability: float = 0.0
    mean_difficulty: float = 0.0
    level_signal: float = 0.0  # Rough 0-6 CEFR-ish scalar

    def is_known(self, lemma: str) -> bool:
        return lemma.lower() in self.known_lemmas

    def is_learning(self, lemma: str) -> bool:
        return lemma.lower() in self.learning_lemmas

    def is_familiar(self, lemma: str) -> bool:
        """Known or actively learning."""
        l = lemma.lower()
        return l in self.known_lemmas or l in self.learning_lemmas


# In-process LRU-ish cache. Keyed by (user_id, language).
_CACHE: dict[tuple[int, str], VocabProfile] = {}


def invalidate(user_id: int, language: str) -> None:
    """Drop the cached profile for this user+language. Call on review submit."""
    _CACHE.pop((user_id, language), None)


def _backfill_lemmas_for_user(db: Session, user_id: int, language: str) -> int:
    """
    Lemmatize and persist the lemma column for any cards belonging to this user
    in this language that don't have a lemma yet. Idempotent.

    Supports Spanish ('es') and English ('en').
    """
    if language not in ("es", "en"):
        return 0

    cards = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == user_id,
            UserFlashcardProgress.language == language,
            UserFlashcardProgress.lemma.is_(None),
        )
        .all()
    )
    if not cards:
        return 0

    for card in cards:
        try:
            card.lemma = lemmatize_word(card.word, language)
        except Exception:
            card.lemma = (card.word or "").lower().strip()

    db.commit()
    return len(cards)


def _compute_level_signal(known_count: int, mean_stability: float,
                          mean_difficulty: float) -> float:
    """
    Rough 0-6 scalar mapping (CEFR-ish):
      0 = pre-A1 (no mature cards)
      1 = A1 (100+ known)
      2 = A2 (500+ known)
      3 = B1 (1500+)
      4 = B2 (3500+)
      5 = C1 (6000+)
      6 = C2 (9000+)
    Adjusted slightly by mean stability/difficulty.
    """
    if known_count < 50:
        return 0.0

    breakpoints = [50, 250, 1000, 2500, 5000, 8000]
    base = 0.0
    for i, bp in enumerate(breakpoints):
        if known_count >= bp:
            base = float(i + 1)
        else:
            # Interpolate between this breakpoint and the previous one
            prev_bp = breakpoints[i - 1] if i > 0 else 0
            frac = (known_count - prev_bp) / max(bp - prev_bp, 1)
            base = float(i) + frac
            break

    # Tiny adjustments based on FSRS health (max ±0.3)
    if mean_stability > 60:
        base += 0.15
    if mean_difficulty < 5:
        base += 0.15

    return min(base, 6.0)


def build_profile(db: Session, user_id: int, language: str) -> VocabProfile:
    """
    Build (or return cached) vocab profile for a user+language.
    Triggers lazy lemma backfill on first call per user.
    """
    cache_key = (user_id, language)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Lazy backfill — keeps the lemma column populated for active users
    _backfill_lemmas_for_user(db, user_id, language)

    cards = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == user_id,
            UserFlashcardProgress.language == language,
        )
        .all()
    )

    profile = VocabProfile(user_id=user_id, language=language)
    if not cards:
        _CACHE[cache_key] = profile
        return profile

    profile.total_cards = len(cards)

    total_stability = 0.0
    total_difficulty = 0.0
    now = datetime.utcnow()

    for card in cards:
        lemma = (card.lemma or card.word or "").lower().strip()
        if not lemma:
            continue

        stability = float(card.stability or 0.0)
        difficulty = float(card.difficulty or 0.0)
        state = int(card.state or 0)
        total_stability += stability
        total_difficulty += difficulty

        # "Known" gate: in review/relearning state AND stable for at least N days
        if state in (_STATE_REVIEW, _STATE_RELEARNING) and stability >= _KNOWN_STABILITY_DAYS:
            profile.known_lemmas.add(lemma)
        elif state in (_STATE_NEW, _STATE_LEARNING) or stability < _KNOWN_STABILITY_DAYS:
            profile.learning_lemmas.add(lemma)

        if card.due and card.due <= now:
            profile.due_soon_lemmas.add(lemma)

    if profile.total_cards:
        profile.mean_stability = total_stability / profile.total_cards
        profile.mean_difficulty = total_difficulty / profile.total_cards
    profile.level_signal = _compute_level_signal(
        len(profile.known_lemmas),
        profile.mean_stability,
        profile.mean_difficulty,
    )

    _CACHE[cache_key] = profile
    return profile


def cefr_label(level_signal: float) -> str:
    """Map the 0-6 scalar to a CEFR-ish label."""
    if level_signal < 0.5:
        return "Pre-A1"
    if level_signal < 1.5:
        return "A1"
    if level_signal < 2.5:
        return "A2"
    if level_signal < 3.5:
        return "B1"
    if level_signal < 4.5:
        return "B2"
    if level_signal < 5.5:
        return "C1"
    return "C2"
