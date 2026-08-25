from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user_optional
from app.models.user import User
from app.services.subtitle_service import load_cached_subtitles, load_cached_subtitles_ukrainian, load_cached_subtitles_english
from app.services.korean_tokenizer import extract_korean_words_from_subtitles
from app.services.ukrainian_tokenizer import extract_ukrainian_words_from_subtitles
from app.services.english_tokenizer import extract_english_words_from_subtitles
from app.services.vocab_service import load_frequency_map, filter_vocabulary, filter_by_priority_mode, get_vocab_stats
from app.services.mining_service import apply_mining_limits, record_mined_words, get_mining_stats
from app.api.routes.netflix import load_cached_netflix_subtitles
from app.api.routes.user_vocab import get_user_vocabulary_words, get_user_priority_mode
from app.services.card_upgrade_service import auto_upgrade_tts_cards

router = APIRouter()

# Cache frequency maps in memory (loaded once at first request)
_FREQUENCY_MAP_KO: dict | None = None
_FREQUENCY_MAP_UK: dict | None = None
_FREQUENCY_MAP_EN: dict | None = None


def get_frequency_map(lang: str = 'ko') -> dict:
    global _FREQUENCY_MAP_KO, _FREQUENCY_MAP_UK, _FREQUENCY_MAP_EN
    if lang == 'uk':
        if _FREQUENCY_MAP_UK is None:
            _FREQUENCY_MAP_UK = load_frequency_map('uk')
        return _FREQUENCY_MAP_UK
    elif lang == 'en':
        if _FREQUENCY_MAP_EN is None:
            _FREQUENCY_MAP_EN = load_frequency_map('en')
        return _FREQUENCY_MAP_EN
    else:
        if _FREQUENCY_MAP_KO is None:
            _FREQUENCY_MAP_KO = load_frequency_map('ko')
        return _FREQUENCY_MAP_KO


@router.get("/vocabulary/{video_id}")
async def get_vocabulary(
    video_id: str,
    limit: int = 20,
    lang: str = Query('ko'),
    include_all: bool = Query(False, description="Return every distinct target-language caption word without priority filtering"),
    apply_limits: bool = Query(False, description="Apply mining limits (cap cards per duration, enforce gaps)"),
    duration_seconds: Optional[float] = Query(None, description="Video duration in seconds (required when apply_limits=True)"),
    upgrade_cards: bool = True,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Extract vocabulary from cached video subtitles.
    If user is authenticated, applies their priority mode and uploaded vocabulary.
    lang: 'ko' (Korean) or 'uk' (Ukrainian). Subtitles must be fetched first.
    Supports both YouTube and Netflix videos (Netflix videos have netflix_ prefix).

    Mining limits (when apply_limits=True):
    - Cap at ~4 cards per 10 minutes of video
    - Enforce 20-second gap between mined moments
    - Prioritize words appearing 2+ times in subtitles
    - Exclude words already mined from this video (authenticated users)
    """
    return await _extract_vocabulary(
        video_id, limit, lang, include_all, apply_limits, duration_seconds, upgrade_cards, db, current_user,
    )


async def _extract_vocabulary(
    video_id: str,
    limit: int,
    lang: str,
    include_all: bool,
    apply_limits: bool,
    duration_seconds: Optional[float],
    upgrade_cards: bool,
    db: Session,
    current_user: Optional[User],
    # Callers iterating over many videos for the same user (e.g. the Home
    # queue) can pass these in pre-fetched so this doesn't re-run the same
    # user-scoped (not video-scoped) queries once per video.
    priority_mode: Optional[str] = None,
    user_vocab: Optional[list] = None,
):
    # Handle Netflix videos
    if video_id.startswith('netflix_'):
        subtitle_data = load_cached_netflix_subtitles(video_id, lang)
        if lang == 'uk':
            lang_key = 'has_ukrainian'
            extract_fn = extract_ukrainian_words_from_subtitles
        else:
            lang_key = 'has_korean'
            extract_fn = extract_korean_words_from_subtitles
    elif lang == 'uk':
        subtitle_data = load_cached_subtitles_ukrainian(video_id)
        lang_key = 'has_ukrainian'
        extract_fn = extract_ukrainian_words_from_subtitles
    elif lang == 'en':
        subtitle_data = load_cached_subtitles_english(video_id)
        lang_key = 'has_english'
        extract_fn = extract_english_words_from_subtitles
    else:
        subtitle_data = load_cached_subtitles(video_id)
        lang_key = 'has_korean'
        extract_fn = extract_korean_words_from_subtitles

    if not subtitle_data:
        raise HTTPException(
            status_code=404,
            detail=f"Subtitles not found for {video_id}. Fetch them first via /api/subtitles/{video_id}?lang={lang}"
        )

    if not subtitle_data.get(lang_key):
        return {
            "video_id": video_id,
            "lang": lang,
            "total_words": 0,
            "vocabulary": [],
            "stats": {"total": 0},
            "priority_mode": None
        }

    words = extract_fn(subtitle_data["subtitles"])
    mining_info = None

    # The Home word inventory deliberately exposes every distinct word captured
    # from a video.  Practice/deck flows retain the learner's priority-mode
    # filtering so they remain focused and manageable.
    if include_all:
        limited = [
            {'word': word, 'rank': None, 'language': lang, 'source': 'caption'}
            for word in words
        ]
        priority_mode = 'all_caption_words'
    # If user is authenticated, apply priority mode (works for all supported languages)
    else:
        frequency_map = get_frequency_map(lang)
        if current_user:
            if priority_mode is None:
                priority_mode = get_user_priority_mode(current_user.id, db)
            if user_vocab is None:
                user_vocab = get_user_vocabulary_words(current_user.id, db, lang)
            filtered = filter_by_priority_mode(words, frequency_map, user_vocab, priority_mode, lang)
        else:
            priority_mode = None
            filtered = filter_vocabulary(words, frequency_map, language=lang)

        # Apply mining limits if requested
        if apply_limits and duration_seconds is not None:
            mining_result = apply_mining_limits(
                vocabulary=filtered,
                subtitles=subtitle_data["subtitles"],
                duration_seconds=duration_seconds,
                user_id=current_user.id if current_user else None,
                video_id=video_id,
                language=lang,
                db=db if current_user else None
            )
            limited = mining_result['vocabulary']
            mining_info = {
                'session_cap': mining_result['session_cap'],
                'excluded_previously_mined': mining_result['excluded_previously_mined'],
                'high_frequency_count': mining_result['high_frequency_count'],
                'duration_minutes': mining_result['duration_minutes'],
                'applied_limits': True
            }
        else:
            limited = filtered[:limit]

    stats = get_vocab_stats(limited)

    # Auto-upgrade TTS cards with video context for authenticated users
    upgraded_count = 0
    if current_user and upgrade_cards:
        upgraded_words = auto_upgrade_tts_cards(
            user_id=current_user.id,
            video_id=video_id,
            language=lang,
            db=db
        )
        upgraded_count = len(upgraded_words)

    response = {
        "video_id": video_id,
        "lang": lang,
        "total_words": len(limited),
        "vocabulary": limited,
        "stats": stats,
        "priority_mode": priority_mode
    }

    if mining_info:
        response["mining"] = mining_info

    if upgraded_count > 0:
        response["upgraded_tts_cards"] = upgraded_count

    return response


# ── Pydantic Schemas for Mining ─────────────────────────────────────────────

class MinedWordData(BaseModel):
    word: str
    mined_timestamp: Optional[float] = None


class RecordMinedWordsRequest(BaseModel):
    video_id: str
    words: List[MinedWordData]
    language: str = "ko"


# ── Mining Record Endpoints ─────────────────────────────────────────────────

@router.post("/mining/record")
async def record_mined_words_endpoint(
    request: RecordMinedWordsRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Record words that were mined from a video.
    Call this when user creates flashcards from mined words.
    These words will be excluded in future mining sessions for the same video.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required to record mined words")

    words_data = [{"word": w.word, "mined_timestamp": w.mined_timestamp} for w in request.words]

    new_count = record_mined_words(
        user_id=current_user.id,
        video_id=request.video_id,
        words=words_data,
        language=request.language,
        db=db
    )

    return {
        "status": "ok",
        "video_id": request.video_id,
        "words_recorded": new_count,
        "total_submitted": len(request.words)
    }


@router.get("/mining/stats/{video_id}")
async def get_mining_stats_endpoint(
    video_id: str,
    lang: str = Query('ko'),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Get mining statistics for a video.
    Returns count of words already mined from this video.
    """
    if not current_user:
        return {
            "video_id": video_id,
            "mined_count": 0,
            "language": lang,
            "authenticated": False
        }

    stats = get_mining_stats(
        user_id=current_user.id,
        video_id=video_id,
        language=lang,
        db=db
    )
    stats["authenticated"] = True

    return stats
