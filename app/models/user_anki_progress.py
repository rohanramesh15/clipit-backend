from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship
from .base import BaseModel


class UserAnkiProgress(BaseModel):
    """Stores imported Anki progress data for vocabulary words.

    When a user imports an Anki deck, the scheduling data is stored here.
    When a flashcard is later created from a video containing this word,
    the Anki progress data is applied to the new flashcard.
    """
    __tablename__ = "user_anki_progress"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word = Column(String(100), nullable=False, index=True)
    language = Column(String(10), nullable=False, default="ko")
    deck_name = Column(String(100), nullable=True)

    # FSRS/Anki scheduling parameters
    due = Column(DateTime, nullable=True)
    stability = Column(Float, nullable=False, default=0)
    difficulty = Column(Float, nullable=False, default=0)
    elapsed_days = Column(Integer, nullable=False, default=0)
    scheduled_days = Column(Integer, nullable=False, default=0)
    reps = Column(Integer, nullable=False, default=0)
    lapses = Column(Integer, nullable=False, default=0)
    state = Column(Integer, nullable=False, default=0)  # 0=New, 1=Learning, 2=Review, 3=Relearning
    last_review = Column(DateTime, nullable=True)

    # Track if this progress has been applied to a flashcard
    applied_to_flashcard = Column(Integer, nullable=True)  # FK to flashcard id when applied

    # Relationships
    user = relationship("User", backref="anki_progress")

    # One entry per word per user per language
    __table_args__ = (
        UniqueConstraint('user_id', 'word', 'language', name='uq_user_anki_word_lang'),
    )
