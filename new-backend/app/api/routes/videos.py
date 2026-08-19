import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.video_store import (
    add_video, get_all_videos, get_filtered_videos,
    get_unchecked_videos, update_korean_status,
    get_ukrainian_filtered_videos, get_unchecked_ukrainian_videos,
    update_ukrainian_status, get_total_watch_time, update_video_title,
    get_english_filtered_videos, get_unchecked_english_videos, update_english_status,
    add_user_watch, get_user_videos, get_user_filtered_videos,
    delete_user_video, add_watch_time,
)

router = APIRouter()


class TrackVideoRequest(BaseModel):
    video_id: str
    title: str = "Unknown"
    caption_languages: list[str] = []
    watched_at: float | None = None   # Unix timestamp; defaults to now if omitted
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None


class StatusUpdate(BaseModel):
    has_korean: bool


class UkrainianStatusUpdate(BaseModel):
    has_ukrainian: bool


class EnglishStatusUpdate(BaseModel):
    has_english: bool


class TitleUpdate(BaseModel):
    title: str


class WatchTimeUpdate(BaseModel):
    video_id: str
    seconds: int


@router.post("/track")
async def track_video(
    req: TrackVideoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Receive a video ID from the Chrome extension and store it for the current user."""
    if not req.video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    # Update global subtitle-availability flags
    is_new = add_video(req.video_id, req.title, req.season, req.episode, req.episode_title)
    # add_video() never updates the title on an existing record (only
    # season/episode). The extension often tracks a video twice — once
    # immediately with the "Unknown" placeholder, then again once the
    # content script resolves the real title — so backfill it here whenever
    # a non-generic title comes in. update_video_title() only overwrites a
    # still-generic title, so this is safe to call unconditionally.
    if not is_new and req.title and req.title != "Unknown":
        update_video_title(req.video_id, req.title)
    if req.caption_languages:
        has_ko = any(l == 'ko' or l.startswith('ko-') for l in req.caption_languages)
        has_uk = any(l == 'uk' or l.startswith('uk-') for l in req.caption_languages)
        has_en = any(l == 'en' or l.startswith('en-') for l in req.caption_languages)
        update_korean_status(req.video_id, has_ko and has_en)
        update_ukrainian_status(req.video_id, has_uk and has_en)
        update_english_status(req.video_id, has_en)

    # Link this video to the current user
    watched_at = req.watched_at if req.watched_at is not None else time.time()
    add_user_watch(db, current_user.id, req.video_id, watched_at)

    return {"status": "ok", "video_id": req.video_id, "is_new": is_new}


@router.get("/history")
async def get_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all videos tracked by the current user."""
    videos = get_user_videos(db, current_user.id)
    return {"total": len(videos), "videos": videos}


@router.get("/history/filtered")
async def get_filtered_history(
    lang: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the current user's videos that have subtitles in the target language.

    Availability is recorded when the extension tracks a video.  Older videos
    may have an unknown (NULL) status and are intentionally included by
    ``get_user_filtered_videos``.  Do not probe YouTube here: a history read is
    also made while navigating to Home, and synchronously checking every
    unknown video can block the single API worker long enough to stall the
    whole app.
    """
    videos = get_user_filtered_videos(db, current_user.id, lang)
    return {"total": len(videos), "lang": lang, "videos": videos}


@router.put("/{video_id}/status")
async def update_video_status(video_id: str, body: StatusUpdate):
    """Update the has_korean status for a video."""
    update_korean_status(video_id, body.has_korean)
    return {"status": "ok", "video_id": video_id, "has_korean": body.has_korean}


@router.put("/{video_id}/status/ukrainian")
async def update_video_ukrainian_status(video_id: str, body: UkrainianStatusUpdate):
    """Update the has_ukrainian status for a video."""
    update_ukrainian_status(video_id, body.has_ukrainian)
    return {"status": "ok", "video_id": video_id, "has_ukrainian": body.has_ukrainian}


@router.put("/{video_id}/status/english")
async def update_video_english_status(video_id: str, body: EnglishStatusUpdate):
    """Update the has_english status for a video."""
    update_english_status(video_id, body.has_english)
    return {"status": "ok", "video_id": video_id, "has_english": body.has_english}


@router.put("/{video_id}/title")
async def update_title(video_id: str, body: TitleUpdate):
    """Update the title for a video (only if current title is generic)."""
    updated = update_video_title(video_id, body.title)
    return {"status": "ok", "video_id": video_id, "updated": updated}


@router.get("/stats/watch-time")
async def get_watch_time_stats(
    lang: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get total watch time statistics for the current user's videos in the target language."""
    stats = get_total_watch_time(db, current_user.id, lang)
    return stats


@router.post("/watch-time")
async def update_watch_time(
    req: WatchTimeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add watch time seconds to a video for the current user."""
    if req.seconds <= 0:
        raise HTTPException(status_code=400, detail="seconds must be positive")
    total = add_watch_time(db, current_user.id, req.video_id, req.seconds)
    return {"status": "ok", "video_id": req.video_id, "total_seconds": total}


@router.get("/history/building")
async def get_building_videos(
    lang: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return Netflix videos that are still being built (tracked but no subtitles processed yet).
    These are videos where the user is actively watching but ClipIt hasn't captured subtitles yet.
    """
    from app.services.video_store import get_user_building_videos
    videos = get_user_building_videos(db, current_user.id, lang)
    return {"total": len(videos), "lang": lang, "videos": videos}


@router.delete("/{video_id}")
async def delete_video(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a video from the current user's watch history.
    Note: Flashcards are shared across videos and are not deleted.
    """
    deleted = delete_user_video(db, current_user.id, video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Video not found in your history")
    return {"status": "ok", "video_id": video_id}
