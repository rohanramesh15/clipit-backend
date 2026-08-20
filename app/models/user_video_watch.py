from sqlalchemy import Column, Integer, String, Float, UniqueConstraint, ForeignKey
from .base import BaseModel


class UserVideoWatch(BaseModel):
    """Links a user to the videos they've tracked."""
    __tablename__ = "user_video_watches"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_id = Column(String, nullable=False, index=True)  # FK → tracked_videos.video_id
    watched_at = Column(Float, nullable=False)              # Unix timestamp from extension
    watch_time_seconds = Column(Integer, default=0)         # Accumulated actual watch time

    __table_args__ = (UniqueConstraint("user_id", "video_id"),)
