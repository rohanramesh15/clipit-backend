"""
Gemini Flash chat wrapper.

Handles:
  - System-instruction + history prompt assembly
  - Streaming text generation
  - Non-streaming rubric scoring at end-of-session
  - Safety thresholds tuned for language-learning content
"""

from typing import AsyncIterator, Iterator, List, Optional

from google import genai
from google.genai import types as genai_types

from app.core.config import settings

_CHAT_MODEL = "gemini-2.5-flash"
_RUBRIC_MODEL = "gemini-2.5-flash"

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _safety_settings():
    """Permissive safety settings — language learning content can include
    slang, mild violence (from media), etc."""
    return [
        genai_types.SafetySetting(
            category=cat,
            threshold=genai_types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        )
        for cat in (
            genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        )
    ]


def stream_chat(
    system_instruction: str,
    history: List[dict],
    user_message: str,
    usage_out: Optional[dict] = None,
) -> Iterator[str]:
    """
    Stream a Gemini Flash reply, yielding text chunks as they arrive.

    Args:
        system_instruction: The persona + level + vocab rules + retrieved context.
        history: List of {role: 'user'|'assistant', text: str} for prior turns.
        user_message: The latest user message (text path; audio path elsewhere).
        usage_out: Optional dict the caller passes in; we populate it with
            'tokens_in', 'tokens_out', and 'finish_reason' from the final chunk.

    Yields:
        Text chunks (strings) as Gemini streams the reply.
    """
    client = _get_client()

    contents = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part.from_text(text=turn["text"])],
        ))
    contents.append(genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=user_message)],
    ))

    config = genai_types.GenerateContentConfig(
        system_instruction=system_instruction,
        safety_settings=_safety_settings(),
        temperature=0.7,
        # Bumped from 400 → 800 to avoid mid-sentence truncation on longer replies.
        # Most replies are still ~30-80 tokens; we just want headroom.
        max_output_tokens=800,
    )

    response = client.models.generate_content_stream(
        model=_CHAT_MODEL,
        contents=contents,
        config=config,
    )

    last_chunk = None
    for chunk in response:
        last_chunk = chunk
        if chunk.text:
            yield chunk.text

    # Capture usage + finish reason from the final chunk for the caller
    if usage_out is not None and last_chunk is not None:
        try:
            usage = getattr(last_chunk, "usage_metadata", None)
            if usage:
                usage_out["tokens_in"] = int(getattr(usage, "prompt_token_count", 0) or 0)
                usage_out["tokens_out"] = int(getattr(usage, "candidates_token_count", 0) or 0)
            cands = getattr(last_chunk, "candidates", None) or []
            if cands:
                fr = getattr(cands[0], "finish_reason", None)
                if fr is not None:
                    usage_out["finish_reason"] = str(fr)
        except Exception:
            pass


_LANGUAGE_NAMES = {"es": "Spanish", "en": "English", "ko": "Korean", "uk": "Ukrainian"}


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm", language: str = "es") -> str:
    """
    Transcribe target-language speech via Gemini's multimodal input.
    """
    client = _get_client()
    lang_name = _LANGUAGE_NAMES.get(language, "Spanish")

    audio_part = genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    prompt = (
        f"Transcribe the {lang_name} audio above into {lang_name} text. "
        "Return only the transcribed text, nothing else. "
        "If the audio is silent or unclear, return an empty string."
    )

    response = client.models.generate_content(
        model=_CHAT_MODEL,
        contents=[audio_part, prompt],
        config=genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=400,
            safety_settings=_safety_settings(),
        ),
    )
    return (response.text or "").strip()


def synthesize_tts(text: str, voice_name: str = "Kore") -> bytes:
    """
    Generate Spanish TTS audio for the AI's reply.

    Args:
        text: Spanish text to speak.
        voice_name: Gemini TTS voice (e.g. "Kore", "Puck", "Charon").

    Returns:
        Raw audio bytes (PCM L16, 24kHz, mono).
    """
    client = _get_client()

    config = genai_types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                    voice_name=voice_name,
                ),
            ),
        ),
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=text,
        config=config,
    )
    # Audio comes back as inline_data on the first part
    for cand in response.candidates or []:
        for part in cand.content.parts or []:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
    return b""


def translate_to_english(spanish_text: str) -> str:
    """Translate Spanish text → natural English. Used by /chat/translate."""
    return translate_text(spanish_text, source="es", target="en")


def translate_text(text: str, source: str = "es", target: str = "en") -> str:
    """
    Generic Gemini translate. For 'translate-on-tap' in chat: source is the
    user's learning language, target is something they understand (English by
    default). For English-target chats we translate English → Spanish to give
    the user a useful pair; this can be made configurable later.
    """
    if not text or not text.strip():
        return ""
    if source == target:
        return text.strip()
    client = _get_client()
    src_name = _LANGUAGE_NAMES.get(source, "Spanish")
    tgt_name = _LANGUAGE_NAMES.get(target, "English")
    prompt = (
        f"Translate the following {src_name} text to natural conversational {tgt_name}. "
        f"Return ONLY the {tgt_name} translation, no quotes, no explanations.\n\n"
        f"{src_name}: {text}"
    )
    config = genai_types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=400,
        safety_settings=_safety_settings(),
    )
    response = client.models.generate_content(
        model=_CHAT_MODEL,
        contents=prompt,
        config=config,
    )
    return (response.text or "").strip()


def score_session(transcript: str, profile_summary: str) -> dict:
    """
    End-of-session rubric scoring. Single non-streaming Gemini call.

    Args:
        transcript: Full conversation transcript (user + assistant turns).
        profile_summary: A short description of the user's level and recent goals.

    Returns:
        Dict with keys: feedback_note (str), strengths (list[str]),
        suggestions (list[str]), production_level (str like 'A2'),
        turn_count (int)
    """
    client = _get_client()

    prompt = f"""You are evaluating a Spanish learner's conversation practice.

Learner context:
{profile_summary}

Conversation transcript:
{transcript}

Provide a brief, encouraging evaluation in JSON with these keys:
- feedback_note (string, 1-2 sentences, friendly tone)
- strengths (array of 1-3 short strings, e.g., "used past tense correctly")
- suggestions (array of 0-2 short strings, e.g., "try using más complex sentences")
- production_level (one of: "Pre-A1", "A1", "A2", "B1", "B2", "C1", "C2")

Return ONLY valid JSON, no markdown fences."""

    config = genai_types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=500,
        response_mime_type="application/json",
        safety_settings=_safety_settings(),
    )

    response = client.models.generate_content(
        model=_RUBRIC_MODEL,
        contents=prompt,
        config=config,
    )

    import json
    try:
        return json.loads(response.text or "{}")
    except Exception:
        return {
            "feedback_note": "Nice chat! Keep practicing.",
            "strengths": [],
            "suggestions": [],
            "production_level": "A1",
        }
