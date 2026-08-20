"""
Chat orchestrator.

The conductor that ties together:
  - VocabProfileService (what the user knows)
  - ContextRetriever (what they've watched, relevant to the topic)
  - GeminiChatService (the actual LLM call + streaming)
  - LemmaAuditService (which words in the reply are 'new' to them)

Produces the system instruction for Gemini and runs the lemma audit
on the streamed reply.
"""

from typing import Iterator, List, Optional

from sqlalchemy.orm import Session

from app.services.context_retriever import retrieve, RetrievedSentence
from app.services.gemini_chat_service import stream_chat

# Conversation modes — different personas/intent for the same backend.
# Default is "free": natural conversation partner. Other modes layer on top.
CONVERSATION_MODE_PROMPTS = {
    "free": "",  # Default — no extra instructions
    "debate": (
        "Mode: Friendly debate. Pick a clear position on whatever the learner "
        "discusses and defend it, but always invite their counter-arguments. "
        "Use rhetorical questions. Stay polite — never combative."
    ),
    "interview": (
        "Mode: Interview. You are interviewing the learner about a topic. "
        "Ask open-ended questions one at a time. Listen to their reply and "
        "ask a relevant follow-up. The learner should be doing most of the talking."
    ),
    "roleplay": (
        "Mode: Role-play. Stay completely in character for the scenario. "
        "Never break the fourth wall — don't say things like 'as an AI' or "
        "'in this role-play'. React as a real person in that situation would."
    ),
    "shadowing": (
        "Mode: Shadowing practice. Speak one short sentence (5-10 words) in the "
        "target language, then wait for the learner to repeat it back. After "
        "they do, give one brief encouraging response and offer the next sentence. "
        "Keep sentences varied and at the learner's level."
    ),
}
from app.services.lemmatizer import (
    lemmatize,
    filter_content_lemmas,
    function_words,
    get_nlp,
)
from app.services.vocab_profile_service import (
    VocabProfile,
    cefr_label,
)
from app.services.vocab_service import load_frequency_map

# Top-N most common words shouldn't be flagged as "new" — these are words
# even beginners encounter constantly. Loaded once per language, cached.
_TOP_COMMON_BY_LANG: dict[str, set[str]] = {}
_TOP_COMMON_CUTOFF = 200

# Per-language obvious cognates / extremely common loanwords that shouldn't
# surface as save-to-deck chips even when not yet in the user's deck.
_OBVIOUS_COGNATES_BY_LANG: dict[str, set[str]] = {
    "es": {
        "internet", "video", "audio", "música", "foto", "computadora",
        "hospital", "hotel", "restaurante", "doctor", "policía", "actor",
        "actriz", "presidente", "famoso", "popular", "natural", "normal",
        "perfecto", "increíble", "fantástico", "imposible", "posible",
    },
    # For English-target, the obvious-cognates concept doesn't apply the
    # same way — we just leave it empty and rely on the top-N filter.
    "en": set(),
}


def _get_top_common(language: str) -> set[str]:
    cached = _TOP_COMMON_BY_LANG.get(language)
    if cached is not None:
        return cached
    try:
        freq = load_frequency_map(language)
        result = {w for w, rank in freq.items() if rank <= _TOP_COMMON_CUTOFF}
    except Exception:
        result = set()
    _TOP_COMMON_BY_LANG[language] = result
    return result


# Per-language conversational instruction blocks for the system prompt.
_LANG_PROMPT_HEADER = {
    "es": (
        "You are a friendly Spanish conversation partner helping someone practice.\n"
        "Speak in natural, conversational Spanish — never in English."
    ),
    "en": (
        "You are a friendly English conversation partner helping someone practice.\n"
        "Speak in natural, conversational English — keep replies easy to follow."
    ),
}

_LANG_REGISTER_HINT = {
    "es": "- Use the warm, casual `tú` register unless the scenario demands `usted`.",
    "en": "- Use casual register unless the scenario calls for formal speech.",
}


def build_system_instruction(
    profile: VocabProfile,
    level: str,
    seed_label: Optional[str],
    seed_video_title: Optional[str],
    retrieved: List[RetrievedSentence],
    memory_facts: Optional[List[str]] = None,
    adaptation_hint: Optional[str] = None,
    mode: Optional[str] = None,
) -> str:
    """
    Build the system prompt for Gemini. Encodes language, level, vocab lock,
    seed/topic context, retrieved authentic lines, memory, mode, adaptation.
    """
    language = profile.language
    known_sample = list(profile.known_lemmas)[:200]  # cap prompt size
    learning_sample = list(profile.learning_lemmas)[:100]

    header = _LANG_PROMPT_HEADER.get(language, _LANG_PROMPT_HEADER["es"])
    register = _LANG_REGISTER_HINT.get(language, _LANG_REGISTER_HINT["es"])

    parts = [
        header,
        "",
        f"Target proficiency level: {level}.",
        "Match your vocabulary, sentence length, and grammar complexity to this level.",
        "Keep replies short — 1-3 sentences, ~15-30 words max per reply.",
        "",
        "Vocabulary rules (very important):",
        "- Prefer words from the learner's known vocabulary (see list below).",
        "- You may introduce at most 1-2 new words per reply, ideally from the learning list.",
        "- Avoid rare or advanced vocabulary unless it directly fits the topic.",
        "- Do NOT pile up multiple new words at once — keep input comprehensible.",
        "",
        "Conversation behavior:",
        "- Ask follow-up questions to keep the conversation going.",
        "- Show interest in what the learner says.",
        register,
        "- Do NOT translate or explain unless explicitly asked.",
        "- Do NOT correct grammar mid-conversation — that comes at the end.",
    ]

    mode_prompt = CONVERSATION_MODE_PROMPTS.get((mode or "free").lower(), "")
    if mode_prompt:
        parts.append("")
        parts.append(mode_prompt)

    if seed_label:
        parts.append("")
        parts.append(f"Conversation topic: {seed_label}.")
    if seed_video_title:
        parts.append(
            f"The learner recently watched: \"{seed_video_title}\". "
            f"You can naturally reference scenes, themes, or vocabulary from it, "
            f"but don't quote subtitles verbatim."
        )

    if adaptation_hint:
        parts.append("")
        parts.append(f"Mid-session signal: {adaptation_hint}")

    if memory_facts:
        parts.append("")
        parts.append("What you remember about this learner from past chats "
                     "(reference naturally if relevant, do NOT recite them verbatim):")
        for f in memory_facts[:5]:
            parts.append(f"- {f}")

    if retrieved:
        lang_label = {"es": "Spanish", "en": "English"}.get(language, language)
        parts.append("")
        parts.append(f"Authentic {lang_label} lines from content the learner has seen "
                     "(use these as style/topic references, do NOT quote directly):")
        for r in retrieved[:5]:
            parts.append(f"- {r.sentence}")

    if known_sample:
        parts.append("")
        parts.append("Learner's known vocabulary (prefer these words):")
        parts.append(", ".join(known_sample))

    if learning_sample:
        parts.append("")
        parts.append("Learner is actively learning these (good candidates to use):")
        parts.append(", ".join(learning_sample))

    return "\n".join(parts)


def audit_reply_lemmas(reply_text: str, profile: VocabProfile) -> List[dict]:
    """
    Find content lemmas in the AI reply that are NOT in the user's familiar set.
    Language-aware via profile.language.
    """
    if not reply_text or not reply_text.strip():
        return []

    language = profile.language
    doc = get_nlp(language)(reply_text)

    top_common = _get_top_common(language)
    fn_words = function_words(language)
    cognates = _OBVIOUS_COGNATES_BY_LANG.get(language, set())
    seen_lemmas: set[str] = set()
    out: List[dict] = []

    for tok in doc:
        if tok.is_punct or tok.is_space or tok.like_num:
            continue
        if tok.pos_ == "PROPN":
            continue
        if tok.ent_type_ in {"PER", "LOC", "ORG", "MISC", "GPE"}:
            continue

        lemma = tok.lemma_.lower().strip()
        if not lemma or len(lemma) < 2:
            continue
        if lemma in fn_words:
            continue
        if lemma in top_common:
            continue
        if lemma in cognates:
            continue
        if lemma in seen_lemmas:
            continue
        if profile.is_familiar(lemma):
            continue
        if tok.is_title and tok.i != 0:
            continue

        seen_lemmas.add(lemma)
        out.append({
            "lemma": lemma,
            "surface": tok.text,
            "sentence": reply_text,
        })

    return out


def stream_turn(
    db: Session,
    profile: VocabProfile,
    level: str,
    history: List[dict],
    user_message: str,
    *,
    seed_label: Optional[str] = None,
    seed_video_id: Optional[str] = None,
    seed_video_title: Optional[str] = None,
    memory_facts: Optional[List[str]] = None,
    adaptation_hint: Optional[str] = None,
    mode: Optional[str] = None,
    usage_out: Optional[dict] = None,
) -> Iterator[str]:
    """
    Run one chat turn end-to-end, yielding text chunks as they stream.

    The orchestrator handles retrieval and prompt assembly internally so the
    route handler stays thin.
    """
    # Retrieve relevant subtitle context (best-effort — may be empty if no embeddings)
    retrieved: List[RetrievedSentence] = []
    try:
        retrieved = retrieve(
            db=db,
            query=user_message or seed_label or "",
            profile=profile,
            video_id=seed_video_id,
            language=profile.language or "es",
            top_k=5,
        )
    except Exception as e:
        print(f"[orchestrator] retrieval failed: {e}")

    system_instruction = build_system_instruction(
        profile=profile,
        level=level,
        seed_label=seed_label,
        seed_video_title=seed_video_title,
        retrieved=retrieved,
        memory_facts=memory_facts,
        adaptation_hint=adaptation_hint,
        mode=mode,
    )

    yield from stream_chat(
        system_instruction=system_instruction,
        history=history,
        user_message=user_message,
        usage_out=usage_out,
    )
