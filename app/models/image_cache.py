from sqlalchemy import Column, String, Text, DateTime
from datetime import datetime
from app.core.database import Base


class ImageCache(Base):
    """Store images (thumbnails, screenshots) in database for persistence."""
    __tablename__ = "image_cache"

    key = Column(String(255), primary_key=True, index=True)  # e.g., "thumbnail_netflix_12345" or "screenshot_netflix_12345_60"
    image_data = Column(Text, nullable=False)  # base64 encoded image
    mime_type = Column(String(50), default="image/jpeg")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
