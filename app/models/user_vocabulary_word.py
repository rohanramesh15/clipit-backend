from sqlalchemy import Column, String, Integer, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from .base import BaseModel


class UserVocabularyWord(BaseModel):
    """Individual word entry from a user-uploaded vocabulary list"""
    __tablename__ = "user_vocabulary_words"

    list_id = Column(Integer, ForeignKey("user_vocabulary_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    word = Column(String(100), nullable=False, index=True)
    translation = Column(String(500), nullable=False)
    example = Column(String(1000), nullable=True)  # Example sentence in target language
    example_translation = Column(String(1000), nullable=True)  # Example sentence translation
    sort_order = Column(Integer, default=0)
    prefer_tts = Column(Boolean, nullable=False, default=False)  # User prefers TTS over video

    # Relationships
    vocabulary_list = relationship("UserVocabularyList", back_populates="words")

    # Prevent duplicate words in the same list
    __table_args__ = (
        UniqueConstraint('list_id', 'word', name='uq_vocab_list_word'),
    )
