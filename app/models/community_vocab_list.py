from sqlalchemy import Column, String, Integer, ForeignKey
from sqlalchemy.orm import relationship
from .base import BaseModel


class CommunityVocabList(BaseModel):
    """A vocabulary list within a community group"""
    __tablename__ = "community_vocab_lists"

    group_id = Column(Integer, ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    added_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    word_count = Column(Integer, default=0, nullable=False)

    # Relationships
    group = relationship("CommunityGroup", back_populates="vocab_lists")
    added_by_user = relationship("User", backref="community_lists_added")
    words = relationship("CommunityVocabWord", back_populates="vocab_list", cascade="all, delete-orphan")
