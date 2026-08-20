"""
Cross-session memory service.

After each chat session ends:
  1. extract_facts(transcript) — Gemini Flash extracts 1-3 durable facts
     about the user (plans, opinions, language struggles, preferences).
  2. store_facts() — embed each fact via Gemini and persist to chat_memory_fact.

When a new chat turn starts:
  3. retrieve_facts(user, query) — pgvector cosine-search the user's facts,
     return the top-K most relevant ones. These are inlined into the
     system prompt so the AI feels like an ongoing relationship.
"""

import json
from typing import List, Optional

from google import genai
from google.genai import types as genai_types
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_memory import ChatMemoryFact
from app.services.embedding_service import embed_texts, embed_query


_FACT_MODEL = "gemini-2.5-flash"


def extract_facts(transcript: str, profile_summary: str) -> List[str]:
    """
    Run a single Gemini Flash call to extract durable facts about the user
    from a chat transcript. Returns 0-3 short facts.

    Each fact should be:
      - About the user (not the AI)
      - Durable (not "user said hi today")
      - Specific (not "user likes Spanish")
      - One sentence, written in third person, present tense
    """
    if not transcript.strip():
        return []

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    prompt = f"""Extract up to 3 durable facts about the LEARNER from this Spanish conversation transcript.

A good fact is:
- About the learner (not the AI assistant)
- Durable / worth remembering next session (a plan, an opinion, a relationship, a learning struggle, a preference)
- Specific (not generic)
- One short sentence, third person, present tense

Bad facts (do NOT include):
- "The learner said hello"
- "The learner is learning Spanish"
- "The learner likes the AI"
- Anything about the conversation itself

Profile context:
{profile_summary}

Transcript:
{transcript}

Return a JSON object with a single key "facts" mapped to an array of 0-3 short strings.
If nothing durable came up, return {{"facts": []}}.
Return ONLY valid JSON."""

    config = genai_types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=300,
        response_mime_type="application/json",
    )

    try:
        response = client.models.generate_content(
            model=_FACT_MODEL,
            contents=prompt,
            config=config,
        )
        data = json.loads(response.text or "{}")
        raw = data.get("facts", [])
        return [str(f).strip() for f in raw if isinstance(f, str) and f.strip()][:3]
    except Exception as e:
        print(f"[memory] fact extraction failed: {e}")
        return []


def store_facts(
    db: Session,
    user_id: int,
    language: str,
    session_id: Optional[int],
    facts: List[str],
) -> int:
    """Embed and persist a batch of facts. Returns count stored."""
    if not facts:
        return 0
    try:
        vectors = embed_texts(facts)
    except Exception as e:
        print(f"[memory] embed failed: {e}")
        return 0

    stored = 0
    for fact, vec in zip(facts, vectors):
        if not vec:
            continue
        db.add(ChatMemoryFact(
            user_id=user_id,
            language=language,
            fact=fact,
            source_session_id=session_id,
            embedding=vec,
        ))
        stored += 1
    db.commit()
    return stored


def retrieve_facts(
    db: Session,
    user_id: int,
    language: str,
    query: str,
    *,
    top_k: int = 4,
    min_similarity: float = 0.5,
) -> List[str]:
    """
    pgvector cosine-search for facts most relevant to the current chat turn.
    Returns the fact strings (no metadata).
    """
    if not query.strip():
        return []
    try:
        qvec = embed_query(query)
    except Exception as e:
        print(f"[memory] query embed failed: {e}")
        return []
    if not qvec:
        return []

    sql = text("""
        SELECT fact,
               (1 - (embedding <=> CAST(:qvec AS vector))) AS similarity
        FROM chat_memory_fact
        WHERE user_id = :user_id AND language = :language
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :top_k
    """)
    rows = db.execute(sql, {
        "qvec": str(qvec),
        "user_id": user_id,
        "language": language,
        "top_k": top_k,
    }).mappings().all()

    return [r["fact"] for r in rows if float(r["similarity"] or 0.0) >= min_similarity]
