"""
Suggestion service for the chat opening screen.

Builds 3-4 chips that give the user something concrete to start talking about,
mixed across:
  - "video" — most recently watched Spanish video
  - "srs"   — practice words recently added to their deck
  - "scenario" — drawn from their onboarding motivation (with a fallback)
  - "wildcard" — generic conversation starters

The user can also ignore all chips and start typing freely.
"""

from dataclasses import dataclass, asdict
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.user_video_watch import UserVideoWatch
from app.models.video import TrackedVideo
from app.models.user_flashcard_progress import UserFlashcardProgress


@dataclass
class Suggestion:
    id: str
    type: str           # 'video' | 'scenario' | 'srs' | 'wildcard'
    label: str          # User-facing display text
    seed_label: str     # Sent back to the chat session as the seed
    seed_video_id: Optional[str] = None


_SCENARIOS_BY_MOTIVATION = {
    "travel": [
        ("Plan a trip to Mexico City", "scenario_travel_mexico"),
        ("Practice ordering at a café", "scenario_cafe"),
        ("Ask for directions", "scenario_directions"),
    ],
    "family": [
        ("Catch up with a family member", "scenario_family_catchup"),
        ("Describe your week", "scenario_describe_week"),
        ("Talk about a childhood memory", "scenario_childhood"),
    ],
    "work": [
        ("Introduce yourself professionally", "scenario_intro_work"),
        ("Talk about your job", "scenario_job"),
        ("Schedule a meeting", "scenario_meeting"),
    ],
    "pop_culture": [
        ("Talk about a TV show you love", "scenario_tv_show"),
        ("Recommend a movie", "scenario_movie"),
        ("Debate a hot take", "scenario_debate"),
    ],
    "romance": [
        ("Plan a date", "scenario_date"),
        ("Get to know someone new", "scenario_new_person"),
        ("Talk about a relationship moment", "scenario_relationship"),
    ],
    "heritage": [
        ("Talk about family traditions", "scenario_traditions"),
        ("Describe a holiday meal", "scenario_holiday_meal"),
        ("Share something from your background", "scenario_background"),
    ],
}

_DEFAULT_SCENARIOS = [
    ("Practice ordering at a café", "scenario_cafe"),
    ("Talk about your weekend", "scenario_weekend"),
    ("Describe a movie you saw", "scenario_movie"),
]

_WILDCARDS = [
    ("Surprise me with a topic", "wildcard_surprise"),
    ("Just chat — anything goes", "wildcard_free"),
]


def _most_recent_video_for_lang(db: Session, user_id: int, language: str) -> Optional[TrackedVideo]:
    if language == "en":
        cond = TrackedVideo.has_english.is_(True)
    else:
        return None
    return (
        db.query(TrackedVideo)
        .join(UserVideoWatch, UserVideoWatch.video_id == TrackedVideo.video_id)
        .filter(UserVideoWatch.user_id == user_id, cond)
        .order_by(desc(UserVideoWatch.watched_at))
        .first()
    )


def _recent_srs_words(db: Session, user_id: int, language: str, limit: int = 5) -> List[str]:
    rows = (
        db.query(UserFlashcardProgress.word)
        .filter(
            UserFlashcardProgress.user_id == user_id,
            UserFlashcardProgress.language == language,
        )
        .order_by(desc(UserFlashcardProgress.created_at))
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0]]


def build_suggestions(
    db: Session,
    user_id: int,
    motivation: Optional[str] = None,
    language: str = "es",
) -> List[Suggestion]:
    """
    Return 3-4 starter chips for this user. Order: video, scenario, srs, wildcard.
    """
    suggestions: List[Suggestion] = []

    # 1. Most recent target-language video
    video = _most_recent_video_for_lang(db, user_id, language)
    if video:
        episode_suffix = ""
        if video.season and video.episode:
            episode_suffix = f" (S{video.season}E{video.episode})"
        suggestions.append(Suggestion(
            id=f"video_{video.video_id}",
            type="video",
            label=f"Talk about {video.title}{episode_suffix}",
            seed_label=f"the video '{video.title}'",
            seed_video_id=video.video_id,
        ))

    # 2. Scenario tied to motivation (fall back to defaults)
    scenarios = _SCENARIOS_BY_MOTIVATION.get(motivation or "", _DEFAULT_SCENARIOS)
    if scenarios:
        label, sid = scenarios[0]
        suggestions.append(Suggestion(
            id=sid,
            type="scenario",
            label=label,
            seed_label=label.lower(),
        ))

    # 3. SRS-anchored chip (only if they have recent Spanish cards)
    recent = _recent_srs_words(db, user_id, language)
    if recent:
        sample = recent[:3]
        suggestions.append(Suggestion(
            id="srs_recent",
            type="srs",
            label=f"Practice words you're learning",
            seed_label=f"using words like: {', '.join(sample)}",
        ))

    # 4. Wildcard fallback
    label, sid = _WILDCARDS[0]
    suggestions.append(Suggestion(
        id=sid,
        type="wildcard",
        label=label,
        seed_label="a topic of your choice",
    ))

    # Trim to 4 max
    return suggestions[:4]


def suggestions_to_dicts(suggestions: List[Suggestion]) -> List[dict]:
    return [asdict(s) for s in suggestions]
