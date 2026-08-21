"""
Netflix subtitle handling routes.
Receives subtitles captured by the Chrome extension and stores them for vocabulary extraction.
"""
import json
import base64
from pathlib import Path
from typing import List, Set
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import Response
from app.core.config import settings
from app.services.video_store import update_video_duration, update_korean_status, update_ukrainian_status
from app.services.vocab_service import load_frequency_map, is_common_particle
from app.services.korean_tokenizer import extract_korean_words
from app.services.ukrainian_tokenizer import extract_ukrainian_words
from app.services.image_store import save_image, get_image

router = APIRouter()

# Cache frequency maps in memory
_FREQUENCY_MAP_KO: dict | None = None
_FREQUENCY_MAP_UK: dict | None = None


def get_frequency_map_cached(lang: str = 'ko') -> dict:
    """Get cached frequency map for a language."""
    global _FREQUENCY_MAP_KO, _FREQUENCY_MAP_UK
    if lang == 'uk':
        if _FREQUENCY_MAP_UK is None:
            _FREQUENCY_MAP_UK = load_frequency_map('uk')
        return _FREQUENCY_MAP_UK
    else:
        if _FREQUENCY_MAP_KO is None:
            _FREQUENCY_MAP_KO = load_frequency_map('ko')
        return _FREQUENCY_MAP_KO


def normalize_timestamp(ts) -> float:
    """
    Convert Netflix tick format to seconds if needed.
    Netflix uses 10,000,000 ticks per second.
    """
    if ts is None:
        return 0.0
    ts = float(ts)
    # If timestamp is larger than ~28 hours in seconds, it's probably in ticks
    if ts > 100000:
        return ts / 10000000
    return ts


def extract_keyword_timestamps(subtitles: List[dict], language: str) -> List[int]:
    """
    Identify which subtitle timestamps contain vocabulary words.
    Returns list of timestamps (in seconds) where keywords appear.
    """
    frequency_map = get_frequency_map_cached(language)
    if language == 'uk':
        extract_fn = extract_ukrainian_words
        text_key = 'ukrainian'
    else:
        extract_fn = extract_korean_words
        text_key = 'korean'

    keyword_timestamps: List[int] = []

    for sub in subtitles:
        text = sub.get(text_key, '')
        if not text:
            continue

        words = extract_fn(text)
        for word in words:
            rank = frequency_map.get(word)
            if rank is not None and not is_common_particle(word, rank, language):
                # This subtitle contains a keyword - normalize timestamp to seconds
                raw_ts = sub.get('start', 0)
                timestamp = int(normalize_timestamp(raw_ts))
                keyword_timestamps.append(timestamp)
                break  # Only need one keyword per subtitle

    # Remove duplicates and sort
    return sorted(set(keyword_timestamps))

# Cache directory for Netflix subtitles
NETFLIX_CACHE_DIR = Path(settings.SUBTITLES_CACHE_DIR) / "netflix"
SCREENSHOTS_DIR = NETFLIX_CACHE_DIR / "screenshots"
AUDIO_DIR = NETFLIX_CACHE_DIR / "audio"
THUMBNAILS_DIR = NETFLIX_CACHE_DIR / "thumbnails"


def get_netflix_cache_path(video_id: str, lang: str) -> Path:
    """Get cache file path for Netflix subtitles."""
    NETFLIX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return NETFLIX_CACHE_DIR / f"{video_id}_{lang}.json"


@router.post("/subtitles")
async def save_netflix_subtitles(request: dict = Body(...)):
    """
    Save Netflix subtitles captured by the Chrome extension.
    Body: { video_id, language, subtitles: [...] }
    """
    video_id = request.get("video_id")
    language = request.get("language", "ko")
    subtitles = request.get("subtitles", [])

    if not video_id or not subtitles:
        raise HTTPException(status_code=400, detail="video_id and subtitles are required")

    # Extract and save screenshots, remove from subtitle data
    # Also normalize timestamps from Netflix tick format to seconds
    screenshots_saved = 0
    clean_subtitles = []
    for sub in subtitles:
        screenshot = sub.pop("screenshot", None)

        # Normalize timestamps to seconds (Netflix may send ticks)
        if "start" in sub:
            sub["start"] = normalize_timestamp(sub["start"])
        if "end" in sub:
            sub["end"] = normalize_timestamp(sub["end"])
        if "duration" in sub:
            sub["duration"] = normalize_timestamp(sub["duration"])

        if screenshot and screenshot.startswith("data:image"):
            # Save screenshot to file
            timestamp = int(sub.get("start", 0))
            screenshot_path = save_screenshot_file(video_id, timestamp, screenshot)
            if screenshot_path:
                sub["screenshot_path"] = screenshot_path
                screenshots_saved += 1
        clean_subtitles.append(sub)

    # Build subtitle data structure matching YouTube format
    data = {
        "video_id": video_id,
        "platform": "netflix",
        "total_subtitles": len(clean_subtitles),
        "has_korean": language == "ko",
        "has_ukrainian": language == "uk",
        "subtitles": clean_subtitles,
    }

    # Save to cache
    cache_file = get_netflix_cache_path(video_id, language)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # The local cache is only a speed optimization. Fly machines have separate
    # ephemeral filesystems, so retain the source payload in Postgres as well
    # or a later Home request can no longer mine an already watched episode.
    try:
        from app.services.video_store import save_subtitles, save_subtitles_ukrainian
        if language == "uk":
            persisted = save_subtitles_ukrainian(video_id, data)
        else:
            persisted = save_subtitles(video_id, data)
        if not persisted:
            raise RuntimeError(f"tracked video {video_id} does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist Netflix subtitles: {e}")

    # Calculate and save video duration from last subtitle
    if clean_subtitles:
        last_sub = clean_subtitles[-1]
        duration_seconds = int(last_sub.get("end", last_sub["start"] + last_sub.get("duration", 5)))
        try:
            update_video_duration(video_id, duration_seconds)
        except Exception:
            pass

    # Mark video as having target-language subtitles so it appears in watch history
    try:
        if language == "ko":
            update_korean_status(video_id, True)
        elif language == "uk":
            update_ukrainian_status(video_id, True)
        print(f"[Netflix] Marked {video_id} as having {language} subtitles")
    except Exception as e:
        print(f"[Netflix] Failed to update language status: {e}")

    # Identify keyword timestamps for targeted screenshot capture
    keyword_timestamps = extract_keyword_timestamps(clean_subtitles, language)

    return {
        "status": "ok",
        "video_id": video_id,
        "language": language,
        "subtitle_count": len(clean_subtitles),
        "screenshots_saved": screenshots_saved,
        "keyword_timestamps": keyword_timestamps,
    }


def save_screenshot_file(video_id: str, timestamp: int, data_url: str) -> str | None:
    """Save a screenshot from data URL to database, return the key."""
    try:
        # Extract base64 data from data URL
        # Format: data:image/jpeg;base64,/9j/4AAQ...
        if "," not in data_url:
            return None
        header, b64_data = data_url.split(",", 1)
        mime_type = "image/jpeg" if "jpeg" in header else "image/png"

        # Save to database
        key = f"screenshot_{video_id}_{timestamp}"
        success = save_image(key, b64_data, mime_type)

        if success:
            return key
        return None
    except Exception as e:
        print(f"[Deadbird] Failed to save screenshot: {e}")
        return None


@router.get("/subtitles/{video_id}")
async def get_netflix_subtitles(video_id: str, lang: str = "ko"):
    """
    Get cached Netflix subtitles.
    """
    cache_file = get_netflix_cache_path(video_id, lang)

    if not cache_file.exists():
        raise HTTPException(status_code=404, detail=f"No {lang} subtitles found for {video_id}")

    with open(cache_file, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/screenshot")
async def save_screenshot(request: dict = Body(...)):
    """
    Save a single screenshot from the extension.
    Body: { video_id, timestamp, data_url }
    """
    video_id = request.get("video_id")
    timestamp = request.get("timestamp")
    data_url = request.get("data_url")

    if not video_id or timestamp is None or not data_url:
        raise HTTPException(status_code=400, detail="video_id, timestamp, and data_url required")

    # Extract base64 data from data URL
    if "," in data_url:
        header, b64_data = data_url.split(",", 1)
        mime_type = "image/jpeg" if "jpeg" in header else "image/png"
    else:
        b64_data = data_url
        mime_type = "image/jpeg"

    # Save to database
    key = f"screenshot_{video_id}_{int(timestamp)}"
    success = save_image(key, b64_data, mime_type)

    if success:
        print(f"[Netflix] Screenshot saved to DB: {key}")

    return {"status": "ok", "saved": success}


@router.api_route("/screenshot/{video_id}/{timestamp}", methods=["GET", "HEAD"])
async def get_netflix_screenshot(video_id: str, timestamp: int):
    """
    Get a screenshot for a specific video and timestamp.
    Also checks nearby timestamps (within 3 seconds) for a match.
    """
    # Check exact timestamp and nearby (within 3 seconds)
    for offset in range(0, 4):
        for t in [timestamp + offset, timestamp - offset]:
            if t < 0:
                continue
            key = f"screenshot_{video_id}_{t}"
            result = get_image(key)
            if result:
                b64_data, mime_type = result
                image_bytes = base64.b64decode(b64_data)
                return Response(content=image_bytes, media_type=mime_type)

    raise HTTPException(status_code=404, detail="Screenshot not found")


def save_audio_file(video_id: str, timestamp: int, audio_data: str, mime_type: str) -> str | None:
    """Save audio from base64 data URL to file, return the relative path."""
    try:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        # Extract base64 data from data URL
        # Format: data:audio/webm;base64,GkXfo6NChh...
        if "," in audio_data:
            _, b64_data = audio_data.split(",", 1)
        else:
            b64_data = audio_data

        # Determine file extension from mime type
        if "webm" in mime_type:
            ext = "webm"
        elif "ogg" in mime_type:
            ext = "ogg"
        elif "mp4" in mime_type:
            ext = "m4a"
        else:
            ext = "webm"  # Default

        # Create filename
        filename = f"{video_id}_{timestamp}.{ext}"
        filepath = AUDIO_DIR / filename

        # Decode and save
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64_data))

        print(f"[Deadbird] Audio saved: {filepath} ({filepath.stat().st_size} bytes)")
        return f"audio/{filename}"
    except Exception as e:
        print(f"[Deadbird] Failed to save audio: {e}")
        return None


@router.post("/audio")
async def save_audio(request: dict = Body(...)):
    """
    Save an audio clip from the extension.
    Body: { video_id, timestamp, audio_data (base64), mime_type }
    """
    video_id = request.get("video_id")
    timestamp = request.get("timestamp")
    audio_data = request.get("audio_data")
    mime_type = request.get("mime_type", "audio/webm")

    if not video_id or timestamp is None or not audio_data:
        raise HTTPException(status_code=400, detail="video_id, timestamp, and audio_data required")

    path = save_audio_file(video_id, int(timestamp), audio_data, mime_type)
    return {"status": "ok", "path": path}


@router.api_route("/audio/{video_id}/{timestamp}", methods=["GET", "HEAD"])
async def get_netflix_audio(video_id: str, timestamp: int):
    """
    Get an audio clip for a specific video and timestamp.
    Also checks nearby timestamps (within 3 seconds) for a match.
    """
    from fastapi.responses import FileResponse

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Check exact timestamp and nearby (within 3 seconds)
    for offset in range(0, 4):
        for t in [timestamp + offset, timestamp - offset]:
            if t < 0:
                continue
            for ext in ["webm", "ogg", "m4a"]:
                filepath = AUDIO_DIR / f"{video_id}_{t}.{ext}"
                if filepath.exists():
                    media_type = {
                        "webm": "audio/webm",
                        "ogg": "audio/ogg",
                        "m4a": "audio/mp4"
                    }.get(ext, "audio/webm")
                    return FileResponse(filepath, media_type=media_type)

    raise HTTPException(status_code=404, detail="Audio not found")


def save_thumbnail_file(video_id: str, data_url: str) -> str | None:
    """Save a thumbnail from data URL to file, return the relative path."""
    try:
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

        # Extract base64 data from data URL
        if "," not in data_url:
            return None
        header, b64_data = data_url.split(",", 1)

        # Determine file extension
        ext = "jpg" if "jpeg" in header else "png"

        # Create filename (just video_id, no timestamp)
        filename = f"{video_id}.{ext}"
        filepath = THUMBNAILS_DIR / filename

        # Decode and save
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64_data))

        print(f"[Deadbird] Thumbnail saved: {filepath}")
        return f"thumbnails/{filename}"
    except Exception as e:
        print(f"[Deadbird] Failed to save thumbnail: {e}")
        return None


@router.post("/thumbnail")
async def save_thumbnail(request: dict = Body(...)):
    """
    Save a thumbnail for a Netflix video.
    Body: { video_id, data_url }
    """
    video_id = request.get("video_id")
    data_url = request.get("data_url")

    if not video_id or not data_url:
        raise HTTPException(status_code=400, detail="video_id and data_url required")

    # Extract base64 data from data URL
    if "," in data_url:
        header, b64_data = data_url.split(",", 1)
        mime_type = "image/jpeg" if "jpeg" in header else "image/png"
    else:
        b64_data = data_url
        mime_type = "image/jpeg"

    # Save to database
    key = f"thumbnail_{video_id}"
    success = save_image(key, b64_data, mime_type)

    if success:
        print(f"[Netflix] Thumbnail saved to DB for: {video_id}")

    return {"status": "ok", "saved": success}


@router.api_route("/thumbnail/{video_id}", methods=["GET", "HEAD"])
async def get_netflix_thumbnail(video_id: str):
    """Get the thumbnail for a Netflix video."""
    key = f"thumbnail_{video_id}"
    result = get_image(key)

    if result:
        b64_data, mime_type = result
        image_bytes = base64.b64decode(b64_data)
        return Response(content=image_bytes, media_type=mime_type)

    raise HTTPException(status_code=404, detail="Thumbnail not found")


def load_cached_netflix_subtitles(video_id: str, lang: str = "ko") -> dict | None:
    """Load Netflix subtitles from cache, then durable database storage."""
    cache_file = get_netflix_cache_path(video_id, lang)
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Normalize timestamps in case they're in Netflix tick format
        if data and "subtitles" in data:
            for sub in data["subtitles"]:
                if "start" in sub:
                    sub["start"] = normalize_timestamp(sub["start"])
                if "end" in sub:
                    sub["end"] = normalize_timestamp(sub["end"])
                if "duration" in sub:
                    sub["duration"] = normalize_timestamp(sub["duration"])

        return data
    # Fly may route a request to a different Machine (or the original Machine
    # may have restarted), so local cache absence must not erase a watched
    # episode's vocabulary source.
    from app.services.video_store import get_subtitles, get_subtitles_ukrainian
    return get_subtitles_ukrainian(video_id) if lang == "uk" else get_subtitles(video_id)
