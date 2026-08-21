import time
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_word import UserVocabularyWord
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


# Home is a preview queue, not a full review deck.  Keep its cold-path work
# small enough to remain responsive even when definitions need DeepL.
HOME_QUEUE_VIDEO_LIMIT = 4
HOME_QUEUE_WORDS_PER_VIDEO = 4
HOME_QUEUE_BUILD_CONCURRENCY = 4
HOME_QUEUE_BUILD_TIMEOUT_SECONDS = 7


@router.get("/home/queue")
async def get_home_queue(
    lang: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Build the practice queue in one authenticated request.

    The old Home flow first fetched history, then made up to three additional
    browser requests per video. Keeping the candidate selection and card
    assembly together avoids that request fan-out while preserving the
    client-side FSRS ordering and local deleted-card state.
    """
    # Imports stay local to keep this router independent from the vocabulary
    # and flashcard routers during application startup.
    from app.api.routes.flashcards import _build_one_flashcard, _load_flashcard_context
    from app.api.routes.vocabulary import get_vocabulary

    uploaded_words = (
        db.query(UserVocabularyWord)
        .join(UserVocabularyList)
        .filter(
            UserVocabularyList.user_id == current_user.id,
            UserVocabularyList.language == lang,
        )
        .order_by(UserVocabularyList.created_at, UserVocabularyWord.sort_order)
        .all()
    )
    cards = [
        {
            "target_word": word.word,
            "dictionary_form": word.word,
            "english": word.translation,
            "video_id": None,
            "video_title": "Your vocabulary list",
        }
        for word in uploaded_words
    ]

    videos = get_user_filtered_videos(db, current_user.id, lang)[:HOME_QUEUE_VIDEO_LIMIT]
    # Keep the watched-video count in the response even when none of those
    # videos can produce a card yet.  The client uses it to distinguish
    # "nothing watched" from "captions/cards are still being prepared".
    source_video_count = len(videos)
    preparing_video_ids: set[str] = set()
    card_jobs: list[tuple[str, str, str, dict]] = []
    for video in videos:
        try:
            # Home needs a compact varied queue, not the full video deck. Skip
            # the deck-upgrade write work here; review sessions still perform
            # it through their existing vocabulary endpoint.
            vocabulary = await get_vocabulary(
                video_id=video["video_id"],
                limit=HOME_QUEUE_WORDS_PER_VIDEO,
                lang=lang,
                db=db,
                current_user=current_user,
                upgrade_cards=False,
            )
            words = [item["word"] for item in vocabulary.get("vocabulary", [])]
            if not words:
                continue
            context = await asyncio.to_thread(_load_flashcard_context, video["video_id"], lang)
            card_jobs.extend((word, video["video_id"], video["title"], context) for word in words)
        except Exception:
            # A watched video may still be processing subtitles. It should not
            # make the whole home queue fail, but it is important to report
            # that state rather than presenting it as an empty watch history.
            preparing_video_ids.add(video["video_id"])
            continue

    semaphore = asyncio.Semaphore(HOME_QUEUE_BUILD_CONCURRENCY)

    async def build_card(job: tuple[str, str, str, dict]) -> dict | None:
        word, video_id, video_title, context = job
        async with semaphore:
            try:
                card = await asyncio.to_thread(_build_one_flashcard, word, video_id, lang, context)
                card["video_title"] = video_title
                return card
            except Exception:
                # One uncached translation or malformed subtitle should not
                # blank the complete Home queue.
                return None

    # Never hold the first signed-in screen hostage to cold translation cache
    # misses.  Return every card that is ready within the budget; the complete
    # per-video decks are still generated by the dedicated practice flows.
    generated = []
    pending: set[asyncio.Task] = set()
    if card_jobs:
        tasks = [asyncio.create_task(build_card(job)) for job in card_jobs]
        done, pending = await asyncio.wait(tasks, timeout=HOME_QUEUE_BUILD_TIMEOUT_SECONDS)
        for task in done:
            try:
                card = task.result()
            except Exception:
                card = None
            if card is not None:
                generated.append(card)

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    cards.extend(card for card in generated if card is not None)

    # If every card for an otherwise usable source misses the first-load time
    # budget (or its definition cache), it is still an in-progress source from
    # the learner's perspective.
    if card_jobs and not generated:
        preparing_video_ids.update(video_id for _, video_id, _, _ in card_jobs)

    # Match the old UI's last-write-wins deduplication. Keep every uploaded
    # word: the browser owns FSRS priority, so truncating this list here could
    # hide a locally due card before it gets a chance to be selected.
    card_by_key = {card["dictionary_form"] or card["target_word"]: card for card in cards}
    return {
        "lang": lang,
        "cards": list(card_by_key.values()),
        "partial": bool(pending),
        "source_video_count": source_video_count,
        "preparing_video_count": len(preparing_video_ids),
    }


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
