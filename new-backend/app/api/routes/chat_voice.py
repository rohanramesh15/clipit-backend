"""
Gemini Live voice chat — WebSocket relay.

Browser ↔ FastAPI WebSocket ↔ Gemini Live session.

Wire format:
  client → server:
    - binary frames: 16-bit little-endian PCM @ 16 kHz, mono (mic)
    - text frames:   JSON control ({"event": "end"} to hang up)

  server → client:
    - binary frames: 16-bit little-endian PCM @ 24 kHz, mono (Gemini voice)
    - text frames:   JSON events
        {"event": "ready"}
        {"event": "user_transcript",      "text": "..."}
        {"event": "assistant_transcript", "text": "..."}
        {"event": "interrupted"}
        {"event": "turn_complete"}
        {"event": "error", "message": "..."}
"""

import asyncio
import json
import traceback
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from google import genai
from google.genai import types as gtypes

from app.core.config import settings
from app.core.database import SessionLocal
from app.api.deps import get_current_user_from_token
from app.models.chat import ChatSession
from app.models.user import User
from app.models.video import TrackedVideo
from app.services.chat_orchestrator import build_system_instruction
from app.services.memory_service import retrieve_facts
from app.services.vocab_profile_service import build_profile


router = APIRouter()

_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
_DEFAULT_VOICE = "Aoede"  # natural female voice; "Puck"/"Charon" are alternatives


_live_client: Optional[genai.Client] = None


def _get_live_client() -> genai.Client:
    global _live_client
    if _live_client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        _live_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _live_client


def _authenticate(token: str, db: Session) -> Optional[User]:
    try:
        user, _is_new = get_current_user_from_token(token, db)
        return user
    except Exception:
        return None


def _video_title(db: Session, video_id: Optional[str]) -> Optional[str]:
    if not video_id:
        return None
    v = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
    return v.title if v else None


def _build_live_config(system_instruction: str) -> dict:
    """Live API config dict. SDK accepts plain dicts; sticking to that keeps
    us tolerant of small SDK version differences."""
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": _DEFAULT_VOICE},
            },
        },
        "system_instruction": {"parts": [{"text": system_instruction}]},
        # These give us the transcript both directions for the optional
        # frontend transcript drawer + session summary later.
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


@router.websocket("/chat/voice/ws")
async def voice_ws(
    websocket: WebSocket,
    token: str = Query(..., description="Supabase access token"),
    session_id: int = Query(..., description="ChatSession id created via POST /chat/session"),
):
    await websocket.accept()

    db: Session = SessionLocal()
    try:
        user = _authenticate(token, db)
        if not user:
            await websocket.send_json({"event": "error", "message": "Unauthorized"})
            await websocket.close(code=1008)
            return

        chat_session = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user.id)
            .first()
        )
        if not chat_session:
            await websocket.send_json({"event": "error", "message": "Session not found"})
            await websocket.close(code=1008)
            return
        if chat_session.ended_at:
            await websocket.send_json({"event": "error", "message": "Session already ended"})
            await websocket.close(code=1008)
            return

        lang = chat_session.language or "es"
        profile = build_profile(db, user.id, lang)
        memory_facts: list[str] = []
        try:
            memory_facts = retrieve_facts(
                db, user.id, lang, chat_session.seed_label or "", top_k=4,
            )
        except Exception:
            pass

        system_instruction = build_system_instruction(
            profile=profile,
            level=chat_session.level_used or "A2",
            seed_label=chat_session.seed_label,
            seed_video_title=_video_title(db, chat_session.seed_video_id),
            retrieved=[],  # skip subtitle retrieval on the voice path — keep prompt tight
            memory_facts=memory_facts,
            mode=chat_session.mode,
        )

        client = _get_live_client()
        live_config = _build_live_config(system_instruction)

        async with client.aio.live.connect(model=_LIVE_MODEL, config=live_config) as live:
            await websocket.send_json({"event": "ready"})

            async def pump_client_to_gemini():
                """Browser mic → Gemini realtime input."""
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

            async def pump_gemini_to_client():
                """Gemini audio + transcripts → browser."""
                async for response in live.receive():
                    audio = getattr(response, "data", None)
                    if audio:
                        await websocket.send_bytes(audio)

                    sc = getattr(response, "server_content", None)
                    if not sc:
                        continue

                    inp_t = getattr(sc, "input_transcription", None)
                    if inp_t and getattr(inp_t, "text", None):
                        await websocket.send_json({
                            "event": "user_transcript",
                            "text": inp_t.text,
                        })

                    out_t = getattr(sc, "output_transcription", None)
                    if out_t and getattr(out_t, "text", None):
                        await websocket.send_json({
                            "event": "assistant_transcript",
                            "text": out_t.text,
                        })

                    if getattr(sc, "interrupted", False):
                        await websocket.send_json({"event": "interrupted"})

                    if getattr(sc, "turn_complete", False):
                        await websocket.send_json({"event": "turn_complete"})

            await asyncio.gather(
                pump_client_to_gemini(),
                pump_gemini_to_client(),
                return_exceptions=True,
            )

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
