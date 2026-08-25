from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel as PydanticModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models.user_review_history import UserReviewHistory
from app.services.video_store import get_total_watch_time

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CardUpsert(PydanticModel):
    word: str
    language: str
    due: str                        # ISO datetime string from ts-fsrs
    stability: float = 0.0
    difficulty: float = 0.0
    elapsed_days: int = 0
    scheduled_days: int = 0
    reps: int = 0
    lapses: int = 0
    state: int = 0                  # 0=New 1=Learning 2=Review 3=Relearning
    last_review: Optional[str] = None
    video_id: Optional[str] = None  # YouTube video ID for deck organization


class CardBulkUpsert(PydanticModel):
    cards: list[CardUpsert]


class ReviewCreate(PydanticModel):
    word: str
    language: str
    rating: int                     # 1=Again 2=Hard 3=Good 4=Easy
    clip_duration: Optional[float] = None
    reviewed_at: str                # ISO datetime string


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string, handling the 'Z' UTC suffix."""
    if not dt_str:
        return None
    try:
        parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (ValueError, AttributeError):
        return datetime.utcnow()


def _apply_card_upsert(
    db: Session, user_id: int, card: CardUpsert
) -> UserFlashcardProgress:
    due = _parse_dt(card.due) or datetime.utcnow()
    last_review = _parse_dt(card.last_review)

    existing = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == user_id,
            UserFlashcardProgress.word == card.word,
            UserFlashcardProgress.language == card.language,
        )
        .first()
    )
    if existing:
        existing.due = due
        existing.stability = card.stability
        existing.difficulty = card.difficulty
        existing.elapsed_days = card.elapsed_days
        existing.scheduled_days = card.scheduled_days
        existing.reps = card.reps
        existing.lapses = card.lapses
        existing.state = card.state
        existing.last_review = last_review
        # Only update video_id if provided and not already set
        if card.video_id and not existing.video_id:
            existing.video_id = card.video_id
        return existing
    else:
        progress = UserFlashcardProgress(
            user_id=user_id,
            word=card.word,
            language=card.language,
            video_id=card.video_id,
            due=due,
            stability=card.stability,
            difficulty=card.difficulty,
            elapsed_days=card.elapsed_days,
            scheduled_days=card.scheduled_days,
            reps=card.reps,
            lapses=card.lapses,
            state=card.state,
            last_review=last_review,
        )
        db.add(progress)
        return progress


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/cards")
def get_cards(
    limit: int = 500,
    offset: int = 0,
    video_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return FSRS card states for the current user, with optional pagination and filtering."""
    query = (
        db.query(UserFlashcardProgress)
        .filter(UserFlashcardProgress.user_id == current_user.id)
    )

    # Filter by specific video (deck)
    if video_id:
        query = query.filter(UserFlashcardProgress.video_id == video_id)

    query = query.order_by(UserFlashcardProgress.word)
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "cards": [
            {
                "word": r.word,
                "language": r.language,
                "video_id": r.video_id,
                "due": r.due.isoformat() if r.due else None,
                "stability": r.stability,
                "difficulty": r.difficulty,
                "elapsed_days": r.elapsed_days,
                "scheduled_days": r.scheduled_days,
                "reps": r.reps,
                "lapses": r.lapses,
                "state": r.state,
                "last_review": r.last_review.isoformat() if r.last_review else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


def _invalidate_vocab_profile(user_id: int, language: str) -> None:
    """Best-effort: drop the cached chat vocab profile so the next chat sees
    the most recent card state. Safe to call without the chat module loaded."""
    try:
        from app.services.vocab_profile_service import invalidate
        invalidate(user_id, language)
    except Exception:
        pass


@router.post("/cards")
def upsert_card(
    card: CardUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert a single FSRS card state for the current user."""
    _apply_card_upsert(db, current_user.id, card)
    db.commit()
    _invalidate_vocab_profile(current_user.id, card.language)
    return {"status": "ok", "word": card.word, "language": card.language}


@router.post("/cards/bulk")
def upsert_cards_bulk(
    body: CardBulkUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert many FSRS card states at once (for initial localStorage migration)."""
    langs_touched = set()
    for card in body.cards:
        _apply_card_upsert(db, current_user.id, card)
        langs_touched.add(card.language)
    db.commit()
    for lang in langs_touched:
        _invalidate_vocab_profile(current_user.id, lang)
    return {"status": "ok", "upserted": len(body.cards)}


@router.post("/reviews")
def add_review(
    review: ReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append a review log entry for the current user."""
    reviewed_at = _parse_dt(review.reviewed_at) or datetime.utcnow()
    entry = UserReviewHistory(
        user_id=current_user.id,
        word=review.word,
        language=review.language,
        rating=review.rating,
        clip_duration=review.clip_duration,
        reviewed_at=reviewed_at,
    )
    db.add(entry)
    db.commit()
    _invalidate_vocab_profile(current_user.id, review.language)
    return {"status": "ok"}


@router.get("/reviews")
def get_reviews(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return review history for the current user (analytics), newest first."""
    query = (
        db.query(UserReviewHistory)
        .filter(UserReviewHistory.user_id == current_user.id)
        .order_by(UserReviewHistory.reviewed_at.desc())
    )
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "reviews": [
            {
                "word": r.word,
                "language": r.language,
                "rating": r.rating,
                "clip_duration": r.clip_duration,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            }
            for r in rows
        ],
    }


@router.get("/progress-summary")
def get_progress_summary(
    lang: str = "ko",
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the compact aggregate data needed by the Progress page.

    The UI needs a total and one count per active day, not every individual
    review event. Aggregating in Postgres keeps a long review history from
    turning a Progress visit into a multi-megabyte response.
    """
    target_year = year or datetime.utcnow().year
    start = datetime(target_year, 1, 1)
    end = datetime(target_year + 1, 1, 1)
    reviews = db.query(UserReviewHistory).filter(
        UserReviewHistory.user_id == current_user.id,
    )
    total_reviews = reviews.count()
    daily_rows = (
        db.query(
            func.date(UserReviewHistory.reviewed_at).label("date"),
            func.count(UserReviewHistory.id).label("count"),
        )
        .filter(
            UserReviewHistory.user_id == current_user.id,
            UserReviewHistory.reviewed_at >= start,
            UserReviewHistory.reviewed_at < end,
        )
        .group_by(func.date(UserReviewHistory.reviewed_at))
        .order_by(func.date(UserReviewHistory.reviewed_at))
        .all()
    )
    reviews_by_date = {
        value.isoformat() if hasattr(value, "isoformat") else str(value): count
        for value, count in daily_rows
    }
    watch_time = get_total_watch_time(db, current_user.id, lang)
    return {
        "total_reviews": total_reviews,
        "reviews_by_date": reviews_by_date,
        "total_hours": watch_time.get("total_hours", 0),
    }


@router.get("/reviews/today")
def get_reviews_today(
    tz_offset_minutes: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return how many cards the current user reviewed today in their local timezone."""
    now_utc = datetime.utcnow()
    local_now = now_utc - timedelta(minutes=tz_offset_minutes)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start + timedelta(minutes=tz_offset_minutes)
    utc_end = local_end + timedelta(minutes=tz_offset_minutes)

    count = (
        db.query(UserReviewHistory)
        .filter(
            UserReviewHistory.user_id == current_user.id,
            UserReviewHistory.reviewed_at >= utc_start,
            UserReviewHistory.reviewed_at < utc_end,
        )
        .count()
    )

    return {
        "count": count,
        "date": local_start.date().isoformat(),
        "tz_offset_minutes": tz_offset_minutes,
    }


@router.delete("/cards/{word}")
def delete_card(
    word: str,
    language: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a flashcard from the user's progress. Also removes review history."""
    # Delete the card progress
    deleted = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.word == word,
            UserFlashcardProgress.language == language,
        )
        .delete()
    )

    # Also delete review history for this word
    db.query(UserReviewHistory).filter(
        UserReviewHistory.user_id == current_user.id,
        UserReviewHistory.word == word,
        UserReviewHistory.language == language,
    ).delete()

    db.commit()

    if not deleted:
        raise HTTPException(status_code=404, detail="Card not found")

    return {"status": "ok", "word": word, "language": language}


@router.delete("/cards/video/{video_id}")
def delete_cards_by_video(
    video_id: str,
    language: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all flashcards for a specific video from the user's progress."""
    # Get all cards for this video first (to delete their review history)
    cards = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.video_id == video_id,
            UserFlashcardProgress.language == language,
        )
        .all()
    )

    words_deleted = [card.word for card in cards]

    # Delete the card progress
    deleted_count = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.video_id == video_id,
            UserFlashcardProgress.language == language,
        )
        .delete()
    )

    # Also delete review history for these words
    if words_deleted:
        db.query(UserReviewHistory).filter(
            UserReviewHistory.user_id == current_user.id,
            UserReviewHistory.word.in_(words_deleted),
            UserReviewHistory.language == language,
        ).delete(synchronize_session=False)

    db.commit()

    return {
        "status": "ok",
        "video_id": video_id,
        "language": language,
        "deleted_count": deleted_count,
    }
