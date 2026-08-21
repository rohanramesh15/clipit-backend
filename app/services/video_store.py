import time
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.video import TrackedVideo
from app.models.user_video_watch import UserVideoWatch


def _db():
    return SessionLocal()


def add_video(video_id: str, title: str, season: int = None, episode: int = None, episode_title: str = None) -> bool:
    """Insert video if not already tracked. Returns True if newly added."""
    db = _db()
    try:
        existing = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
        if existing:
            # Update episode info if provided and not already set
            if season and not existing.season:
                existing.season = season
            if episode and not existing.episode:
                existing.episode = episode
            if episode_title and not existing.episode_title:
                existing.episode_title = episode_title
            db.commit()
            return False
        video = TrackedVideo(
            video_id=video_id,
            title=title,
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            tracked_at=time.time(),
            season=season,
            episode=episode,
            episode_title=episode_title,
        )
        db.add(video)
        db.commit()
        return True
    finally:
        db.close()


def get_all_videos() -> list[dict]:
    db = _db()
    try:
        rows = db.query(TrackedVideo).order_by(TrackedVideo.tracked_at.desc()).all()
        return [
            {
                "video_id": r.video_id,
                "title": r.title,
                "youtube_url": r.youtube_url,
                "tracked_at": r.tracked_at,
                "has_korean": r.has_korean,
                "has_ukrainian": r.has_ukrainian,
                "has_english": r.has_english,
                "season": r.season,
                "episode": r.episode,
                "episode_title": r.episode_title,
            }
            for r in rows
        ]
    finally:
        db.close()


def update_korean_status(video_id: str, has_korean: bool) -> None:
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"has_korean": has_korean}
        )
        db.commit()
    finally:
        db.close()


def update_video_title(video_id: str, title: str) -> bool:
    """Update the title for a video. Only updates if current title is generic."""
    db = _db()
    try:
        video = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
        if not video:
            return False
        # Only update if current title is generic/unknown
        generic_titles = ['Unknown', 'Netflix Video', 'Netflix', '']
        if video.title in generic_titles or video.title is None:
            video.title = title
            db.commit()
            return True
        return False
    finally:
        db.close()


def save_subtitles(video_id: str, subtitle_data: dict) -> None:
    """Store Korean subtitle JSON for the tracked video."""
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"subtitles": subtitle_data}
        )
        db.commit()
    finally:
        db.close()


def get_subtitles(video_id: str) -> dict | None:
    """Retrieve stored Korean subtitles. Returns None if not saved yet."""
    db = _db()
    try:
        row = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
        if row and row.subtitles:
            return row.subtitles
        return None
    finally:
        db.close()


def save_subtitles_ukrainian(video_id: str, subtitle_data: dict) -> None:
    """Store Ukrainian subtitle JSON in its own column for the tracked video."""
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"subtitles_uk": subtitle_data}
        )
        db.commit()
    finally:
        db.close()


def get_subtitles_ukrainian(video_id: str) -> dict | None:
    """Retrieve stored Ukrainian subtitles. Returns None if not saved yet."""
    db = _db()
    try:
        row = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
        if row and row.subtitles_uk:
            return row.subtitles_uk
        return None
    finally:
        db.close()


def get_filtered_videos() -> list[dict]:
    """Return only videos confirmed to have Korean vocabulary."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_korean == True)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [
            {
                "video_id": r.video_id,
                "title": r.title,
                "youtube_url": r.youtube_url,
                "tracked_at": r.tracked_at,
                "has_korean": r.has_korean,
                "has_ukrainian": r.has_ukrainian,
                "has_english": r.has_english,
                "season": r.season,
                "episode": r.episode,
                "episode_title": r.episode_title,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_unchecked_videos() -> list[dict]:
    """Return videos where Korean subtitle availability hasn't been checked yet."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_korean == None)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [{"video_id": r.video_id, "title": r.title, "tracked_at": r.tracked_at} for r in rows]
    finally:
        db.close()


def update_ukrainian_status(video_id: str, has_ukrainian: bool) -> None:
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"has_ukrainian": has_ukrainian}
        )
        db.commit()
    finally:
        db.close()



def update_english_status(video_id: str, has_english: bool) -> None:
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"has_english": has_english}
        )
        db.commit()
    finally:
        db.close()


def get_ukrainian_filtered_videos() -> list[dict]:
    """Return only videos confirmed to have Ukrainian vocabulary."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_ukrainian == True)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [
            {
                "video_id": r.video_id,
                "title": r.title,
                "youtube_url": r.youtube_url,
                "tracked_at": r.tracked_at,
                "has_korean": r.has_korean,
                "has_ukrainian": r.has_ukrainian,
                "has_english": r.has_english,
                "season": r.season,
                "episode": r.episode,
                "episode_title": r.episode_title,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_unchecked_ukrainian_videos() -> list[dict]:
    """Return videos where Ukrainian subtitle availability hasn't been checked yet."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_ukrainian == None)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [{"video_id": r.video_id, "title": r.title, "tracked_at": r.tracked_at} for r in rows]
    finally:
        db.close()


def get_english_filtered_videos() -> list[dict]:
    """Return only videos confirmed to have English vocabulary."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_english == True)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [
            {
                "video_id": r.video_id,
                "title": r.title,
                "youtube_url": r.youtube_url,
                "tracked_at": r.tracked_at,
                "has_korean": r.has_korean,
                "has_ukrainian": r.has_ukrainian,
                "has_english": r.has_english,
                "season": r.season,
                "episode": r.episode,
                "episode_title": r.episode_title,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_unchecked_english_videos() -> list[dict]:
    """Return videos where English subtitle availability hasn't been checked yet."""
    db = _db()
    try:
        rows = (
            db.query(TrackedVideo)
            .filter(TrackedVideo.has_english == None)
            .order_by(TrackedVideo.tracked_at.desc())
            .all()
        )
        return [{"video_id": r.video_id, "title": r.title, "tracked_at": r.tracked_at} for r in rows]
    finally:
        db.close()


def update_video_duration(video_id: str, duration_seconds: int) -> None:
    """Update the duration of a video in seconds."""
    db = _db()
    try:
        db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).update(
            {"duration_seconds": duration_seconds}
        )
        db.commit()
    finally:
        db.close()


def add_user_watch(db: Session, user_id: int, video_id: str, watched_at: float) -> None:
    """Record that a user has watched a video. Ignores if already tracked (unique constraint)."""
    existing = db.query(UserVideoWatch).filter(
        UserVideoWatch.user_id == user_id,
        UserVideoWatch.video_id == video_id,
    ).first()
    if not existing:
        watch = UserVideoWatch(user_id=user_id, video_id=video_id, watched_at=watched_at)
        db.add(watch)
        db.commit()


def get_user_videos(db: Session, user_id: int) -> list[dict]:
    """Return all videos the user has tracked, joined with tracked_videos metadata."""
    watches = db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).all()
    if not watches:
        return []
    video_ids = [w.video_id for w in watches]
    watch_times = {w.video_id: w.watched_at for w in watches}
    rows = (
        db.query(TrackedVideo)
        .filter(TrackedVideo.video_id.in_(video_ids))
        .all()
    )
    return [
        {
            "video_id": r.video_id,
            "title": r.title,
            "youtube_url": r.youtube_url,
            "tracked_at": watch_times.get(r.video_id, r.tracked_at),
            "has_korean": r.has_korean,
            "has_ukrainian": r.has_ukrainian,
            "has_english": r.has_english,
            "season": r.season,
            "episode": r.episode,
            "episode_title": r.episode_title,
        }
        for r in sorted(rows, key=lambda r: watch_times.get(r.video_id, 0), reverse=True)
    ]


def get_user_filtered_videos(db: Session, user_id: int, lang: str = "ko") -> list[dict]:
    """
    Return a user's videos that have or might have subtitles in the target language.
    Shows videos where the language status is True or NULL (unchecked/processing).
    Only excludes videos explicitly marked as False (definitely no subtitles).
    """
    watches = db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).all()
    if not watches:
        return []
    video_ids = [w.video_id for w in watches]
    watch_times = {w.video_id: w.watched_at for w in watches}
    query = db.query(TrackedVideo).filter(TrackedVideo.video_id.in_(video_ids))
    # Filter to show videos with language = True OR NULL (not False)
    if lang == "uk":
        query = query.filter((TrackedVideo.has_ukrainian == True) | (TrackedVideo.has_ukrainian == None))
    elif lang == "en":
        query = query.filter((TrackedVideo.has_english == True) | (TrackedVideo.has_english == None))
    else:
        query = query.filter((TrackedVideo.has_korean == True) | (TrackedVideo.has_korean == None))
    rows = query.all()
    return [
        {
            "video_id": r.video_id,
            "title": r.title,
            "youtube_url": r.youtube_url,
            "tracked_at": watch_times.get(r.video_id, r.tracked_at),
            "has_korean": r.has_korean,
            "has_ukrainian": r.has_ukrainian,
            "has_english": r.has_english,
            "season": r.season,
            "episode": r.episode,
            "episode_title": r.episode_title,
        }
        for r in sorted(rows, key=lambda r: watch_times.get(r.video_id, 0), reverse=True)
    ]


def get_user_building_videos(db: Session, user_id: int, lang: str = "ko") -> list[dict]:
    """
    Return Netflix videos that are 'building' - tracked but no subtitles processed yet.
    These are videos where the user should keep watching while ClipIt builds the deck.
    """
    watches = db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).all()
    if not watches:
        return []
    video_ids = [w.video_id for w in watches]
    watch_times = {w.video_id: w.watched_at for w in watches}

    # Only get Netflix videos that don't have subtitles yet
    query = db.query(TrackedVideo).filter(
        TrackedVideo.video_id.in_(video_ids),
        TrackedVideo.video_id.like('netflix_%')  # Only Netflix videos
    )
    if lang == "uk":
        query = query.filter(
            (TrackedVideo.has_ukrainian == None) | (TrackedVideo.has_ukrainian == False)
        )
    elif lang == "en":
        query = query.filter(
            (TrackedVideo.has_english == None) | (TrackedVideo.has_english == False)
        )
    else:
        query = query.filter(
            (TrackedVideo.has_korean == None) | (TrackedVideo.has_korean == False)
        )
    rows = query.all()
    return [
        {
            "video_id": r.video_id,
            "title": r.title,
            "tracked_at": watch_times.get(r.video_id, r.tracked_at),
            "season": r.season,
            "episode": r.episode,
            "episode_title": r.episode_title,
            "building": True,
        }
        for r in sorted(rows, key=lambda r: watch_times.get(r.video_id, 0), reverse=True)
    ]


def get_user_unchecked_videos(db: Session, user_id: int, lang: str = "ko") -> list[dict]:
    """Return user's videos where subtitle availability for the given lang hasn't been checked."""
    watches = db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).all()
    if not watches:
        return []
    video_ids = [w.video_id for w in watches]
    query = db.query(TrackedVideo).filter(TrackedVideo.video_id.in_(video_ids))
    if lang == "uk":
        query = query.filter(TrackedVideo.has_ukrainian == None)
    elif lang == "en":
        query = query.filter(TrackedVideo.has_english == None)
    else:
        query = query.filter(TrackedVideo.has_korean == None)
    rows = query.all()
    return [{"video_id": r.video_id, "title": r.title, "tracked_at": r.tracked_at} for r in rows]


def get_total_watch_time(db: Session, user_id: int, lang: str = "ko") -> dict:
    """Get total actual watch time stats for a user's videos in the target language."""
    # Get all user's video watches with their watch times
    watches = db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).all()
    if not watches:
        return {
            "total_seconds": 0,
            "total_hours": 0.0,
            "video_count": 0,
            "videos_with_watch_time": 0,
        }

    video_ids = [w.video_id for w in watches]
    watch_times = {w.video_id: w.watch_time_seconds or 0 for w in watches}

    # Filter to videos in the target language
    query = db.query(TrackedVideo).filter(TrackedVideo.video_id.in_(video_ids))
    if lang == "uk":
        query = query.filter(TrackedVideo.has_ukrainian == True)
    elif lang == "en":
        query = query.filter(TrackedVideo.has_english == True)
    else:
        query = query.filter(TrackedVideo.has_korean == True)
    filtered_videos = query.all()

    # Sum actual watch time for filtered videos
    total_seconds = sum(watch_times.get(v.video_id, 0) for v in filtered_videos)
    video_count = len(filtered_videos)
    videos_with_watch_time = sum(1 for v in filtered_videos if watch_times.get(v.video_id, 0) > 0)

    return {
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600, 1),
        "video_count": video_count,
        "videos_with_watch_time": videos_with_watch_time,
    }


def delete_user_video(db: Session, user_id: int, video_id: str) -> bool:
    """Remove a video from a user's watch history. Returns True if deleted."""
    deleted = (
        db.query(UserVideoWatch)
        .filter(UserVideoWatch.user_id == user_id, UserVideoWatch.video_id == video_id)
        .delete()
    )
    db.commit()
    return deleted > 0


def add_watch_time(db: Session, user_id: int, video_id: str, seconds: int) -> int:
    """Add seconds to the user's accumulated watch time for a video. Returns total."""
    watch = db.query(UserVideoWatch).filter(
        UserVideoWatch.user_id == user_id,
        UserVideoWatch.video_id == video_id,
    ).first()
    if watch:
        watch.watch_time_seconds = (watch.watch_time_seconds or 0) + seconds
        db.commit()
        return watch.watch_time_seconds
    # If no watch record exists, create one with current time
    watch = UserVideoWatch(
        user_id=user_id,
        video_id=video_id,
        watched_at=time.time(),
        watch_time_seconds=seconds,
    )
    db.add(watch)

    # Also ensure TrackedVideo exists (in case initial tracking failed)
    tracked = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
    if not tracked:
        # Determine URL format based on video_id prefix
        if video_id.startswith('netflix_'):
            url = f"https://www.netflix.com/watch/{video_id.replace('netflix_', '')}"
        else:
            url = f"https://www.youtube.com/watch?v={video_id}"
        tracked = TrackedVideo(
            video_id=video_id,
            title="Unknown",
            youtube_url=url,
            tracked_at=time.time(),
        )
        db.add(tracked)

    db.commit()
    return seconds


def get_video_by_id(video_id: str) -> dict | None:
    """Get a single video by its ID."""
    db = _db()
    try:
        row = db.query(TrackedVideo).filter(TrackedVideo.video_id == video_id).first()
        if not row:
            return None
        return {
            "video_id": row.video_id,
            "title": row.title,
            "youtube_url": row.youtube_url,
            "tracked_at": row.tracked_at,
            "has_korean": row.has_korean,
            "has_ukrainian": row.has_ukrainian,
            "season": row.season,
            "episode": row.episode,
            "episode_title": row.episode_title,
            "duration_seconds": row.duration_seconds,
        }
    finally:
        db.close()
