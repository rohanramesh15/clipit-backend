from fastapi import APIRouter, HTTPException, Query, Body
from typing import List, Optional
from pydantic import BaseModel, Field
from app.services.subtitle_service import (
    fetch_and_cache_subtitles,
    fetch_and_cache_subtitles_ukrainian,
    save_subtitles_from_extension,
)
from app.api.routes.netflix import load_cached_netflix_subtitles

router = APIRouter()


class SubtitleEntry(BaseModel):
    start: float
    duration: float
    text: str


class YouTubeSubtitlesRequest(BaseModel):
    video_id: str
    korean: List[SubtitleEntry] = Field(default_factory=list)
    ukrainian: List[SubtitleEntry] = Field(default_factory=list)
    english: List[SubtitleEntry] = Field(default_factory=list)


@router.post("/youtube/subtitles")
async def receive_youtube_subtitles(req: YouTubeSubtitlesRequest):
    """
    Receive YouTube subtitles fetched client-side by the Chrome extension.
    This bypasses YouTube's IP blocking of cloud servers.
    """
    try:
        has_korean = len(req.korean) > 0
        has_ukrainian = len(req.ukrainian) > 0
        has_english = len(req.english) > 0
        lang = 'uk' if has_ukrainian else 'ko'

        # Convert to our merged format
        merged = merge_client_subtitles(
            req.ukrainian if has_ukrainian else req.korean,
            req.english,
            lang,
        )

        # Save to the language-specific cache and update video language status.
        persisted = save_subtitles_from_extension(
            req.video_id,
            lang,
            merged,
            has_korean=has_korean,
            has_ukrainian=has_ukrainian,
        )
        if not persisted:
            raise HTTPException(status_code=500, detail="Subtitle upload could not be persisted")

        return {
            "success": True,
            "video_id": req.video_id,
            "has_korean": has_korean,
            "has_ukrainian": has_ukrainian,
            "has_english": has_english,
            "subtitle_count": len(merged),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save subtitles: {str(e)}")


def merge_client_subtitles(target_subtitles: List[SubtitleEntry], english: List[SubtitleEntry], lang: str = 'ko') -> list:
    """Merge target-language and English subtitles by timestamp."""
    target_key = 'ukrainian' if lang == 'uk' else 'korean'

    if not target_subtitles and not english:
        return []

    if target_subtitles and english:
        # Match target-language subtitles with closest English ones
        merged = []
        for target_sub in target_subtitles:
            target_start = target_sub.start
            # Find closest English subtitle
            best_match = None
            best_diff = float('inf')
            for en_sub in english:
                diff = abs(en_sub.start - target_start)
                if diff < best_diff and diff < 5:  # Within 5 seconds
                    best_diff = diff
                    best_match = en_sub

            merged.append({
                'start': target_sub.start,
                'duration': target_sub.duration,
                'end': target_sub.start + target_sub.duration,
                target_key: target_sub.text,
                'english': best_match.text if best_match else '',
            })
        return merged
    elif target_subtitles:
        return [
            {
                'start': s.start,
                'duration': s.duration,
                'end': s.start + s.duration,
                target_key: s.text,
                'english': '',
            }
            for s in target_subtitles
        ]
    else:
        return [
            {
                'start': s.start,
                'duration': s.duration,
                'end': s.start + s.duration,
                target_key: '',
                'english': s.text,
            }
            for s in english
        ]
class SubtitleUpload(BaseModel):
    video_id: str
    lang: str
    subtitles: list
    has_korean: bool = False
    has_ukrainian: bool = False


@router.get("/subtitles/{video_id}")
async def get_subtitles(video_id: str, lang: str = Query('ko')):
    """
    Get cached subtitles for a YouTube or Netflix video.
    NOTE: This endpoint now only returns cached subtitles.
    Subtitles are fetched by the extension to avoid IP blocking.
    lang: 'ko' (Korean) or 'uk' (Ukrainian). Defaults to 'ko'.
    """
    try:
        # Handle Netflix videos (prefixed with netflix_)
        if video_id.startswith('netflix_'):
            from app.services.subtitle_service import load_cached_subtitles, load_cached_subtitles_ukrainian
            data = load_cached_netflix_subtitles(video_id, lang)
            if not data:
                raise HTTPException(status_code=404, detail=f"No Netflix subtitles found for {video_id}")
            if lang == 'uk':
                target_key = 'ukrainian'
            else:
                target_key = 'korean'
            return {
                "video_id": video_id,
                "lang": lang,
                "platform": "netflix",
                "subtitles": [
                    {
                        "start": sub["start"],
                        "duration": sub.get("duration", sub.get("end", sub["start"] + 5) - sub["start"]),
                        "english": sub.get("english", ""),
                        target_key: sub.get(target_key, ""),
                    }
                    for sub in data["subtitles"]
                ]
            }

        # Handle YouTube videos - only load from cache
        from app.services.subtitle_service import load_cached_subtitles, load_cached_subtitles_ukrainian

        if lang == 'uk':
            data = load_cached_subtitles_ukrainian(video_id)
            target_key = 'ukrainian'
        else:
            data = load_cached_subtitles(video_id)
            target_key = 'korean'

        if not data:
            raise HTTPException(
                status_code=404,
                detail=f"Subtitles not cached yet. The extension will fetch and upload them automatically."
            )

        return {
            "video_id": video_id,
            "lang": lang,
            "platform": "youtube",
            "subtitles": [
                {
                    "start": sub["start"],
                    "duration": sub["duration"],
                    "english": sub["english"],
                    target_key: sub.get(target_key),
                }
                for sub in data["subtitles"]
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading subtitles: {str(e)}")


@router.post("/subtitles/upload")
async def upload_subtitles(data: SubtitleUpload):
    """
    Receive subtitles fetched by the extension.
    This endpoint stores subtitles that were fetched in the user's browser,
    avoiding YouTube IP blocking issues.
    """
    try:
        # Save subtitles using the subtitle service
        result = save_subtitles_from_extension(
            video_id=data.video_id,
            lang=data.lang,
            subtitles=data.subtitles,
            has_korean=data.has_korean,
            has_ukrainian=data.has_ukrainian,
        )
        if not result:
            raise HTTPException(status_code=500, detail="Subtitle upload could not be persisted")

        return {
            "status": "ok",
            "video_id": data.video_id,
            "lang": data.lang,
            "total_subtitles": len(data.subtitles),
            "saved": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save subtitles: {str(e)}")
