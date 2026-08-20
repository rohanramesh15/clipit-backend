from sqlalchemy import Column, Integer, String, Float, ForeignKey, UniqueConstraint, Index
from .base import BaseModel


class UserLanguageProfile(BaseModel):
    """
    Per-user, per-language proficiency snapshot. Two separate scores:
      - recognition_score: derived from FSRS card stability + count (passive)
      - production_score: derived from end-of-session rubrics (active)

    These typically diverge by 1-2 levels for active learners. Tracking both
    is the foundation for adaptive difficulty and motivational metrics.
    """
    __tablename__ = "user_language_profile"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    language = Column(String(10), nullable=False)
    self_rated_level = Column(String(8), nullable=True)
    recognition_score = Column(Float, nullable=True)
    production_score = Column(Float, nullable=True)
    session_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint('user_id', 'language', name='uq_user_language_profile'),
    )
