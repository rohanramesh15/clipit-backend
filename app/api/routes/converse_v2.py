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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from google import genai
from google.genai import types as gtypes

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, get_db
from app.models import converse_v2 as m
from app.services import converse_v2_service as svc

router = APIRouter()

USER_KEY = "prototype"

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


# --------------------------------------------------------------------------
# Routes: session + chat
# --------------------------------------------------------------------------

@router.post("/session")
def create_session(req: SessionRequest, db: Session = Depends(get_db)):
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

    seed = {"type": req.seed_type}
    seed_label = None
    seed_video_id = None
    if req.seed_type == "video":
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
        "session_id": sess.id,
        "level": profile["level"],
        "due_words": due_words,
        "opening": {
            "turn_id": turn.id,
            "reply": opening["reply"],
            "reply_translation": opening["reply_translation"],
        },
    }


def _load_session(db: Session, session_id: int) -> m.CV2Session:
    sess = db.query(m.CV2Session).filter(m.CV2Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    return sess


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
def chat_turn(session_id: int, req: TurnRequest, db: Session = Depends(get_db)):
    _ensure_tables()
    sess = _load_session(db, session_id)
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


@router.post("/session/{session_id}/regenerate")
def regenerate(session_id: int, req: LangBody, db: Session = Depends(get_db)):
    """Produce a different assistant reply to the most recent learner turn,
    replacing the last assistant message in place."""
    _ensure_tables()
    sess = _load_session(db, session_id)
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
def suggest(session_id: int, req: LangBody, db: Session = Depends(get_db)):
    """Suggest a few things the learner could say next."""
    _ensure_tables()
    sess = _load_session(db, session_id)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.generate_suggestions(profile, due_words, _history(db, session_id), req.language)


@router.post("/session/{session_id}/coach")
def coach(session_id: int, req: HowDoISayRequest, db: Session = Depends(get_db)):
    """The learner replied in English — return the corrected target-language
    message + explanation + advanced grammar feedback (for the right panel)."""
    _ensure_tables()
    sess = _load_session(db, session_id)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.coach_english(profile, due_words, _history(db, session_id), req.english, req.language)


@router.post("/session/{session_id}/hint")
def hint(session_id: int, language: str = "ko", db: Session = Depends(get_db)):
    _ensure_tables()
    sess = _load_session(db, session_id)
    profile = {"level": sess.level, "reason": sess.reason, "english_support": sess.english_support}
    due_words = [w["lemma"] for w in json.loads(sess.due_words_json or "[]")]
    return svc.generate_hint(profile, due_words, _history(db, session_id), language)


@router.post("/session/{session_id}/how-do-i-say")
def how_do_i_say(session_id: int, req: HowDoISayRequest, db: Session = Depends(get_db)):
    _ensure_tables()
    sess = _load_session(db, session_id)
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
def session_feedback(session_id: int, req: SessionFeedbackRequest, db: Session = Depends(get_db)):
    _ensure_tables()
    sess = _load_session(db, session_id)
    sess.difficulty_nudge = (sess.difficulty_nudge or 0) + (1 if req.kind == "too_easy" else -1)
    db.add(m.CV2Feedback(session_id=session_id, kind=req.kind))
    db.commit()
    return {"ok": True, "difficulty_nudge": sess.difficulty_nudge}


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
