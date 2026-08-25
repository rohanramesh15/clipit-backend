"""
Converse V2 prototype API.

A self-contained rebuild of the Converse flow implementing:
  - light onboarding (level + reason + how-much-English)
  - a chat that recycles "due" words and steers them into production
  - the English-support ladder (tap-to-translate, "stuck?" hint,
    "how do I say...?", level-matched suggested replies, correction + why)
  - two one-tap feedback buttons (session too easy/hard, correction fine/wrong)

Login is intentionally skipped: everything is keyed by a fixed prototype user.
Mounted under /api/converse2.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from google import genai
from google.genai import types as gtypes

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, get_db
from app.api.deps import get_current_user, get_current_user_optional
from app.models.user import User
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models import converse_v2 as m
from app.services import converse_v2_service as svc
from app.services.video_store import get_user_filtered_videos
from app.services.subtitle_service import (
    load_cached_subtitles,
    load_cached_subtitles_ukrainian,
    load_cached_subtitles_english,
)
from app.services.korean_tokenizer import extract_korean_words_from_subtitles
from app.services.ukrainian_tokenizer import extract_ukrainian_words_from_subtitles
from app.services.english_tokenizer import extract_english_words_from_subtitles
from app.services.vocab_service import load_frequency_map, filter_by_priority_mode
from app.api.routes.netflix import load_cached_netflix_subtitles
from app.api.routes.user_vocab import get_user_priority_mode, get_user_vocabulary_words
from app.api.routes.flashcards import iter_flashcard_data
from app.api.routes.chat import _wrap_pcm_as_wav
from app.services.gemini_chat_service import synthesize_tts

router = APIRouter()

USER_KEY = "prototype"
MIXED_WORD_LIMIT = 6
_frequency_maps: dict[str, dict] = {}

# Create the cv2_* tables once, lazily, so a DB hiccup never blocks app import.
_tables_ready = False


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    try:
        Base.metadata.create_all(
            bind=engine,
            tables=[
                m.CV2Profile.__table__,
                m.CV2Session.__table__,
                m.CV2Turn.__table__,
                m.CV2Feedback.__table__,
            ],
        )
        # create_all only creates missing tables — it never alters an existing
        # one. cv2_session predates user_id, so patch it in for any DB where
        # the table was already created before this column existed.
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE cv2_session ADD COLUMN IF NOT EXISTS user_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cv2_session_user_id ON cv2_session (user_id)"))
    finally:
        _tables_ready = True


# --------------------------------------------------------------------------
# Mock content (prototype). "Due words" themed by the learner's reason, and a
# small gallery of mock YouTube videos to use as conversation seeds.
# --------------------------------------------------------------------------

_BASE_WORDS = [
    {"lemma": "porque", "gloss": "because"},
    {"lemma": "todavía", "gloss": "still / yet"},
    {"lemma": "intentar", "gloss": "to try"},
    {"lemma": "aunque", "gloss": "although"},
    {"lemma": "conseguir", "gloss": "to get / manage to"},
]

_WORDS_BY_REASON = {
    "travel": [
        {"lemma": "billete", "gloss": "ticket"},
        {"lemma": "alojamiento", "gloss": "accommodation"},
        {"lemma": "perderse", "gloss": "to get lost"},
        {"lemma": "reservar", "gloss": "to book / reserve"},
        {"lemma": "equipaje", "gloss": "luggage"},
        {"lemma": "recomendar", "gloss": "to recommend"},
    ],
    "work": [
        {"lemma": "reunión", "gloss": "meeting"},
        {"lemma": "plazo", "gloss": "deadline"},
        {"lemma": "encargarse", "gloss": "to take care of"},
        {"lemma": "informe", "gloss": "report"},
        {"lemma": "acuerdo", "gloss": "agreement"},
        {"lemma": "disponible", "gloss": "available"},
    ],
    "family": [
        {"lemma": "cariño", "gloss": "affection / dear"},
        {"lemma": "echar de menos", "gloss": "to miss (someone)"},
        {"lemma": "crecer", "gloss": "to grow up"},
        {"lemma": "cuñado", "gloss": "brother-in-law"},
        {"lemma": "celebrar", "gloss": "to celebrate"},
        {"lemma": "reunirse", "gloss": "to get together"},
    ],
    "partner": [
        {"lemma": "extrañar", "gloss": "to miss"},
        {"lemma": "cita", "gloss": "date"},
        {"lemma": "discutir", "gloss": "to argue / discuss"},
        {"lemma": "apoyar", "gloss": "to support"},
        {"lemma": "planear", "gloss": "to plan"},
        {"lemma": "sentir", "gloss": "to feel"},
    ],
    "show": [
        {"lemma": "trama", "gloss": "plot"},
        {"lemma": "personaje", "gloss": "character"},
        {"lemma": "temporada", "gloss": "season"},
        {"lemma": "darse cuenta", "gloss": "to realize"},
        {"lemma": "emocionante", "gloss": "exciting"},
        {"lemma": "spoiler / destripar", "gloss": "to spoil"},
    ],
    "general": [
        {"lemma": "cotidiano", "gloss": "everyday"},
        {"lemma": "soler", "gloss": "to usually (do)"},
        {"lemma": "mejorar", "gloss": "to improve"},
        {"lemma": "costumbre", "gloss": "habit / custom"},
        {"lemma": "elegir", "gloss": "to choose"},
        {"lemma": "disfrutar", "gloss": "to enjoy"},
    ],
}

# Mock YouTube videos (Spanish content) used as chat seeds. Thumbnails come from
# img.youtube.com; the frontend falls back to a gradient card if one 404s.
_MOCK_VIDEOS = [
    {"video_id": "dQw4w9WgXcQ", "title": "Un día en Madrid: vlog de viaje", "channel": "Spanish Vlogs", "level": "beginner"},
    {"video_id": "kXYiU_JCYtU", "title": "La Casa de Papel — escena clave", "channel": "Series en Español", "level": "intermediate"},
    {"video_id": "L_jWHffIx5E", "title": "Receta de paella valenciana", "channel": "Cocina Española", "level": "beginner"},
    {"video_id": "fJ9rUzIMcZQ", "title": "Entrevista de trabajo en español", "channel": "Español Profesional", "level": "advanced"},
    {"video_id": "ScMzIvxBSi4", "title": "Música latina: lo más escuchado", "channel": "Ritmo Latino", "level": "intermediate"},
    {"video_id": "9bZkp7q19f0", "title": "Conversación en un café", "channel": "Spanish Daily", "level": "beginner"},
]


def _due_words_for(reason: str, limit: int = 6) -> list[dict]:
    """Prototype stand-in for the SRS 'words due to review' list, themed by reason."""
    themed = _WORDS_BY_REASON.get(reason, _WORDS_BY_REASON["general"])
    pool = themed + _BASE_WORDS
    return pool[:limit]


def _video_vocabulary_words(video_id: str, language: str, user_id: int, db: Session) -> list[str]:
    """Return the same eligible vocabulary pool shown for one tracked video."""
    if video_id.startswith("netflix_"):
        subtitle_data = load_cached_netflix_subtitles(video_id, language)
    elif language == "uk":
        subtitle_data = load_cached_subtitles_ukrainian(video_id)
    elif language == "en":
        subtitle_data = load_cached_subtitles_english(video_id)
    else:
        subtitle_data = load_cached_subtitles(video_id)

    if not subtitle_data or not subtitle_data.get("subtitles"):
        return []

    if language == "uk":
        extract_words = extract_ukrainian_words_from_subtitles
    elif language == "en":
        extract_words = extract_english_words_from_subtitles
    else:
        extract_words = extract_korean_words_from_subtitles

    frequency_map = _frequency_maps.get(language)
    if frequency_map is None:
        frequency_map = load_frequency_map(language)
        _frequency_maps[language] = frequency_map
    filtered = filter_by_priority_mode(
        extract_words(subtitle_data["subtitles"]),
        frequency_map,
        get_user_vocabulary_words(user_id, db, language),
        get_user_priority_mode(user_id, db),
        language,
    )
    return [item["word"] for item in filtered]


def _mixed_candidates_for_user(db: Session, user: User, language: str, limit: int = MIXED_WORD_LIMIT) -> list[dict]:
    """Choose up to ``limit`` real words, balanced across the user's videos.

    Due FSRS cards are preferred. If a user has no synced FSRS state yet, the
    video vocabulary pool supplies new words so Mixed Chat remains usable from
    the first tracked video onward.
    """
    videos = get_user_filtered_videos(db, user.id, language)
    if not videos:
        return []

    video_ids = [video["video_id"] for video in videos]
    candidates_by_video: dict[str, list[dict]] = {video_id: [] for video_id in video_ids}
    seen_words: set[str] = set()

    def add_candidate(video: dict, word: str) -> None:
        normalized = word.strip().lower()
        if not normalized or normalized in seen_words:
            return
        seen_words.add(normalized)
        candidates_by_video[video["video_id"]].append({
            "word": word.strip(),
            "video_id": video["video_id"],
            "video_title": video["title"],
        })

    videos_by_id = {video["video_id"]: video for video in videos}
    due_cards = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == user.id,
            UserFlashcardProgress.language == language,
            UserFlashcardProgress.video_id.in_(video_ids),
            UserFlashcardProgress.due <= datetime.utcnow(),
        )
        .order_by(UserFlashcardProgress.due.asc())
        .all()
    )
    for card in due_cards:
        video = videos_by_id.get(card.video_id)
        if video:
            add_candidate(video, card.lemma or card.word)

    # Fill every source with genuine vocabulary only after due cards, so a
    # learner can still start a mixed session before their local FSRS state has
    # synchronized to the backend.
    for video in videos:
        try:
            for word in _video_vocabulary_words(video["video_id"], language, user.id, db):
                add_candidate(video, word)
        except Exception:
            # One stale subtitle cache must not hide the rest of the user's decks.
            continue

    selected: list[dict] = []
    while len(selected) < limit:
        added_this_round = False
        for video_id in video_ids:
            if not candidates_by_video[video_id]:
                continue
            selected.append(candidates_by_video[video_id].pop(0))
            added_this_round = True
            if len(selected) == limit:
                break
        if not added_this_round:
            break
    return selected


def _mixed_source_videos(candidates: list[dict]) -> list[dict]:
    """Return each selected source once, in the same order as the session mix."""
    sources: list[dict] = []
    source_ids: set[str] = set()
    for candidate in candidates:
        if candidate["video_id"] in source_ids:
            continue
        source_ids.add(candidate["video_id"])
        sources.append({"video_id": candidate["video_id"], "title": candidate["video_title"]})
    return sources


def _materialize_mixed_words(candidates: list[dict], language: str) -> list[dict]:
    """Attach a definition and dictionary form while retaining source metadata."""
    due_words: list[dict] = []
    seen_lemmas: set[str] = set()
    for candidate in candidates:
        try:
            card = next(iter_flashcard_data(candidate["video_id"], [candidate["word"]], language))
        except Exception:
            continue
        lemma = (card.get("dictionary_form") or candidate["word"]).strip()
        if not lemma or lemma.lower() in seen_lemmas:
            continue
        seen_lemmas.add(lemma.lower())
        due_words.append({
            "lemma": lemma,
            "gloss": card.get("english") or "Definition unavailable",
            "source_video_id": candidate["video_id"],
            "source_video_title": candidate["video_title"],
        })
    return due_words


# --------------------------------------------------------------------------
# Profile helpers
# --------------------------------------------------------------------------

def _get_profile_row(db: Session) -> m.CV2Profile | None:
    return db.query(m.CV2Profile).filter(m.CV2Profile.user_key == USER_KEY).first()


def _profile_dict(row: m.CV2Profile | None) -> dict | None:
    if not row:
        return None
    return {
        "level": row.level,
        "reason": row.reason,
        "english_support": row.english_support,
    }


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class OnboardingRequest(BaseModel):
    level: str = "beginner"
    reason: str = "general"
    english_support: str = "some"


class SeedWord(BaseModel):
    lemma: str
    gloss: str = ""


class SessionRequest(BaseModel):
    seed_type: str = "due_words"          # due_words | video | free | topic
    video_id: str | None = None
    seed_label: str | None = None         # for seed_type == "topic": the topic + context line
    language: str = "ko"                   # target language: es | uk | ko
    # For seed_type == "video": the actual words extracted from the chosen video.
    # When provided these become the session's target words (woven + tracked),
    # replacing the themed mock list. lemma = dictionary form, gloss = English.
    seed_words: list[SeedWord] | None = None
    # The web chat creates the session immediately, then streams its opening
    # reply through the dedicated SSE endpoint below.
    stream_opening: bool = False


class TurnRequest(BaseModel):
    text: str
    language: str = "ko"


class HowDoISayRequest(BaseModel):
    english: str
    language: str = "ko"


class TranslateRequest(BaseModel):
    text: str
    language: str = "ko"


class RomanizeRequest(BaseModel):
    text: str
    language: str = "ko"


class LangBody(BaseModel):
    language: str = "ko"


class SessionFeedbackRequest(BaseModel):
    kind: str                              # too_easy | too_hard


class TargetWordsRequest(BaseModel):
    words: list[SeedWord]


class CorrectionFeedbackRequest(BaseModel):
    turn_id: int | None = None
    verdict: str                           # fine | wrong


# --------------------------------------------------------------------------
# Routes: onboarding
# --------------------------------------------------------------------------

@router.get("/profile")
def get_profile(db: Session = Depends(get_db)):
    _ensure_tables()
    return {"profile": _profile_dict(_get_profile_row(db))}


@router.post("/onboarding")
def save_onboarding(req: OnboardingRequest, db: Session = Depends(get_db)):
    _ensure_tables()
    row = _get_profile_row(db)
    if row is None:
        row = m.CV2Profile(user_key=USER_KEY)
        db.add(row)
    row.level = req.level
    row.reason = req.reason
    row.english_support = req.english_support
    db.commit()
    db.refresh(row)
    return {"profile": _profile_dict(row)}


@router.get("/videos")
def list_videos():
    return {"videos": _MOCK_VIDEOS}


@router.get("/mixed-sources")
def get_mixed_sources(
    language: str = Query("ko"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview the exact tracked videos that can seed the next mixed chat."""
    candidates = _mixed_candidates_for_user(db, current_user, language)
    return {
        "word_count": len(candidates),
        "max_words": MIXED_WORD_LIMIT,
        "videos": _mixed_source_videos(candidates),
    }


# --------------------------------------------------------------------------
# Routes: session + chat
# --------------------------------------------------------------------------

@router.post("/session")
def create_session(
    req: SessionRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    try:
        _ensure_tables()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database setup failed: {str(e)}")
    prow = _get_profile_row(db)
    # Voice Chat skips onboarding, so a profile may not exist yet — fall back to
    # sensible defaults rather than blocking the session.
    profile = _profile_dict(prow) or {
        "level": "beginner",
        "reason": "general",
        "english_support": "some",
    }
    due_words = _due_words_for(profile["reason"])
    source_videos: list[dict] = []

    seed = {"type": req.seed_type}
    seed_label = None
    seed_video_id = None
    if req.seed_type == "due_words":
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication is required for a mixed chat")
        candidates = _mixed_candidates_for_user(db, current_user, req.language)
        due_words = _materialize_mixed_words(candidates, req.language)
        if not due_words:
            raise HTTPException(
                status_code=422,
                detail="No usable words found in your tracked videos yet. Watch a captioned video, then try again.",
            )
        source_ids = {word["source_video_id"] for word in due_words}
        source_videos = _mixed_source_videos(
            [candidate for candidate in candidates if candidate["video_id"] in source_ids]
        )
    elif req.seed_type == "video":
        # The frontend now sends the real words extracted from the chosen video.
        # Use those as the session's target words (woven into the chat + tracked),
        # and the video title as the seed label. Fall back to the mock gallery
        # only when no explicit words/title were provided (legacy callers).
        title = (req.seed_label or "").strip()
        # Use the video's real words; an empty list is fine (better than injecting
        # wrong-language themed defaults for ko/uk).
        due_words = [
            {"lemma": w.lemma, "gloss": w.gloss}
            for w in (req.seed_words or [])
            if (w.lemma or "").strip()
        ][:8]
        if not title:
            video = next((v for v in _MOCK_VIDEOS if v["video_id"] == req.video_id), None)
            if not video:
                raise HTTPException(status_code=404, detail="Unknown video.")
            title = video["title"]
        seed = {"type": "video", "title": title}
        seed_label = title
        seed_video_id = req.video_id
    elif req.seed_type == "topic":
        label = (req.seed_label or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="A topic is required.")
        seed = {"type": "topic", "title": label}
        seed_label = label

    sess = m.CV2Session(
        user_key=USER_KEY,
        user_id=current_user.id if current_user else None,
        seed_type=req.seed_type,
        seed_label=seed_label,
        seed_video_id=seed_video_id,
        level=profile["level"],
        reason=profile["reason"],
        english_support=profile["english_support"],
        due_words_json=json.dumps(due_words),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    response = {
        "session_id": sess.id,
        "level": profile["level"],
        "due_words": due_words,
        "source_videos": source_videos,
    }
    if req.stream_opening:
        return response

    lemmas = [w["lemma"] for w in due_words]
    opening = svc.generate_opening(profile, lemmas, seed, req.language)

    turn = m.CV2Turn(
        session_id=sess.id,
        idx=0,
        role="assistant",
        text=opening["reply"],
        meta_json=json.dumps({"reply_translation": opening["reply_translation"]}),
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)

    return {
        **response,
        "opening": {
            "turn_id": turn.id,
            "reply": opening["reply"],
            "reply_translation": opening["reply_translation"],
        },
    }


@router.post("/session/{session_id}/opening/stream")
def stream_opening(
    session_id: int,
    body: LangBody,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Generate and persist a new conversation's greeting as SSE chunks."""
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)

    existing_opening = (
        db.query(m.CV2Turn)
        .filter(m.CV2Turn.session_id == sess.id, m.CV2Turn.idx == 0, m.CV2Turn.role == "assistant")
        .first()
    )
    if existing_opening:
        raise HTTPException(status_code=409, detail="This session already has an opening message")

    profile = {
        "level": sess.level,
        "reason": sess.reason,
        "english_support": sess.english_support,
    }
    due_words = [word.get("lemma", "") for word in json.loads(sess.due_words_json or "[]")]
    seed = {"type": sess.seed_type}
    if sess.seed_label:
        seed["title"] = sess.seed_label

    def event_stream():
        try:
            result = None
            for kind, payload in svc.generate_opening_stream(profile, due_words, seed, body.language):
                if kind == "chunk":
                    yield f"data: {json.dumps({'type': 'chunk', 'text': payload})}\n\n"
                else:
                    result = payload
        except Exception as error:
            yield f"data: {json.dumps({'type': 'error', 'message': str(error)})}\n\n"
            return

        if result is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'No opening message generated'})}\n\n"
            return

        turn = m.CV2Turn(
            session_id=sess.id,
            idx=0,
            role="assistant",
            text=result["reply"],
            meta_json=json.dumps({"reply_translation": result["reply_translation"]}),
        )
        db.add(turn)
        db.commit()
        db.refresh(turn)
        yield f"data: {json.dumps({'type': 'done', 'turn_id': turn.id, **result})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _load_session(db: Session, session_id: int) -> m.CV2Session:
    sess = db.query(m.CV2Session).filter(m.CV2Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    return sess


def _load_owned_session(db: Session, session_id: int, current_user: Optional[User]) -> m.CV2Session:
    """Same as _load_session, but 404s (not 403 — no confirming a session ID
    exists to someone who doesn't own it) if the session belongs to a
    different signed-in user. Sessions created before per-user scoping have
    user_id=None and stay reachable by anyone, matching their pre-existing
    (unscoped) behavior rather than orphaning them outright."""
    sess = _load_session(db, session_id)
    if sess.user_id is not None and (current_user is None or sess.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return sess


def _turn_dict(row: m.CV2Turn) -> dict:
    meta = json.loads(row.meta_json) if row.meta_json else {}
    return {
        "turn_id": row.id,
        "role": row.role,
        "text": row.text,
        "reply_translation": meta.get("reply_translation"),
        "correction": meta.get("correction"),
        "used_target_words": meta.get("used_target_words", []),
        "suggested_replies": meta.get("suggested_replies", []),
    }


@router.get("/sessions/recent")
def recent_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Most recent session belonging to the signed-in user, for a Resume card.

    Only sessions created after user scoping was added (see CV2Session.user_id)
    are resumable — sessions from before that point have no owner on record.
    """
    _ensure_tables()
    sess = (
        db.query(m.CV2Session)
        .filter(m.CV2Session.user_id == current_user.id)
        .order_by(m.CV2Session.started_at.desc())
        .first()
    )
    if not sess:
        return {"session": None}

    turn_count = db.query(m.CV2Turn).filter(m.CV2Turn.session_id == sess.id).count()
    last_turn = (
        db.query(m.CV2Turn)
        .filter(m.CV2Turn.session_id == sess.id)
        .order_by(m.CV2Turn.idx.desc())
        .first()
    )
    return {
        "session": {
            "session_id": sess.id,
            "seed_type": sess.seed_type,
            "seed_label": sess.seed_label,
            "seed_video_id": sess.seed_video_id,
            "started_at": sess.started_at.isoformat(),
            "turn_count": turn_count,
            "last_line": last_turn.text if last_turn else "",
            # Mixed sessions have no single seed_video_id — this lets the
            # Resume card show a thumbnail stack of the videos it drew from.
            "due_words": json.loads(sess.due_words_json or "[]"),
        }
    }


@router.get("/session/{session_id}/resume")
def resume_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full turn history for a session, so the frontend can rehydrate the chat
    exactly where it left off. 404s (not 403) on someone else's session, same
    as a session that doesn't exist, so this can't be used to probe IDs."""
    _ensure_tables()
    sess = _load_session(db, session_id)
    if sess.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found.")

    rows = (
        db.query(m.CV2Turn)
        .filter(m.CV2Turn.session_id == session_id)
        .order_by(m.CV2Turn.idx.asc())
        .all()
    )
    return {
        "session_id": sess.id,
        "level": sess.level,
        "seed_label": sess.seed_label,
        "seed_video_id": sess.seed_video_id,
        "due_words": json.loads(sess.due_words_json or "[]"),
        "turns": [_turn_dict(r) for r in rows],
    }


def _history(db: Session, session_id: int) -> list[dict]:
    rows = (
        db.query(m.CV2Turn)
        .filter(m.CV2Turn.session_id == session_id)
        .order_by(m.CV2Turn.idx.asc())
        .all()
    )
    return [{"role": r.role, "text": r.text} for r in rows]


def _next_idx(db: Session, session_id: int) -> int:
    rows = db.query(m.CV2Turn).filter(m.CV2Turn.session_id == session_id).all()
    return len(rows)


@router.post("/session/{session_id}/turn")
def chat_turn(
    session_id: int,
    req: TurnRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    history = _history(db, session_id)

    # Persist the user's turn.
    user_turn = m.CV2Turn(session_id=session_id, idx=_next_idx(db, session_id), role="user", text=req.text)
    db.add(user_turn)
    db.commit()

    result = svc.generate_turn(profile, due_words, history, req.text, req.language)

    assistant_turn = m.CV2Turn(
        session_id=session_id,
        idx=_next_idx(db, session_id),
        role="assistant",
        text=result["reply"],
        meta_json=json.dumps(
            {
                "reply_translation": result["reply_translation"],
                "correction": result["correction"],
                "used_target_words": result["used_target_words"],
                "suggested_replies": result["suggested_replies"],
                "detected_language": result["detected_language"],
            }
        ),
    )
    db.add(assistant_turn)
    db.commit()
    db.refresh(assistant_turn)

    return {"turn_id": assistant_turn.id, **result}


@router.post("/session/{session_id}/turn/stream")
def chat_turn_stream(
    session_id: int,
    req: TurnRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Same as chat_turn, but streams the reply as Server-Sent Events instead
    of waiting for the full response:
      data: {"type": "chunk", "text": "..."}   (repeated as text arrives)
      data: {"type": "done", "turn_id": ..., "reply": ..., ...}  (final, full result)
    """
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    history = _history(db, session_id)

    user_turn = m.CV2Turn(session_id=session_id, idx=_next_idx(db, session_id), role="user", text=req.text)
    db.add(user_turn)
    db.commit()

    def event_stream():
        result: Optional[dict] = None
        try:
            for kind, payload in svc.generate_turn_stream(profile, due_words, history, req.text, req.language):
                if kind == "chunk":
                    yield f"data: {json.dumps({'type': 'chunk', 'text': payload})}\n\n"
                else:
                    result = payload
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        if result is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'No reply generated'})}\n\n"
            return

        assistant_turn = m.CV2Turn(
            session_id=session_id,
            idx=_next_idx(db, session_id),
            role="assistant",
            text=result["reply"],
            meta_json=json.dumps(
                {
                    "reply_translation": result["reply_translation"],
                    "correction": result["correction"],
                    "used_target_words": result["used_target_words"],
                    "suggested_replies": result["suggested_replies"],
                    "detected_language": result["detected_language"],
                }
            ),
        )
        db.add(assistant_turn)
        db.commit()
        db.refresh(assistant_turn)

        yield f"data: {json.dumps({'type': 'done', 'turn_id': assistant_turn.id, **result})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/session/{session_id}/regenerate")
def regenerate(
    session_id: int,
    req: LangBody,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Produce a different assistant reply to the most recent learner turn,
    replacing the last assistant message in place."""
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]

    turns = (
        db.query(m.CV2Turn).filter(m.CV2Turn.session_id == session_id).order_by(m.CV2Turn.idx.asc()).all()
    )
    last_asst = next((t for t in reversed(turns) if t.role == "assistant"), None)
    if last_asst is None:
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")
    prior = [t for t in turns if t.idx < last_asst.idx]
    last_user = next((t for t in reversed(prior) if t.role == "user"), None)
    if last_user is None:
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")
    history = [{"role": t.role, "text": t.text} for t in prior if t.idx < last_user.idx]

    result = svc.generate_turn(profile, due_words, history, last_user.text, req.language)
    last_asst.text = result["reply"]
    last_asst.meta_json = json.dumps(
        {
            "reply_translation": result["reply_translation"],
            "correction": result["correction"],
            "used_target_words": result["used_target_words"],
            "suggested_replies": result["suggested_replies"],
            "detected_language": result["detected_language"],
        }
    )
    db.commit()
    db.refresh(last_asst)
    return {"turn_id": last_asst.id, **result}


@router.post("/session/{session_id}/suggest")
def suggest(
    session_id: int,
    req: LangBody,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Suggest a few things the learner could say next."""
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.generate_suggestions(profile, due_words, _history(db, session_id), req.language)


@router.post("/session/{session_id}/coach")
def coach(
    session_id: int,
    req: HowDoISayRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """The learner replied in English — return the corrected target-language
    message + explanation + advanced grammar feedback (for the right panel)."""
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.coach_english(profile, due_words, _history(db, session_id), req.english, req.language)


@router.post("/session/{session_id}/hint")
def hint(
    session_id: int,
    language: str = "ko",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.generate_hint(profile, due_words, _history(db, session_id), language)


@router.post("/session/{session_id}/how-do-i-say")
def how_do_i_say(
    session_id: int,
    req: HowDoISayRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.how_do_i_say(profile, due_words, req.english, req.language)


@router.post("/translate")
def translate(req: TranslateRequest):
    return {"translation": svc.translate_to_english(req.text, req.language)}


@router.post("/romanize")
def romanize(req: RomanizeRequest):
    return {"romanized": svc.romanize(req.text, req.language)}


# --------------------------------------------------------------------------
# Routes: feedback (Phase 3)
# --------------------------------------------------------------------------

@router.post("/session/{session_id}/session-feedback")
def session_feedback(
    session_id: int,
    req: SessionFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    sess.difficulty_nudge = (sess.difficulty_nudge or 0) + (1 if req.kind == "too_easy" else -1)
    db.add(m.CV2Feedback(session_id=session_id, kind=req.kind))
    db.commit()
    return {"ok": True, "difficulty_nudge": sess.difficulty_nudge}


@router.post("/session/{session_id}/target-words")
def set_target_words(
    session_id: int,
    req: TargetWordsRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Attach the resolved target words to a session after it's already
    started. The video-seed opening line only needs the video's title, so
    the frontend shows the greeting immediately and fills these in once the
    slower, translation-heavy word lookup finishes in the background."""
    _ensure_tables()
    sess = _load_owned_session(db, session_id, current_user)
    sess.due_words_json = json.dumps([{"lemma": w.lemma, "gloss": w.gloss} for w in req.words])
    db.commit()
    return {"ok": True}


@router.post("/correction-feedback")
def correction_feedback(req: CorrectionFeedbackRequest, db: Session = Depends(get_db)):
    _ensure_tables()
    db.add(m.CV2Feedback(session_id=0, turn_id=req.turn_id, kind=req.verdict, note="correction"))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Voice: Gemini Live WebSocket relay (sandboxed — no JWT, prototype user only)
# --------------------------------------------------------------------------

_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
_LIVE_VOICE = "Aoede"

_live_client: Optional[genai.Client] = None


@router.get("/turn/{turn_id}/audio")
def get_turn_audio(
    turn_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Re-synthesize an assistant turn's text with the same Gemini voice used
    in the live conversation (_LIVE_VOICE), so "Listen" plays back in the
    voice the tutor actually spoke in rather than a mismatched browser voice."""
    turn = db.query(m.CV2Turn).filter(m.CV2Turn.id == turn_id).first()
    if not turn:
        raise HTTPException(status_code=404, detail="Turn not found")
    if turn.role != "assistant" or not turn.text:
        raise HTTPException(status_code=400, detail="Only assistant turns have audio")

    _load_owned_session(db, turn.session_id, current_user)  # 404s if not owned

    try:
        audio_bytes = synthesize_tts(turn.text, voice_name=_LIVE_VOICE)
    except Exception as e:
        print(f"[converse2] TTS failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed")

    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS returned no audio")

    return Response(content=_wrap_pcm_as_wav(audio_bytes), media_type="audio/wav")


def _get_live_client() -> genai.Client:
    global _live_client
    if _live_client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        _live_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _live_client


def _voice_system_instruction(profile: dict, due_words: list[str], seed_label: Optional[str], language: str = "ko") -> str:
    """Adapt the cv2 text persona for the voice channel: drop JSON instructions,
    add voice-specific brevity / pacing hints."""
    lang_name = svc._lang(language)["name"]
    base = svc._persona(profile, due_words, language)
    voice_addendum = (
        "\n\nYOU ARE SPEAKING ALOUD — NOT TYPING."
        f"\n- Speak {lang_name}."
        "\n- Keep each spoken turn to 1-2 short sentences."
        "\n- Speak naturally. Pause briefly after questions so the learner can answer."
        "\n- Never say things like 'in this prompt', 'as an AI', or read out JSON."
    )
    if seed_label:
        voice_addendum += f"\n- Conversation seed (topic to lean into): {seed_label}."
    return base + voice_addendum


@router.websocket("/voice/ws")
async def voice_ws(
    websocket: WebSocket,
    session_id: int = Query(..., description="cv2 session id from POST /session"),
    language: str = Query("ko", description="target language: uk | ko"),
):
    """Browser ↔ Gemini Live relay for a cv2 sandbox session.

    Wire format mirrors the production chat_voice route:
      client → server: binary PCM @ 16 kHz, or JSON {"event": "end"}
      server → client: binary PCM @ 24 kHz, or JSON events
        ready / user_transcript / assistant_transcript / interrupted /
        turn_complete / turn_persisted / error
    """
    await websocket.accept()
    db: Session = SessionLocal()
    try:
        _ensure_tables()
        sess = db.query(m.CV2Session).filter(m.CV2Session.id == session_id).first()
        if not sess:
            await websocket.send_json({"event": "error", "message": "Session not found"})
            await websocket.close(code=1008)
            return

        profile = {
            "level": sess.level,
            "reason": sess.reason,
            "english_support": sess.english_support,
        }
        due_words = [w.get("lemma", "") for w in json.loads(sess.due_words_json or "[]")]
        system_instruction = _voice_system_instruction(profile, due_words, sess.seed_label, language)

        live_config = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": _LIVE_VOICE},
                },
            },
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

        # Per-turn transcript buffers. Flushed to cv2_turn rows on turn_complete,
        # or discarded (assistant only) on interrupted.
        user_buf: list[str] = []
        asst_buf: list[str] = []

        def _persist_turn(role: str, text: str) -> Optional[int]:
            text = (text or "").strip()
            if not text:
                return None
            rows = db.query(m.CV2Turn).filter(m.CV2Turn.session_id == sess.id).all()
            turn = m.CV2Turn(
                session_id=sess.id,
                idx=len(rows),
                role=role,
                text=text,
                meta_json=json.dumps({"modality": "voice"}),
            )
            db.add(turn)
            db.commit()
            db.refresh(turn)
            return turn.id

        client = _get_live_client()

        async with client.aio.live.connect(model=_LIVE_MODEL, config=live_config) as live:
            await websocket.send_json({"event": "ready"})

            async def pump_in():
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    data = msg.get("bytes")
                    if data:
                        await live.send_realtime_input(
                            audio=gtypes.Blob(
                                data=data,
                                mime_type="audio/pcm;rate=16000",
                            )
                        )
                        continue
                    text = msg.get("text")
                    if text:
                        try:
                            ctrl = json.loads(text)
                        except Exception:
                            continue
                        if ctrl.get("event") == "end":
                            return
                        if ctrl.get("event") == "text_turn":
                            # Optional path: type instead of speak. Persisted as
                            # a user turn and forwarded into Gemini as a text input.
                            t = (ctrl.get("text") or "").strip()
                            if t:
                                _persist_turn("user", t)
                                await live.send_client_content(
                                    turns={"role": "user", "parts": [{"text": t}]},
                                    turn_complete=True,
                                )

            async def pump_out():
                nonlocal user_buf, asst_buf
                async for response in live.receive():
                    audio = getattr(response, "data", None)
                    if audio:
                        await websocket.send_bytes(audio)

                    sc = getattr(response, "server_content", None)
                    if not sc:
                        continue

                    inp_t = getattr(sc, "input_transcription", None)
                    if inp_t and getattr(inp_t, "text", None):
                        user_buf.append(inp_t.text)
                        await websocket.send_json({"event": "user_transcript", "text": inp_t.text})

                    out_t = getattr(sc, "output_transcription", None)
                    if out_t and getattr(out_t, "text", None):
                        asst_buf.append(out_t.text)
                        await websocket.send_json({"event": "assistant_transcript", "text": out_t.text})

                    if getattr(sc, "interrupted", False):
                        # Drop the assistant's partial turn — the learner barged in.
                        asst_buf = []
                        await websocket.send_json({"event": "interrupted"})

                    if getattr(sc, "turn_complete", False):
                        user_text = "".join(user_buf).strip()
                        asst_text = "".join(asst_buf).strip()
                        user_buf = []
                        asst_buf = []
                        user_id = _persist_turn("user", user_text) if user_text else None
                        asst_id = _persist_turn("assistant", asst_text) if asst_text else None
                        await websocket.send_json({
                            "event": "turn_complete",
                            "user_turn_id": user_id,
                            "assistant_turn_id": asst_id,
                            "user_text": user_text,
                            "assistant_text": asst_text,
                        })

            await asyncio.gather(pump_in(), pump_out(), return_exceptions=True)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        db.close()
        try:
            await websocket.close()
        except Exception:
            pass
