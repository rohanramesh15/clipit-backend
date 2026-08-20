"""
Service for auto-upgrading TTS-only flashcards with video context.

When a user watches a video and words from their vocab list appear in the subtitles,
this service upgrades those TTS cards to have video context (sentence, timestamp, etc).
"""
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_word import UserVocabularyWord
from app.services.subtitle_service import load_cached_subtitles, load_cached_subtitles_ukrainian, load_cached_subtitles_english
from app.api.routes.netflix import load_cached_netflix_subtitles


def get_user_vocab_words_set(user_id: int, db: Session, language: str = 'ko') -> dict:
    """
    Get user's vocabulary words as a dict mapping word -> translation.
    """
    words = (
        db.query(UserVocabularyWord)
        .join(UserVocabularyList)
        .filter(
            UserVocabularyList.user_id == user_id,
            UserVocabularyList.language == language
        )
        .all()
    )
    return {w.word: w.translation for w in words}


def find_sentence_for_word_simple(word: str, subtitles: list, language: str = 'ko') -> Optional[dict]:
    """
    Simple sentence finder that checks if word appears in subtitles.
    Returns sentence data if found, None otherwise.
    """
    # Korean particles that can follow a noun
    particles = ['이', '가', '을', '를', '은', '는', '의', '에', '도', '만', '와', '과', '로', '으로', '에서', '에게', '부터', '까지', '요', '야', ' ', ',', '.', '?', '!']

    # Get the right subtitle key based on language
    if language == 'uk':
        sub_key = 'ukrainian'
    elif language == 'en':
        sub_key = 'english'
    else:
        sub_key = 'korean'

    for sub in subtitles:
        text = sub.get(sub_key, '')
        if not text or word not in text:
            continue

        # Check for valid word boundary (exact match or followed by particle)
        idx = text.find(word)
        while idx != -1:
            end_idx = idx + len(word)
            # Check if it's a valid match (end of text or followed by particle/space)
            if end_idx == len(text) or any(text[end_idx:].startswith(p) for p in particles):
                start = sub.get('start', 0)
                end = sub.get('end', start + 5)
                return {
                    'sentence': text,
                    'sentence_translation': sub.get('english', ''),
                    'timestamp': int(start),
                    'end_timestamp': int(end) + 1,
                }
            idx = text.find(word, end_idx)

    # Fallback: any substring match
    for sub in subtitles:
        text = sub.get(sub_key, '')
        if text and word in text:
            start = sub.get('start', 0)
            end = sub.get('end', start + 5)
            return {
                'sentence': text,
                'sentence_translation': sub.get('english', ''),
                'timestamp': int(start),
                'end_timestamp': int(end) + 1,
            }

    return None


def auto_upgrade_tts_cards(
    user_id: int,
    video_id: str,
    language: str,
    db: Session
) -> List[str]:
    """
    Auto-upgrade TTS-only flashcards with video context.

    Checks if any words from the user's vocab lists appear in the video's subtitles.
    If they do, and the user has a TTS-only card for that word, upgrade it with video context.

    Args:
        user_id: The user's ID
        video_id: The video ID (YouTube or Netflix)
        language: Language code ('ko' or 'uk')
        db: Database session

    Returns:
        List of words that were upgraded
    """
    upgraded_words = []

    # Get user's vocabulary words
    user_vocab = get_user_vocab_words_set(user_id, db, language)
    if not user_vocab:
        return upgraded_words

    # Load subtitles for the video (YouTube or Netflix)
    if video_id.startswith('netflix_'):
        subtitle_data = load_cached_netflix_subtitles(video_id, language)
    elif language == 'uk':
        subtitle_data = load_cached_subtitles_ukrainian(video_id)
    elif language == 'en':
        subtitle_data = load_cached_subtitles_english(video_id)
    else:
        subtitle_data = load_cached_subtitles(video_id)

    if not subtitle_data:
        return upgraded_words

    subtitles = subtitle_data.get('subtitles', [])
    if not subtitles:
        return upgraded_words

    # Check each vocab word to see if it appears in subtitles
    for word, translation in user_vocab.items():
        # Find if word appears in subtitles
        sentence_data = find_sentence_for_word_simple(word, subtitles, language)
        if not sentence_data:
            continue

        # Check if user has a TTS-only card for this word
        card = db.query(UserFlashcardProgress).filter(
            UserFlashcardProgress.user_id == user_id,
            UserFlashcardProgress.word == word,
            UserFlashcardProgress.language == language,
            UserFlashcardProgress.card_type == 'tts'  # Only upgrade TTS cards
        ).first()

        if not card:
            continue

        # Upgrade the card with video context
        card.video_id = video_id
        card.card_type = 'video'
        # Note: sentence/timestamp stored separately or in frontend state
        # The key upgrade is setting video_id and card_type

        upgraded_words.append(word)

    if upgraded_words:
        db.commit()
        print(f"[AUTO-UPGRADE] User {user_id}: Upgraded {len(upgraded_words)} TTS cards with video context from {video_id}")

    return upgraded_words
