"""
Dual proficiency tracking:
  - recognition_score (0-6 CEFR-ish): derived from FSRS card stability + count
  - production_score (0-6): EWMA over end-of-session rubric levels

These typically diverge by 1-2 levels for active learners; tracking both
lets us show users their progress *closing the gap* over time.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.user_language_profile import UserLanguageProfile
from app.services.vocab_profile_service import build_profile

_EWMA_ALPHA = 0.3  # New session weighed 30% vs prior 70%

_CEFR_TO_SCALAR = {
    "Pre-A1": 0.0, "A1": 1.0, "A2": 2.0,
    "B1": 3.0, "B2": 4.0, "C1": 5.0, "C2": 6.0,
}


def _cefr_to_scalar(label: Optional[str]) -> Optional[float]:
    if not label:
        return None
    return _CEFR_TO_SCALAR.get(label.strip())


def get_or_create_profile(db: Session, user_id: int, language: str) -> UserLanguageProfile:
    profile = (
        db.query(UserLanguageProfile)
        .filter(
            UserLanguageProfile.user_id == user_id,
            UserLanguageProfile.language == language,
        )
        .first()
    )
    if profile is None:
        profile = UserLanguageProfile(user_id=user_id, language=language, session_count=0)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def refresh_recognition_score(db: Session, user_id: int, language: str) -> float:
    """
    Recompute recognition_score from current FSRS state.
    Returns the new score.
    """
    vocab = build_profile(db, user_id, language)
    score = vocab.level_signal

    record = get_or_create_profile(db, user_id, language)
    record.recognition_score = score
    db.commit()
    return score


def update_production_score(
    db: Session,
    user_id: int,
    language: str,
    rubric_level_label: Optional[str],
) -> Optional[float]:
    """
    EWMA update of production_score using an end-of-session rubric CEFR label.
    Returns the new score (or None if the label couldn't be parsed).
    """
    new_signal = _cefr_to_scalar(rubric_level_label)
    if new_signal is None:
        return None

    record = get_or_create_profile(db, user_id, language)
    prior = record.production_score
    if prior is None:
        new_score = new_signal
    else:
        new_score = _EWMA_ALPHA * new_signal + (1 - _EWMA_ALPHA) * prior

    record.production_score = round(new_score, 3)
    record.session_count = (record.session_count or 0) + 1
    db.commit()
    return record.production_score


def get_scores(db: Session, user_id: int, language: str) -> dict:
    """Return both scores + CEFR labels for the API."""
    record = (
        db.query(UserLanguageProfile)
        .filter(
            UserLanguageProfile.user_id == user_id,
            UserLanguageProfile.language == language,
        )
        .first()
    )
    from app.services.vocab_profile_service import cefr_label

    rec = record.recognition_score if record else None
    prod = record.production_score if record else None
    sessions = record.session_count if record else 0

    return {
        "language": language,
        "recognition_score": rec,
        "recognition_label": cefr_label(rec) if rec is not None else None,
        "production_score": prod,
        "production_label": cefr_label(prod) if prod is not None else None,
        "session_count": sessions,
    }
