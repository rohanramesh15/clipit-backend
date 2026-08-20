from sqlalchemy import Column, String, Integer, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .base import BaseModel


class UserMinedWord(BaseModel):
    """Tracks words that have been mined from a specific video for a user.
    Used to exclude already-mined words in subsequent watches."""
    __tablename__ = "user_mined_words"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(String(100), nullable=False, index=True)  # YouTube ID or netflix_{id}
    word = Column(String(100), nullable=False, index=True)
    language = Column(String(10), nullable=False, default="ko")
    timestamp = Column(Float, nullable=True)  # Subtitle timestamp where word was mined

    # Relationships
    user = relationship("User", backref="mined_words")

    # Prevent duplicate mining of same word from same video
    __table_args__ = (
        UniqueConstraint('user_id', 'video_id', 'word', 'language', name='uq_user_video_word_lang'),
    )
