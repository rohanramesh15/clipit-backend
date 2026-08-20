from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.deck_settings import DeckSettings
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.services.video_store import get_video_by_id

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class DeckRename(BaseModel):
    name: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def get_decks(
    language: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all decks (videos with flashcards) for the current user.
    Each video = one deck.
    """
    # Get all videos that have flashcards for this user
    video_stats = (
        db.query(
            UserFlashcardProgress.video_id,
            func.count(UserFlashcardProgress.id).label("total_count"),
            func.sum(
                func.case(
                    (UserFlashcardProgress.due <= func.now(), 1),
                    else_=0
                )
            ).label("due_count"),
        )
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.language == language,
            UserFlashcardProgress.video_id.isnot(None),
        )
        .group_by(UserFlashcardProgress.video_id)
        .all()
    )

    # Get custom names for decks
    deck_settings = (
        db.query(DeckSettings)
        .filter(DeckSettings.user_id == current_user.id)
        .all()
    )
    custom_names = {ds.video_id: ds.custom_name for ds in deck_settings}

    # Build deck list
    decks = []
    for video_id, total_count, due_count in video_stats:
        if not video_id:
            continue

        # Get video title and episode info from video store
        video_info = get_video_by_id(video_id)
        video_title = video_info.get("title", f"Video {video_id}") if video_info else f"Video {video_id}"
        season = video_info.get("season") if video_info else None
        episode = video_info.get("episode") if video_info else None
        episode_title = video_info.get("episode_title") if video_info else None

        decks.append({
            "video_id": video_id,
            "name": custom_names.get(video_id) or video_title,
            "video_title": video_title,
            "season": season,
            "episode": episode,
            "episode_title": episode_title,
            "due_count": int(due_count) if due_count else 0,
            "total_count": int(total_count) if total_count else 0,
        })

    # Calculate total due across all cards
    total_due = sum(d["due_count"] for d in decks)
    total_cards = sum(d["total_count"] for d in decks)

    return {
        "total_due": total_due,
        "total_cards": total_cards,
        "decks": decks,
    }


@router.put("/{video_id}/rename")
def rename_deck(
    video_id: str,
    body: DeckRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a deck (set custom name for a video)."""
    existing = (
        db.query(DeckSettings)
        .filter(
            DeckSettings.user_id == current_user.id,
            DeckSettings.video_id == video_id,
        )
        .first()
    )

    if existing:
        existing.custom_name = body.name if body.name.strip() else None
    else:
        if body.name.strip():
            settings = DeckSettings(
                user_id=current_user.id,
                video_id=video_id,
                custom_name=body.name,
            )
            db.add(settings)

    db.commit()
    return {"status": "ok", "video_id": video_id, "name": body.name}


@router.delete("/{video_id}")
def delete_deck(
    video_id: str,
    delete_cards: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a deck (video's flashcards).
    If delete_cards is True, deletes all flashcards for this video.
    """
    deleted_count = 0

    if delete_cards:
        # Delete all flashcard progress for this video
        deleted_count = (
            db.query(UserFlashcardProgress)
            .filter(
                UserFlashcardProgress.user_id == current_user.id,
                UserFlashcardProgress.video_id == video_id,
            )
            .delete()
        )

    # Remove custom name settings
    db.query(DeckSettings).filter(
        DeckSettings.user_id == current_user.id,
        DeckSettings.video_id == video_id,
    ).delete()

    db.commit()

    return {
        "status": "ok",
        "video_id": video_id,
        "cards_deleted": deleted_count,
    }
