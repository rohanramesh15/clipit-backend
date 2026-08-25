import asyncio
import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_flashcard_progress import UserFlashcardProgress
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


# Home is a short practice queue.  A video can contain thousands of distinct
# caption tokens, but only a focused selection should be offered for study.
HOME_QUEUE_WORDS_PER_VIDEO = 20
HOME_QUEUE_TRANSLATION_CONCURRENCY = 4

_UNUSABLE_TRANSLATIONS = {
    "#",
    "definition available in practice",
    "definition not available",
    "translation unavailable",
}


def _usable_translation(value: object) -> str | None:
    """Return a display-ready English translation, rejecting old placeholders."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in _UNUSABLE_TRANSLATIONS:
        return None
    return cleaned


def _saved_translation(word: str, language: str, definitions: dict, user_definitions: dict) -> str | None:
    return _usable_translation(
        user_definitions.get(f"{language}:{word}")
        or definitions.get(word)
    )


async def _fill_queue_translations(cards: list[dict], language: str) -> None:
    """Fill the Home queue's missing English translations with bounded, cached lookups.

    The translation service persists successful word lookups in its local cache,
    so a word normally pays this cost once. Bounded concurrency keeps a first
    visit responsive and avoids bursting past the translation provider limit.
    """
    from app.services.deepl_service import translate

    source_language = {"ko": "KO", "uk": "UK", "en": "EN"}.get(language, "KO")
    semaphore = asyncio.Semaphore(HOME_QUEUE_TRANSLATION_CONCURRENCY)

    async def fill(card: dict) -> None:
        if _usable_translation(card.get("english")):
            return
        word = (card.get("dictionary_form") or card.get("target_word") or "").strip()
        if not word:
            card["english"] = "Translation unavailable"
            return
        async with semaphore:
            translated = await asyncio.to_thread(translate, word, source_lang=source_language)
        card["english"] = _usable_translation(translated) or "Translation unavailable"

    await asyncio.gather(*(fill(card) for card in cards))


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


@router.get("/home/queue")
async def get_home_queue(
    lang: str = "ko",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return useful, practice-ready vocabulary from watched captions.

    Home uses the same frequency-list and user-priority filtering as the
    practice modes. This avoids presenting every raw caption token (including
    common words and Korean surface-form variants) as vocabulary to learn.
    """
    from app.api.routes.vocabulary import _extract_vocabulary
    from app.api.routes.user_vocab import get_user_priority_mode, get_user_vocabulary_words

    all_watched_videos = get_user_videos(db, current_user.id)
    source_video_count = len(all_watched_videos)
    language_status_key = {
        "ko": "has_korean",
        "uk": "has_ukrainian",
        "en": "has_english",
    }.get(lang, "has_korean")
    watched_titles = {video["video_id"]: video["title"] for video in all_watched_videos}

    # Reuse locally cached English translations whenever available, then fill
    # only cache misses below. The queue shows translations rather than opaque
    # "definition available" placeholders.
    from app.api.routes.flashcards import load_definitions, load_user_definitions
    definitions = load_definitions()
    user_definitions = load_user_definitions()

    saved_cards = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.language == lang,
        )
        .all()
    )
    # Both are keyed on the user (and, for vocab, the language) only — not the
    # video — so fetch them once instead of once per watched video below.
    priority_mode = get_user_priority_mode(current_user.id, db)
    user_vocab = get_user_vocabulary_words(current_user.id, db, lang)

    cards: list[dict] = []
    for saved_card in saved_cards:
        if saved_card.video_id not in watched_titles:
            continue
        word = saved_card.lemma or saved_card.word
        cards.append({
            "target_word": word,
            "dictionary_form": word,
            "english": _saved_translation(word, lang, definitions, user_definitions),
            "video_id": saved_card.video_id,
            "video_title": watched_titles.get(saved_card.video_id, "Your saved practice"),
        })

    for video in all_watched_videos:
        # Do not attempt a server-side YouTube fetch for a source the
        # extension has already confirmed lacks this language's captions.
        if video.get(language_status_key) is False:
            continue
        try:
            vocabulary = await _extract_vocabulary(
                video["video_id"],
                HOME_QUEUE_WORDS_PER_VIDEO,
                lang,
                False,  # include_all
                False,  # apply_limits
                None,  # duration_seconds
                False,  # upgrade_cards
                db,
                current_user,
                # Keep the same focused candidate set used by practice: the
                # user's priority mode, common-word filtering, and a bounded
                # number of words per watched source all apply here. Passed
                # in pre-fetched so this doesn't re-query them per video.
                priority_mode=priority_mode,
                user_vocab=user_vocab,
            )
            vocabulary_items = vocabulary.get("vocabulary", [])
            words = [item["word"] for item in vocabulary_items]
            if not words:
                continue
        except Exception:
            continue

        for item in vocabulary_items:
            word = item["word"]
            cards.append({
                "target_word": word,
                "dictionary_form": word,
                "english": _saved_translation(word, lang, definitions, user_definitions),
                "video_id": video["video_id"],
                "video_title": video["title"],
            })

    # Deduplicate useful words across watched sources, preserving the most
    # recently watched source video.
    card_by_key: dict[str, dict] = {}
    for card in cards:
        card_by_key.setdefault(card["dictionary_form"] or card["target_word"], card)
    queue_cards = list(card_by_key.values())
    await _fill_queue_translations(queue_cards, lang)

    return {
        "lang": lang,
        "cards": queue_cards,
        "candidate_limit_per_video": HOME_QUEUE_WORDS_PER_VIDEO,
        "partial": False,
        "source_video_count": source_video_count,
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
