"""
Context retriever for chat.

Given a topic query (and optionally a specific video_id), returns the top-N
subtitle sentences most relevant to the topic, re-ranked by how well they
fit the user's vocabulary profile.

This is what grounds chat replies in the user's actual watched content.
"""

from dataclasses import dataclass
from typing import List, Optional, Set

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.subtitle_embedding import SubtitleEmbedding
from app.services.embedding_service import embed_query
from app.services.vocab_profile_service import VocabProfile


@dataclass
class RetrievedSentence:
    sentence: str
    sentence_translation: Optional[str]
    video_id: str
    ts_start: float
    ts_end: float
    lemma_set: List[str]
    similarity: float          # 0..1 cosine similarity to the query
    vocab_compliance: float    # 0..1 fraction of lemmas in user's known set
    score: float               # final blended score


def _compliance_ratio(lemmas: List[str], known: Set[str]) -> float:
    if not lemmas:
        return 0.0
    matches = sum(1 for l in lemmas if l.lower() in known)
    return matches / len(lemmas)


def retrieve(
    db: Session,
    query: str,
    profile: VocabProfile,
    *,
    video_id: Optional[str] = None,
    language: str = "es",
    top_k: int = 5,
    candidate_pool: int = 30,
    min_similarity: float = 0.5,
    comprehension_weight: float = 0.4,
) -> List[RetrievedSentence]:
    """
    Vector-search for relevant subtitle sentences, then re-rank by vocab compliance.

    Args:
        query: The topic or user input to anchor retrieval on.
        profile: User's vocab profile (drives the re-rank).
        video_id: If provided, only search within that video's embeddings.
        language: 'es' for v1.
        top_k: How many final sentences to return.
        candidate_pool: How many to retrieve from pgvector before re-ranking.
        min_similarity: Discard candidates below this cosine similarity.
        comprehension_weight: 0..1, how much vocab compliance influences final rank.

    Returns:
        Top-k sentences, ordered by blended score.
    """
    try:
        query_vec = embed_query(query)
    except Exception as e:
        print(f"[retriever] query embed failed: {e}")
        return []

    if not query_vec:
        return []

    known = profile.known_lemmas | profile.learning_lemmas

    # Build pgvector cosine-distance query. We use the <=> operator (cosine distance).
    # Similarity = 1 - distance.
    sql = text(f"""
        SELECT id, video_id, sentence, sentence_translation, lemma_set,
               ts_start, ts_end,
               (1 - (embedding <=> CAST(:qvec AS vector))) AS similarity
        FROM subtitle_embedding
        WHERE language = :lang
          {"AND video_id = :video_id" if video_id else ""}
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :pool
    """)

    params = {
        "qvec": str(query_vec),
        "lang": language,
        "pool": candidate_pool,
    }
    if video_id:
        params["video_id"] = video_id

    rows = db.execute(sql, params).mappings().all()

    candidates: List[RetrievedSentence] = []
    for row in rows:
        sim = float(row["similarity"] or 0.0)
        if sim < min_similarity:
            continue
        lemmas = list(row["lemma_set"] or [])
        compliance = _compliance_ratio(lemmas, known)
        score = (1.0 - comprehension_weight) * sim + comprehension_weight * compliance
        candidates.append(RetrievedSentence(
            sentence=row["sentence"],
            sentence_translation=row["sentence_translation"],
            video_id=row["video_id"],
            ts_start=float(row["ts_start"] or 0.0),
            ts_end=float(row["ts_end"] or 0.0),
            lemma_set=lemmas,
            similarity=sim,
            vocab_compliance=compliance,
            score=score,
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]
