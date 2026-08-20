"""
Image storage service - stores images in database for persistence.
"""
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.image_cache import ImageCache


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_image(key: str, image_data: str, mime_type: str = "image/jpeg") -> bool:
    """Save an image to the database. image_data should be base64 encoded."""
    db = SessionLocal()
    try:
        # Check if exists, update if so
        existing = db.query(ImageCache).filter(ImageCache.key == key).first()
        if existing:
            existing.image_data = image_data
            existing.mime_type = mime_type
        else:
            img = ImageCache(key=key, image_data=image_data, mime_type=mime_type)
            db.add(img)
        db.commit()
        return True
    except Exception as e:
        print(f"[ImageStore] Failed to save image {key}: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_image(key: str) -> tuple[str, str] | None:
    """Get an image from the database. Returns (base64_data, mime_type) or None."""
    db = SessionLocal()
    try:
        img = db.query(ImageCache).filter(ImageCache.key == key).first()
        if img:
            return (img.image_data, img.mime_type)
        return None
    finally:
        db.close()


def delete_image(key: str) -> bool:
    """Delete an image from the database."""
    db = SessionLocal()
    try:
        result = db.query(ImageCache).filter(ImageCache.key == key).delete()
        db.commit()
        return result > 0
    finally:
        db.close()
