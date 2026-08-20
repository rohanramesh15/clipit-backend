"""
Chat routes — Spanish-only voice/text conversation.

Endpoints:
  GET  /chat/suggestions          — starter chips for the opening screen
  POST /chat/session              — create a new conversation
  POST /chat/{id}/turn            — stream one turn (SSE) — text or audio input
  POST /chat/{id}/end             — finalize and run rubric
  POST /chat/save-word            — chip click → save word to FSRS deck
  GET  /chat/sessions             — list past sessions (Phase 2; basic now)
"""

import json
import time
from datetime import datetime
from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Body, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.chat import ChatSession, ChatTurn, ChatSavedWord
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models.video import TrackedVideo

from app.services.vocab_profile_service import (
    build_profile,
    cefr_label,
    invalidate as invalidate_profile,
)
from app.services.chat_orchestrator import (
    stream_turn,
    audit_reply_lemmas,
)
from app.services.gemini_chat_service import (
    score_session,
    transcribe_audio,
    synthesize_tts,
    translate_text,
)
from app.services.suggestion_service import (
    build_suggestions,
    suggestions_to_dicts,
)
from app.services.memory_service import (
    extract_facts,
    store_facts,
    retrieve_facts,
)
from app.services.adaptation_service import compute_adaptation
from app.services.proficiency_service import (
    refresh_recognition_score,
    update_production_score,
    get_scores,
)
from app.services.lemmatizer import lemmatize_word, filter_content_lemmas, lemmatize


# Languages where the conversation feature is enabled. Korean/Ukrainian users
# still see the "coming soon" message until those are added.
SUPPORTED_CHAT_LANGUAGES = {"es", "en"}


def _supported_lang(lang: Optional[str]) -> str:
    return lang if lang in SUPPORTED_CHAT_LANGUAGES else "es"

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    seed_type: str = "free"               # 'video' | 'scenario' | 'srs' | 'wildcard' | 'free'
    seed_id: Optional[str] = None         # scenario id or wildcard id
    seed_video_id: Optional[str] = None   # if seed_type == 'video'
    seed_label: Optional[str] = None      # display label that was tapped
    level: Optional[str] = None           # 'A1' | 'A2' | 'B1' override
    mode: Optional[str] = "free"          # 'free' | 'debate' | 'interview' | 'roleplay' | 'shadowing'
    language: Optional[str] = "es"        # 'es' | 'en' — target language for the chat


class TurnRequest(BaseModel):
    text: str
    level: Optional[str] = None           # mid-session override


class SaveWordRequest(BaseModel):
    session_id: int
    turn_id: Optional[int] = None
    lemma: str
    surface_form: Optional[str] = None
    sentence: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _level_for_session(profile_level_signal: float, override: Optional[str],
                       production_score: Optional[float] = None,
                       session_count: int = 0) -> str:
    """
    Resolve the level for a new chat session.

    Precedence:
      1. Explicit override (user picked A1/A2/B1 on the level dial)
      2. Production score (once the user has ≥3 chats — it's the more accurate
         signal for what they can actually *produce*, vs recognition-only)
      3. Recognition score from FSRS (the default for new users)
    """
    if override and override.upper() in ("A1", "A2", "B1", "B2", "C1", "C2"):
        return override.upper()
    if production_score is not None and session_count >= 3:
        return cefr_label(production_score)
    return cefr_label(profile_level_signal)


def _video_title(db: Session, video_id: Optional[str]) -> Optional[str]:
    if not video_id:
        return None
    v = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
    return v.title if v else None


def _sse(event: str, payload: dict) -> str:
    """Format an SSE message."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/chat/suggestions")
def get_suggestions(
    motivation: Optional[str] = Query(None),
    language: str = Query("es"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return 3-4 starter chips for the opening screen."""
    lang = _supported_lang(language)
    sugs = build_suggestions(db, current_user.id, motivation=motivation, language=lang)
    return {"suggestions": suggestions_to_dicts(sugs)}


@router.post("/chat/session")
def create_session(
    body: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new conversation session. Returns the session_id and resolved level."""
    lang = _supported_lang(body.language)
    profile = build_profile(db, current_user.id, lang)
    scores = get_scores(db, current_user.id, lang)
    level = _level_for_session(
        profile.level_signal,
        body.level,
        production_score=scores.get("production_score"),
        session_count=scores.get("session_count", 0),
    )

    session = ChatSession(
        user_id=current_user.id,
        language=lang,
        seed_type=body.seed_type or "free",
        seed_video_id=body.seed_video_id,
        seed_label=body.seed_label,
        mode=(body.mode or "free").lower(),
        level_used=level,
        started_at=datetime.utcnow(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return {
        "session_id": session.id,
        "level": level,
        "seed_type": session.seed_type,
        "seed_label": session.seed_label,
        "seed_video_id": session.seed_video_id,
        "mode": session.mode,
        "started_at": session.started_at.isoformat() + "Z",
    }


@router.post("/chat/{session_id}/turn")
async def chat_turn(
    session_id: int,
    text: Optional[str] = Form(None),
    level: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream one chat turn via Server-Sent Events.

    Body is multipart/form-data so we accept either:
      - text: the user's message (text path)
      - audio: an audio blob (voice path; transcribed to text via Gemini)

    SSE events emitted (in order):
      - turn_start: { user_text, turn_idx }
      - chunk: { text } (multiple)
      - turn_end: { off_list_lemmas: [...], assistant_text, latency_ms }
    """
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.ended_at:
        raise HTTPException(status_code=400, detail="Session already ended")

    # Resolve user text (transcribe if audio)
    user_text = (text or "").strip()
    if audio is not None:
        audio_bytes = await audio.read()
        try:
            user_text = transcribe_audio(
                audio_bytes,
                mime_type=audio.content_type or "audio/webm",
                language=session.language or "es",
            )
        except Exception as e:
            print(f"[chat] STT failed: {e}")
            raise HTTPException(status_code=500, detail="Transcription failed")
    if not user_text:
        raise HTTPException(status_code=400, detail="Empty user input")

    # Effective level for this turn (mid-session adaptation runs below)
    session_lang = session.language or "es"
    profile = build_profile(db, current_user.id, session_lang)
    scores = get_scores(db, current_user.id, session_lang)
    effective_level = _level_for_session(
        profile.level_signal,
        level or session.level_used,
        production_score=scores.get("production_score"),
        session_count=scores.get("session_count", 0),
    )
    # Persist updated level on the session if it changed
    if effective_level != session.level_used:
        session.level_used = effective_level
        db.commit()

    # Build history from prior turns
    prior_turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == session.id)
        .order_by(ChatTurn.idx.asc())
        .all()
    )
    history = [{"role": t.role, "text": t.text} for t in prior_turns]
    next_idx = (prior_turns[-1].idx + 1) if prior_turns else 0

    # Persist the user turn BEFORE streaming so a mid-stream failure doesn't lose it
    user_lemmas = filter_content_lemmas(lemmatize(user_text, session_lang), session_lang)
    user_turn = ChatTurn(
        session_id=session.id,
        idx=next_idx,
        role="user",
        text=user_text,
        lemmas_json=user_lemmas,
    )
    db.add(user_turn)
    db.commit()
    db.refresh(user_turn)

    seed_video_title = _video_title(db, session.seed_video_id)

    def event_stream():
        # Open with the transcribed user text so the frontend can display it
        # (especially important on the voice path)
        yield _sse("turn_start", {
            "user_text": user_text,
            "user_turn_id": user_turn.id,
            "turn_idx": next_idx + 1,
        })

        chunks: list[str] = []
        t0 = time.time()
        usage: dict = {}
        # Retrieve relevant memory facts from past sessions
        memory_facts: list[str] = []
        try:
            from app.services.memory_service import retrieve_facts
            memory_facts = retrieve_facts(db, current_user.id, session_lang, user_text, top_k=4)
        except Exception as e:
            print(f"[chat] memory retrieval failed: {e}")

        # Mid-session adaptation: look at the last 3 user turns (including the
        # one we just received) and decide whether to nudge difficulty up/down.
        last_user_turns: list[str] = [user_text]
        for t in reversed(prior_turns):
            if t.role == "user":
                last_user_turns.append(t.text)
                if len(last_user_turns) >= 3:
                    break
        try:
            _, adaptation_hint = compute_adaptation(last_user_turns, profile, language=session_lang)
        except Exception as e:
            print(f"[chat] adaptation failed: {e}")
            adaptation_hint = None

        try:
            for chunk in stream_turn(
                db=db,
                profile=profile,
                level=effective_level,
                history=history,
                user_message=user_text,
                seed_label=session.seed_label,
                seed_video_id=session.seed_video_id,
                seed_video_title=seed_video_title,
                memory_facts=memory_facts,
                adaptation_hint=adaptation_hint,
                mode=session.mode,
                usage_out=usage,
            ):
                if chunk:
                    chunks.append(chunk)
                    yield _sse("chunk", {"text": chunk})
        except Exception as e:
            print(f"[chat] stream failed: {e}")
            yield _sse("error", {"message": "AI reply failed. Try again."})
            return

        full_text = "".join(chunks).strip()
        latency_ms = int((time.time() - t0) * 1000)

        # Lemma audit for chip overlay
        off_list = audit_reply_lemmas(full_text, profile) if full_text else []
        reply_lemmas = filter_content_lemmas(lemmatize(full_text, session_lang), session_lang) if full_text else []

        # Persist the assistant turn
        asst_turn = ChatTurn(
            session_id=session.id,
            idx=next_idx + 1,
            role="assistant",
            text=full_text,
            lemmas_json=reply_lemmas,
            off_list_lemmas_json=off_list,
            latency_ms=latency_ms,
            tokens_in=usage.get("tokens_in"),
            tokens_out=usage.get("tokens_out"),
        )
        db.add(asst_turn)
        db.commit()
        db.refresh(asst_turn)

        yield _sse("turn_end", {
            "assistant_turn_id": asst_turn.id,
            "assistant_text": full_text,
            "off_list_lemmas": off_list,
            "latency_ms": latency_ms,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/chat/{session_id}/end")
def end_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """End a session and run rubric scoring on the transcript."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.ended_at is None:
        session.ended_at = datetime.utcnow()

    turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == session.id)
        .order_by(ChatTurn.idx.asc())
        .all()
    )

    transcript = "\n".join(
        f"{'You' if t.role == 'user' else 'AI'}: {t.text}" for t in turns
    )

    # Collect off-list lemmas the user encountered (for the summary card)
    encountered = []
    seen = set()
    for t in turns:
        if t.role == "assistant" and t.off_list_lemmas_json:
            for entry in t.off_list_lemmas_json:
                if entry["lemma"] not in seen:
                    seen.add(entry["lemma"])
                    encountered.append(entry)

    session_lang = session.language or "es"
    profile = build_profile(db, current_user.id, session_lang)
    profile_summary = (
        f"Estimated level: {cefr_label(profile.level_signal)}. "
        f"Total cards: {profile.total_cards}. "
        f"Session level: {session.level_used or 'A2'}. "
        f"Language: {session_lang}."
    )

    rubric: dict = {}
    if transcript.strip():
        try:
            rubric = score_session(transcript, profile_summary)
        except Exception as e:
            print(f"[chat] rubric failed: {e}")

    # ── Dual proficiency tracking ─────────────────────────────────────────
    proficiency: dict = {}
    try:
        refresh_recognition_score(db, current_user.id, session_lang)
        update_production_score(db, current_user.id, session_lang, rubric.get("production_level") if rubric else None)
        proficiency = get_scores(db, current_user.id, session_lang)
    except Exception as e:
        print(f"[chat] proficiency update failed: {e}")

    # ── Cross-session memory extraction ───────────────────────────────────
    fact_count = 0
    if transcript.strip() and len(turns) >= 2:
        try:
            facts = extract_facts(transcript, profile_summary)
            if facts:
                fact_count = store_facts(db, current_user.id, session_lang, session.id, facts)
        except Exception as e:
            print(f"[chat] memory extraction failed: {e}")

    summary = {
        "turn_count": len(turns),
        "user_turn_count": sum(1 for t in turns if t.role == "user"),
        "encountered_new_words": encountered,
        "rubric": rubric,
        "proficiency": proficiency,
        "facts_stored": fact_count,
    }
    session.summary_json = summary
    db.commit()

    return {
        "session_id": session.id,
        "ended_at": session.ended_at.isoformat() + "Z",
        "summary": summary,
    }


class TranslateRequest(BaseModel):
    text: str
    source: Optional[str] = None  # 'es' / 'en' / etc — defaults to 'es'
    target: Optional[str] = None  # 'en' / 'es' / etc — defaults to opposite of source


@router.post("/chat/translate")
def translate(
    body: TranslateRequest,
    current_user: User = Depends(get_current_user),
):
    """Translate chat text from source → target via Gemini Flash."""
    source = (body.source or "es").lower()
    target = (body.target or ("en" if source != "en" else "es")).lower()
    try:
        translation = translate_text(body.text, source=source, target=target)
    except Exception as e:
        print(f"[chat] translate failed: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")
    return {"text": body.text, "translation": translation, "source": source, "target": target}


@router.get("/chat/proficiency")
def chat_proficiency(
    language: str = Query("es"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the user's current dual proficiency scores for the given language."""
    lang = _supported_lang(language)
    return get_scores(db, current_user.id, lang)


@router.post("/chat/save-word")
def save_word(
    body: SaveWordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a clicked chip word as a flashcard in the user's FSRS deck.
    Idempotent: if a card already exists for this (user, lemma, 'es'), we just
    link it; otherwise we create a new card in 'tts' mode with the sentence as
    the example.
    """
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == body.session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    lemma = (body.lemma or "").strip().lower()
    if not lemma:
        raise HTTPException(status_code=400, detail="Empty lemma")

    session_lang = session.language or "es"

    # Check existing card
    card = (
        db.query(UserFlashcardProgress)
        .filter(
            UserFlashcardProgress.user_id == current_user.id,
            UserFlashcardProgress.language == session_lang,
            (UserFlashcardProgress.lemma == lemma) | (UserFlashcardProgress.word == lemma),
        )
        .first()
    )
    if not card:
        # Create a new card in "new" state — frontend's FSRS will progress it on first review
        card = UserFlashcardProgress(
            user_id=current_user.id,
            word=body.surface_form or lemma,
            lemma=lemma,
            language=session_lang,
            card_type="tts",
            due=datetime.utcnow(),
            stability=0.0,
            difficulty=0.0,
            elapsed_days=0,
            scheduled_days=0,
            reps=0,
            lapses=0,
            state=0,
        )
        db.add(card)
        db.flush()

    saved = ChatSavedWord(
        session_id=session.id,
        turn_id=body.turn_id,
        user_id=current_user.id,
        lemma=lemma,
        surface_form=body.surface_form,
        sentence=body.sentence,
        fsrs_card_id=card.id,
    )
    db.add(saved)
    db.commit()

    # Invalidate the cached vocab profile so future turns see the new card
    invalidate_profile(current_user.id, session_lang)

    return {
        "status": "ok",
        "lemma": lemma,
        "card_id": card.id,
        "saved_id": saved.id,
    }


# Gemini TTS voice names. See: https://ai.google.dev/gemini-api/docs/speech-generation
ALLOWED_TTS_VOICES = {"Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"}


@router.get("/chat/voices")
def list_tts_voices(current_user: User = Depends(get_current_user)):
    """List the Gemini TTS voices available to pick from in Settings."""
    return {
        "voices": [
            {"id": "Kore",   "label": "Kore — clear, neutral (default)"},
            {"id": "Puck",   "label": "Puck — bright, energetic"},
            {"id": "Charon", "label": "Charon — deep, calm"},
            {"id": "Aoede",  "label": "Aoede — warm, melodic"},
            {"id": "Fenrir", "label": "Fenrir — strong, direct"},
            {"id": "Leda",   "label": "Leda — soft, friendly"},
            {"id": "Orus",   "label": "Orus — confident"},
            {"id": "Zephyr", "label": "Zephyr — light, breezy"},
        ]
    }


def _wrap_pcm_as_wav(pcm_bytes: bytes) -> bytes:
    """Wrap raw PCM (24kHz, mono, 16-bit — Gemini TTS output) as a WAV so browsers can play it directly."""
    import struct
    sample_rate = 24000
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    wav_header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)              # PCM
        + struct.pack("<H", num_channels)
        + struct.pack("<I", sample_rate)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", block_align)
        + struct.pack("<H", bits_per_sample)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return wav_header + pcm_bytes


# Short greeting read aloud to preview a voice in Settings, in each learning language.
VOICE_SAMPLE_TEXT = {
    "ko": "안녕하세요! 오늘도 함께 연습해봐요.",
    "uk": "Привіт! Сьогодні потренуємося разом.",
}


@lru_cache(maxsize=32)
def _synthesize_voice_sample(voice_name: str, lang: str) -> bytes:
    """Generate a short TTS preview clip for a voice. Cached in-process — there are only
    len(ALLOWED_TTS_VOICES) * len(VOICE_SAMPLE_TEXT) possible inputs, so this never grows."""
    text = VOICE_SAMPLE_TEXT.get(lang, VOICE_SAMPLE_TEXT["ko"])
    return synthesize_tts(text, voice_name=voice_name)


@router.get("/chat/voices/{voice_id}/sample")
def get_voice_sample(
    voice_id: str,
    lang: str = Query("ko"),
    current_user: User = Depends(get_current_user),
):
    """Generate (and cache) a short TTS preview clip for a voice, used in Settings."""
    if voice_id not in ALLOWED_TTS_VOICES:
        raise HTTPException(status_code=404, detail="Unknown voice")

    try:
        audio_bytes = _synthesize_voice_sample(voice_id, lang)
    except Exception as e:
        print(f"[chat] Voice sample TTS failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed")

    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS returned no audio")

    return Response(content=_wrap_pcm_as_wav(audio_bytes), media_type="audio/wav")


@router.get("/chat/audio/{turn_id}")
def get_turn_audio(
    turn_id: int,
    voice: str = Query("Kore"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate (and cache) TTS audio for an assistant turn.
    Returns raw audio bytes (PCM L16, 24kHz, mono — wrap as WAV client-side
    if needed, or use a <audio> element with a Blob URL).
    """
    turn = db.query(ChatTurn).filter(ChatTurn.id == turn_id).first()
    if not turn:
        raise HTTPException(status_code=404, detail="Turn not found")

    # Verify ownership
    session = db.query(ChatSession).filter(ChatSession.id == turn.session_id).first()
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your turn")

    if turn.role != "assistant" or not turn.text:
        raise HTTPException(status_code=400, detail="Only assistant turns have audio")

    voice_name = voice if voice in ALLOWED_TTS_VOICES else "Kore"

    try:
        audio_bytes = synthesize_tts(turn.text, voice_name=voice_name)
    except Exception as e:
        print(f"[chat] TTS failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed")

    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS returned no audio")

    return Response(content=_wrap_pcm_as_wav(audio_bytes), media_type="audio/wav")


