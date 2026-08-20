from sqlalchemy import Column, Integer, String, Float, JSON, Index
from pgvector.sqlalchemy import Vector
from .base import BaseModel


class SubtitleEmbedding(BaseModel):
    """
    Vector embeddings of subtitle sentences, used by the chat context retriever.

    One row per sentence pulled from a TrackedVideo. The embedding column uses
    pgvector and is indexed with HNSW for fast similarity search.
    """
    __tablename__ = "subtitle_embedding"

    video_id = Column(String(100), nullable=False, index=True)
    language = Column(String(10), nullable=False, index=True)
    sentence = Column(String, nullable=False)
    sentence_translation = Column(String, nullable=True)  # English translation if available
    lemma_set = Column(JSON, nullable=True)  # List of content lemmas in this sentence
    ts_start = Column(Float, nullable=True)
    ts_end = Column(Float, nullable=True)
    embedding = Column(Vector(768), nullable=False)  # Gemini text-embedding-004 is 768-dim

    __table_args__ = (
        Index('ix_subtitle_embedding_video_lang', 'video_id', 'language'),
    )
