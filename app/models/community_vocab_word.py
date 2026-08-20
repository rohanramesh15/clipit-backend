from sqlalchemy import Column, String, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .base import BaseModel


class CommunityVocabWord(BaseModel):
    """A vocabulary word within a community vocab list"""
    __tablename__ = "community_vocab_words"

    list_id = Column(Integer, ForeignKey("community_vocab_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    word = Column(String(255), nullable=False)
    translation = Column(String(500), nullable=False)
    example = Column(Text, nullable=True)
    example_translation = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    added_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Unique constraint - no duplicate words in same list
    __table_args__ = (
        UniqueConstraint('list_id', 'word', name='uq_list_word'),
    )

    # Relationships
    vocab_list = relationship("CommunityVocabList", back_populates="words")
    added_by_user = relationship("User", backref="community_words_added")
