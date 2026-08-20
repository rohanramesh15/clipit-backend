from sqlalchemy import Column, String, Integer, ForeignKey
from sqlalchemy.orm import relationship
from .base import BaseModel


class UserVocabularyList(BaseModel):
    """Metadata for a user-uploaded vocabulary list (CSV file)"""
    __tablename__ = "user_vocabulary_lists"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    language = Column(String(10), nullable=False, default="ko")
    word_count = Column(Integer, default=0)

    # Relationships
    user = relationship("User", backref="vocabulary_lists")
    words = relationship("UserVocabularyWord", back_populates="vocabulary_list", cascade="all, delete-orphan")
