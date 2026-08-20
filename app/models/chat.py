from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, JSON, Text, Index
from sqlalchemy.orm import relationship
from .base import BaseModel


class ChatSession(BaseModel):
    """One conversation. Anchored to a user, optionally to a video."""
    __tablename__ = "chat_session"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    language = Column(String(10), nullable=False, default="es")
    seed_type = Column(String(20), nullable=False, default="free")  # 'video' | 'scenario' | 'srs' | 'free' | 'wildcard'
    seed_video_id = Column(String(100), nullable=True, index=True)
    seed_label = Column(String(255), nullable=True)  # Display label that started the conversation
    mode = Column(String(20), nullable=False, default="free")  # 'free' | 'debate' | 'interview' | 'roleplay' | 'shadowing'
    level_used = Column(String(8), nullable=True)  # e.g. 'A1', 'A2', 'B1'
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    summary_json = Column(JSON, nullable=True)

    turns = relationship("ChatTurn", back_populates="session", cascade="all, delete-orphan")
    saved_words = relationship("ChatSavedWord", back_populates="session", cascade="all, delete-orphan")


class ChatTurn(BaseModel):
    """One message in a conversation — either user or assistant."""
    __tablename__ = "chat_turn"

    session_id = Column(Integer, ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)  # Turn order within the session
    role = Column(String(16), nullable=False)  # 'user' | 'assistant'
    text = Column(Text, nullable=False)
    audio_url = Column(String, nullable=True)  # TTS audio for assistant, recorded audio for user (if stored)
    lemmas_json = Column(JSON, nullable=True)  # Content lemmas detected in this turn
    off_list_lemmas_json = Column(JSON, nullable=True)  # Lemmas the user doesn't know (for chip overlay)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    session = relationship("ChatSession", back_populates="turns")

    __table_args__ = (
        Index('ix_chat_turn_session_idx', 'session_id', 'idx'),
    )


class ChatSavedWord(BaseModel):
    """A word saved from chat to the user's FSRS deck via the click-to-save chip."""
    __tablename__ = "chat_saved_word"

    session_id = Column(Integer, ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False, index=True)
    turn_id = Column(Integer, ForeignKey("chat_turn.id", ondelete="CASCADE"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lemma = Column(String, nullable=False)
    surface_form = Column(String, nullable=True)  # The actual word as the user saw it
    sentence = Column(Text, nullable=True)  # Surrounding sentence used as the example
    fsrs_card_id = Column(Integer, nullable=True)  # FK to user_flashcard_progress.id if linked

    session = relationship("ChatSession", back_populates="saved_words")
