from sqlalchemy import Column, String, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .base import BaseModel


class DeckSettings(BaseModel):
    """User-specific settings for video decks (e.g., custom name)"""
    __tablename__ = "deck_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(String(20), nullable=False, index=True)  # YouTube video ID
    custom_name = Column(String(255), nullable=True)  # If null, use original video title

    # Ensure one settings record per user per video
    __table_args__ = (
        UniqueConstraint('user_id', 'video_id', name='uq_deck_settings_user_video'),
    )

    # Relationships
    user = relationship("User", backref="deck_settings")
