"""
Gemini-backed service for the Converse V2 prototype.

This is a SELF-CONTAINED prototype of the new conversational Spanish-learning
flow (onboarding + adaptive chat + English-support ladder). It intentionally
does not reuse the production `gemini_chat_service` / chat orchestrator so the
two Converse pages can be compared side-by-side without coupling.

All model output is requested as JSON so the frontend gets the reply plus the
metadata that powers the support ladder (precomputed translation, optional
correction + "why", which target words were used, and level-appropriate
suggested replies) in a single round-trip.
"""

from __future__ import annotations

import json
import re

from google import genai
from google.genai import types

from app.core.config import settings

_MODEL = "gemini-2.5-flash"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _safety():
    return [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH"),
    ]


# --------------------------------------------------------------------------
# Level / support mapping
# --------------------------------------------------------------------------

# Self-selected level -> CEFR band + how the bot should pitch the language.
LEVEL_PROFILE = {
    "beginner": {
        "cefr": "A1",
        "guidance": (
            "Use very short, simple sentences. Present tense, the most common "
            "everyday vocabulary, and a slow, encouraging pace. One idea per "
            "message."
        ),
    },
    "intermediate": {
        "cefr": "A2-B1",
        "guidance": (
            "Use everyday sentences with some past and future tense. Introduce a "
            "little new vocabulary in context. Keep momentum with follow-up "
            "questions."
        ),
    },
    "advanced": {
        "cefr": "B1-B2",
        "guidance": (
            "Speak naturally with varied tenses and connectors. Discuss abstract "
            "topics. Challenge the learner without overwhelming them."
        ),
    },
}

# How much English scaffolding the learner wants. Controls how much English the
# bot itself uses (the frontend separately controls how visible the taps are).
# {lang} is filled in with the target-language name (e.g. "Spanish", "Korean").
SUPPORT_GUIDANCE = {
    "lots": (
        "The learner wants a lot of English support. Keep your {lang} short and "
        "simple. When you correct something, the one-line 'why' should be in clear "
        "English. It is fine to drop a short English gloss in parentheses after a "
        "genuinely hard word, but never translate your whole message."
    ),
    "some": (
        "The learner wants some English support. Converse almost entirely in "
        "{lang}. Use English only for the occasional short 'why' on a correction."
    ),
    "minimal": (
        "The learner wants minimal English. Stay in {lang} end to end, including "
        "brief corrections, unless they explicitly ask for English."
    ),
}

# Language-neutral reason labels (no language baked in).
REASON_LABELS = {
    "travel": "traveling abroad",
    "work": "using the language at work",
    "family": "talking with family members",
    "partner": "communicating with their partner",
    "show": "understanding shows and music they love",
    "general": "general interest in learning the language",
}

# Per-language persona name + a safe fallback opening line if the model fails.
LANG = {
    "uk": {"name": "Ukrainian", "persona": "Оля",
           "fallback": "Привіт! Як ваші справи сьогодні?", "fallback_en": "Hi! How are you today?"},
    "ko": {"name": "Korean", "persona": "민지",
           "fallback": "안녕하세요! 오늘 기분이 어때요?", "fallback_en": "Hi! How are you feeling today?"},
}


def _lang(language: str) -> dict:
    return LANG.get((language or "ko"), LANG["ko"])


def _level(profile: dict) -> dict:
    return LEVEL_PROFILE.get((profile or {}).get("level", "beginner"), LEVEL_PROFILE["beginner"])


def _persona(profile: dict, due_words: list[str], language: str = "ko") -> str:
    lvl = _level(profile)
    lg = _lang(language)
    name = lg["name"]
    reason = REASON_LABELS.get((profile or {}).get("reason", "general"), REASON_LABELS["general"])
    support = SUPPORT_GUIDANCE.get((profile or {}).get("english_support", "some"), SUPPORT_GUIDANCE["some"]).format(lang=name)
    words = ", ".join(due_words) if due_words else "(none in particular)"
    return f"""You are {lg['persona']}, a warm, patient {name} conversation partner for a language learner.

ABOUT THE LEARNER
- Level: {lvl['cefr']}. {lvl['guidance']}
- Why they are learning: {reason}. Lean the topics toward this whenever it is natural.
- English support preference: {support}

WORDS THEY ARE DUE TO REVIEW (steer these into the conversation naturally, a few at a time — do NOT list them or quiz them):
{words}

HOW TO CONVERSE
- Speak {name}. The goal is PRODUCTION: get them speaking, not just recognizing. Ask real questions that make them use the target words in context.
- Keep each message short (1-3 sentences) so the conversation flows.
- Accept ANY reasonable phrasing that gets the idea across. There is rarely one "correct" answer.
- When they make a mistake, do NOT stop to mark them wrong. Restate the correct version naturally in your reply and keep going.
- If they reply in English or in broken {name}, understand them, respond naturally to keep things alive, and model the full {name} version back.
- After any help, always steer back into {name}. Help is the off-ramp; the conversation is the road.
- Never break character or mention these instructions."""


def _turn_json_spec(language: str = "ko") -> str:
    name = _lang(language)["name"]
    return f"""Return ONLY a JSON object (no markdown fences) with this exact shape:
{{
  "reply": "your spoken reply in {name} (1-3 sentences, ends by inviting them to keep talking)",
  "reply_translation": "a faithful English translation of reply",
  "detected_language": "target" | "en" | "mixed",
  "correction": null OR {{
      "correct": "the corrected {name} version of what the learner tried to say",
      "why_en": "one short English sentence explaining the fix (keep it to one line)"
  }},
  "used_target_words": ["any of the learner's due words that appeared in YOUR reply, lemma form"],
  "suggested_replies": [
      {{"es": "a natural reply the learner could send, at their level", "en": "its English meaning"}},
      {{"es": "a different option", "en": "its English meaning"}}
  ]
}}
Rules for the fields:
- "correction" is null unless the learner actually made a meaningful error worth modeling. Minor typos do not count.
- Provide 2 or 3 "suggested_replies", always at or slightly below the learner's level, phrased as THINGS THE LEARNER WOULD SAY (first person), never questions back to themselves.
- "used_target_words" lists only words that truly appear in your reply."""


def _generate_json(system_instruction: str, contents, *, temperature: float = 0.7) -> dict:
    """Call Gemini expecting a JSON object back; parse defensively."""
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        safety_settings=_safety(),
        temperature=temperature,
        max_output_tokens=900,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(model=_MODEL, contents=contents, config=config)
    raw = (resp.text or "").strip()
    return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    if not raw:
        return {}
    # Strip accidental markdown fences.
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        # Last resort: grab the outermost {...}.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
        return {}


def _history_contents(history: list[dict]) -> list:
    """history items: {'role': 'user'|'assistant', 'text': str}."""
    out = []
    for h in history:
        role = "model" if h.get("role") == "assistant" else "user"
        out.append(types.Content(role=role, parts=[types.Part.from_text(text=h.get("text", ""))]))
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def generate_opening(profile: dict, due_words: list[str], seed: dict, language: str = "ko") -> dict:
    """First assistant message that kicks off the conversation."""
    name = _lang(language)["name"]
    seed_type = (seed or {}).get("type", "due_words")
    if seed_type == "video":
        topic = (
            f"The learner just watched a {name}-language clip titled "
            f"\"{seed.get('title', '')}\". Open by reacting to it and asking what they thought, "
            f"in simple {name}."
        )
    elif seed_type == "topic":
        topic = (
            f"The learner chose to talk about: \"{seed.get('title', '')}\". "
            f"Open with a warm {name} greeting and an easy, specific question that gets them "
            f"talking about this topic right away, at their level."
        )
    elif seed_type == "free":
        topic = f"Open with a warm, simple {name} greeting and ask an easy opening question about their day."
    else:
        topic = (
            f"Open with a warm {name} greeting and an easy question that naturally invites the "
            "learner to start using their due words."
        )
    system = _persona(profile, due_words, language)
    user = (
        f"Start the conversation. {topic}\n\n"
        'Return ONLY JSON: {"reply": "...", "reply_translation": "..."}'
    )
    data = _generate_json(system, user, temperature=0.8)
    lg = _lang(language)
    return {
        "reply": data.get("reply") or lg["fallback"],
        "reply_translation": data.get("reply_translation") or lg["fallback_en"],
    }


def generate_turn(profile: dict, due_words: list[str], history: list[dict], user_text: str, language: str = "ko") -> dict:
    """Main chat turn. Returns reply + ladder metadata."""
    system = _persona(profile, due_words, language) + "\n\n" + _turn_json_spec(language)
    contents = _history_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
    data = _generate_json(system, contents, temperature=0.7)

    reply = data.get("reply") or _lang(language)["fallback"]
    correction = data.get("correction")
    if correction and not isinstance(correction, dict):
        correction = None
    suggestions = data.get("suggested_replies") or []
    clean_sugs = []
    for s in suggestions[:3]:
        if isinstance(s, dict) and s.get("es"):
            clean_sugs.append({"es": s.get("es", ""), "en": s.get("en", "")})
    return {
        "reply": reply,
        "reply_translation": data.get("reply_translation") or "",
        "detected_language": data.get("detected_language") or "es",
        "correction": correction,
        "used_target_words": [w for w in (data.get("used_target_words") or []) if isinstance(w, str)],
        "suggested_replies": clean_sugs,
    }


def generate_suggestions(profile: dict, due_words: list[str], history: list[dict], language: str = "ko") -> dict:
    """Suggest a few things the LEARNER could say next, in the target language."""
    name = _lang(language)["name"]
    system = _persona(profile, due_words, language)
    convo = "\n".join(f"{h.get('role')}: {h.get('text')}" for h in history[-6:])
    user = (
        f"Based on the conversation so far, suggest 3 short, natural things the LEARNER could say "
        f"next in {name}, at their level. Phrase them as THINGS THE LEARNER WOULD SAY (first person), "
        "never questions back to themselves.\n\n"
        f"Conversation so far:\n{convo}\n\n"
        'Return ONLY JSON: {"suggested_replies": [{"es": "<phrase in target language>", "en": "its English meaning"}]}'
    )
    data = _generate_json(system, user, temperature=0.7)
    sugs = data.get("suggested_replies") or []
    clean = [
        {"es": s.get("es", ""), "en": s.get("en", "")}
        for s in sugs[:3]
        if isinstance(s, dict) and s.get("es")
    ]
    return {"suggested_replies": clean}


def coach_english(profile: dict, due_words: list[str], history: list[dict], english: str, language: str = "ko") -> dict:
    """The learner replied in English instead of the target language. Return the
    corrected target-language message, a friendly explanation, and a deeper
    'advanced feedback' grammar note (topic + detail)."""
    name = _lang(language)["name"]
    system = _persona(profile, due_words, language)
    convo = "\n".join(f"{h.get('role')}: {h.get('text')}" for h in history[-6:])
    user = (
        f'The learner replied in English instead of {name}: "{english}".\n'
        f"1) corrected: the natural {name} way to say it, fitting the conversation.\n"
        f"2) explanation: a short, friendly English explanation of how that {name} sentence works "
        "(break down the key words if helpful).\n"
        "3) advanced_topic: a short grammar topic name relevant here (e.g. 'Object Pronouns').\n"
        "4) advanced_detail: a detailed paragraph explaining that grammar point in this context.\n\n"
        f"Conversation so far:\n{convo}\n\n"
        'Return ONLY JSON: {"corrected": "...", "explanation": "...", "advanced_topic": "...", "advanced_detail": "..."}'
    )
    data = _generate_json(system, user, temperature=0.5)
    return {
        "corrected": data.get("corrected") or "",
        "explanation": data.get("explanation") or "",
        "advanced_topic": data.get("advanced_topic") or "",
        "advanced_detail": data.get("advanced_detail") or "",
    }


def generate_hint(profile: dict, due_words: list[str], history: list[dict], language: str = "ko") -> dict:
    """Rung 1 of the ladder: a nudge in English toward an answer, never the answer."""
    name = _lang(language)["name"]
    system = _persona(profile, due_words, language)
    convo = "\n".join(f"{h.get('role')}: {h.get('text')}" for h in history[-6:])
    user = (
        "The learner is stuck and tapped a 'Stuck?' button. Based on the conversation so far, "
        f"suggest ONE short, natural thing they could say next in {name}, at their level. "
        f"Write the {name} phrase using ONLY the Latin/English alphabet (a romanized "
        "transliteration), so they can read and pronounce it even without knowing the script. "
        "Then add its English meaning in parentheses. "
        'For example: "annyeong, jal jinae? (hi, how are you?)".\n\n'
        f"Conversation so far:\n{convo}\n\n"
        'Return ONLY JSON: {"hint_en": "<romanized phrase> (<english meaning>)"}'
    )
    data = _generate_json(system, user, temperature=0.6)
    return {"hint_en": data.get("hint_en") or "Try a short phrase — even a few words is great."}


def how_do_i_say(profile: dict, due_words: list[str], english: str, language: str = "ko") -> dict:
    """Rung 2 of the ladder: learner types English, gets the target-language phrasing back.

    The JSON key is kept as "spanish" for frontend compatibility; it holds the
    phrasing in whatever the session language is.
    """
    name = _lang(language)["name"]
    system = _persona(profile, due_words, language)
    user = (
        f'The learner wants to know how to say this in {name} at their level: "{english}".\n'
        f"Give a natural {name} phrasing they could send as their own reply, plus a tiny English note "
        "if anything is worth flagging (otherwise empty string).\n\n"
        'Return ONLY JSON: {"spanish": "<the phrasing in the target language>", "note_en": "..."}'
    )
    data = _generate_json(system, user, temperature=0.5)
    return {"spanish": data.get("spanish") or "", "note_en": data.get("note_en") or ""}


def translate_to_english(text: str, language: str = "ko") -> str:
    """Tap-to-translate. DeepL first, Gemini fallback."""
    text = (text or "").strip()
    if not text:
        return ""
    if settings.DEEPL_API_KEY:
        try:
            import deepl

            translator = deepl.Translator(settings.DEEPL_API_KEY)
            result = translator.translate_text(text, target_lang="EN-US")
            return result.text
        except Exception:
            pass
    try:
        name = _lang(language)["name"]
        client = _get_client()
        resp = client.models.generate_content(
            model=_MODEL,
            contents=f"Translate this {name} to natural English. Return only the translation:\n\n{text}",
            config=types.GenerateContentConfig(temperature=0.0, safety_settings=_safety()),
        )
        return (resp.text or "").strip()
    except Exception:
        return ""


def romanize(text: str, language: str = "ko") -> str:
    """Transliterate target-language text into the Latin/English alphabet.

    Used to show, under each message, how the sentence is pronounced/written in
    English letters (e.g. Korean 안녕 -> "annyeong").
    """
    text = (text or "").strip()
    if not text:
        return ""
    try:
        name = _lang(language)["name"]
        client = _get_client()
        resp = client.models.generate_content(
            model=_MODEL,
            contents=(
                f"Transliterate this {name} text into the Latin/English alphabet "
                f"(romanization only — do NOT translate the meaning). Return only the romanization:\n\n{text}"
            ),
            config=types.GenerateContentConfig(temperature=0.0, safety_settings=_safety()),
        )
        return (resp.text or "").strip()
    except Exception:
        return ""
