from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from .base import BaseModel


class UserReviewHistory(BaseModel):
    """Review log for analytics (replaces localStorage deadbird_review_history)."""
    __tablename__ = "user_review_history"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    word = Column(String, nullable=False)
    language = Column(String, nullable=False)
    rating = Column(Integer, nullable=False)       # 1=Again 2=Hard 3=Good 4=Easy
    clip_duration = Column(Float, nullable=True)
    reviewed_at = Column(DateTime, nullable=False)
