from sqlalchemy import Column, Integer, String, ForeignKey, Text, Index
from pgvector.sqlalchemy import Vector
from .base import BaseModel


class ChatMemoryFact(BaseModel):
    """
    A persistent fact about a user, extracted from a past chat session.

    Example facts:
      - "user is planning a trip to Mexico City in June"
      - "user's sister also speaks Spanish"
      - "user struggles with the subjunctive mood"
      - "user enjoys discussing the show La Casa de Papel"

    Each fact is embedded so we can do similarity-search retrieval at
    chat-turn time and inject the most relevant ones into the system prompt.
    """
    __tablename__ = "chat_memory_fact"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    language = Column(String(10), nullable=False, default="es", index=True)
    fact = Column(Text, nullable=False)
    source_session_id = Column(Integer, ForeignKey("chat_session.id", ondelete="SET NULL"), nullable=True)
    embedding = Column(Vector(768), nullable=False)

    __table_args__ = (
        Index('ix_chat_memory_fact_user_lang', 'user_id', 'language'),
    )
