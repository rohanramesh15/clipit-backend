"""
Embedding service for chat context retrieval.

Embeds Spanish subtitle sentences via Gemini's text-embedding-004 (768-dim)
and persists them to the subtitle_embedding table for fast pgvector search.

Used by:
  - One-time backfill for existing Spanish-tagged videos
  - Incremental hook in subtitle_service.save_subtitles_from_extension
"""

from typing import Iterable, List, Optional
import re

from google import genai
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.subtitle_embedding import SubtitleEmbedding
from app.services.lemmatizer import lemmatize as _lemmatize_any
from app.services.lemmatizer import filter_content_lemmas as _filter_content_lemmas_any

_EMBED_MODEL = "gemini-embedding-001"
_EMBED_DIMS = 768  # Matches Vector(768) in subtitle_embedding column
_BATCH_SIZE = 100  # Gemini accepts up to ~100 texts per batch
_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured — cannot embed subtitles."
            )
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a batch of texts via Gemini gemini-embedding-001. Returns 768-dim vectors
    (the model's default is 3072 dims but we truncate to match our pgvector column).
    """
    if not texts:
        return []
    client = _get_client()
    result = client.models.embed_content(
        model=_EMBED_MODEL,
        contents=texts,
        config={"output_dimensionality": _EMBED_DIMS},
    )
    return [e.values for e in result.embeddings]


_TARGET_KEY_BY_LANG = {
    "es": "spanish",
    "en": "english",
    "ko": "korean",
    "uk": "ukrainian",
}


def chunk_subtitles_to_sentences(subtitles: List[dict], lang: str = "es") -> List[dict]:
    """
    Convert a list of subtitle dicts (with target-language text + English + timestamps)
    into sentence-level chunks suitable for embedding.

    Each output dict contains:
      - sentence: target-language text
      - sentence_translation: English (None when target IS English)
      - ts_start, ts_end: timestamps
      - lemma_set: list of content lemmas (after function-word filtering)
    """
    target_key = _TARGET_KEY_BY_LANG.get(lang, lang)
    chunks: List[dict] = []

    for sub in subtitles:
        text = (sub.get(target_key) or "").strip()
        if not text:
            continue
        # Skip very short fragments
        if len(text) < 3:
            continue

        ts_start = float(sub.get("start", 0.0) or 0.0)
        ts_end = float(sub.get("end", ts_start + float(sub.get("duration", 0.0) or 0.0)))
        # If target is English, there's no separate translation pair
        eng = None if lang == "en" else ((sub.get("english") or "").strip() or None)

        lemmas = _filter_content_lemmas_any(_lemmatize_any(text, lang), lang)

        chunks.append({
            "sentence": text,
            "sentence_translation": eng,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "lemma_set": lemmas,
        })

    return chunks


def embed_video_subtitles(video_id: str, subtitles: List[dict], language: str = "es",
                          db: Optional[Session] = None) -> int:
    """
    Embed and persist subtitle sentences for one video.

    Idempotent: deletes existing embeddings for this (video_id, language) first.
    Returns count of rows inserted.
    """
    if language not in ("es", "en"):
        return 0  # Chat supports Spanish and English target languages

    chunks = chunk_subtitles_to_sentences(subtitles, lang=language)
    if not chunks:
        return 0

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        # Wipe stale embeddings for this video+language
        db.query(SubtitleEmbedding).filter(
            SubtitleEmbedding.video_id == video_id,
            SubtitleEmbedding.language == language,
        ).delete()

        # Batch-embed and insert
        total = 0
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i:i + _BATCH_SIZE]
            try:
                vectors = embed_texts([c["sentence"] for c in batch])
            except Exception as e:
                print(f"[embed] batch failed for {video_id}: {e}")
                continue

            for chunk, vec in zip(batch, vectors):
                row = SubtitleEmbedding(
                    video_id=video_id,
                    language=language,
                    sentence=chunk["sentence"],
                    sentence_translation=chunk["sentence_translation"],
                    lemma_set=chunk["lemma_set"],
                    ts_start=chunk["ts_start"],
                    ts_end=chunk["ts_end"],
                    embedding=vec,
                )
                db.add(row)
                total += 1

        db.commit()
        return total
    finally:
        if owns_session:
            db.close()


def embed_query(query: str) -> List[float]:
    """Embed a single text (for similarity search at query time)."""
    vectors = embed_texts([query])
    return vectors[0] if vectors else []
