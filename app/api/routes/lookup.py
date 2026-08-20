from typing import List, Optional
from fastapi import APIRouter, HTTPException, Body, Query, Depends
from sqlalchemy.orm import Session
from app.api.routes.vocabulary import get_frequency_map
from app.api.routes.flashcards import strip_korean_particles, load_definitions
from app.services.deepl_service import translate
from app.api.deps import get_current_user_optional
from app.core.database import get_db
from app.models.user import User
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_word import UserVocabularyWord

router = APIRouter()


def get_part_of_speech(korean: str, english: str) -> str:
    """Determine part of speech from Korean word pattern and English definition."""
    english_lower = english.lower()

    # Check for adjectives first (to be X patterns)
    if english_lower.startswith('to be ') or 'to be ' in english_lower:
        return 'adjective'

    # Check for verbs (to X patterns)
    if english_lower.startswith('to '):
        return 'verb'

    # Korean patterns - verbs/adjectives end in 다
    if korean.endswith('다'):
        return 'verb'

    # Common adverb endings
    if korean.endswith('히') or korean.endswith('게') or korean.endswith('로'):
        return 'adverb'

    # Default to noun
    return 'noun'


@router.get("/dictionary")
async def get_dictionary(
    search: Optional[str] = Query(None),
    pos: Optional[str] = Query(None),
    lang: str = Query('ko'),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Get all dictionary entries with optional search and part of speech filter.
    Search matches words or English definitions.
    pos can be: noun, verb, adjective, adverb
    lang can be: 'ko' (Korean) or 'uk' (Ukrainian)

    If user is authenticated, includes their uploaded vocab words.
    """
    frequency_map = get_frequency_map(lang)
    deepl_source_lang = {'uk': 'UK', 'es': 'ES', 'en': 'EN', 'ko': 'KO'}.get(lang, 'KO')

    # Track words we've seen to avoid duplicates
    seen_words = set()
    entries = []

    # First, add user's vocab words (if authenticated)
    if current_user:
        user_vocab_words = (
            db.query(UserVocabularyWord)
            .join(UserVocabularyList)
            .filter(
                UserVocabularyList.user_id == current_user.id,
                UserVocabularyList.language == lang
            )
            .all()
        )
        for vw in user_vocab_words:
            if vw.word in seen_words:
                continue  # Skip duplicate words across lists
            rank = frequency_map.get(vw.word, 10001)
            part_of_speech = get_part_of_speech(vw.word, vw.translation) if lang == 'ko' else 'noun'
            entries.append({
                'word': vw.word,
                'english': vw.translation,
                'rank': rank,
                'pos': part_of_speech,
                'language': lang,
                'source': 'user',  # Mark as user-uploaded word
            })
            seen_words.add(vw.word)

    # For Korean, use the local definitions.json
    # For Ukrainian / Spanish, generate definitions from frequency list using DeepL
    if lang == 'ko':
        definitions = load_definitions()
        for word, english in definitions.items():
            if word in seen_words:
                continue  # Skip if already added from user vocab
            rank = frequency_map.get(word, 10001)
            part_of_speech = get_part_of_speech(word, english)
            entries.append({
                'word': word,
                'english': english,
                'rank': rank,
                'pos': part_of_speech,
                'language': lang,
                'source': 'dictionary',
            })
    else:
        # For Ukrainian / Spanish, use the frequency list and translate top words
        sorted_words = sorted(frequency_map.items(), key=lambda x: x[1])[:500]  # Top 500 words
        for word, rank in sorted_words:
            if word in seen_words:
                continue  # Skip if already added from user vocab
            english = translate(word, source_lang=deepl_source_lang) or "definition not available"
            entries.append({
                'word': word,
                'english': english,
                'rank': rank,
                'pos': 'noun',  # Default (no morphology analysis yet for non-Korean)
                'language': lang,
                'source': 'dictionary',
            })

    # Sort by frequency rank (most common first)
    entries.sort(key=lambda x: x['rank'])

    # Filter by part of speech if provided (only effective for Korean)
    if pos and lang == 'ko':
        entries = [e for e in entries if e['pos'] == pos.lower()]

    # Filter by search term if provided
    if search:
        search_lower = search.lower()
        entries = [
            e for e in entries
            if search_lower in e['word'].lower() or search_lower in e['english'].lower()
        ]

    return {
        'total': len(entries),
        'entries': entries,
        'language': lang
    }


@router.post("/lookup-words")
async def lookup_words(word_list: List[str] = Body(...), lang: str = Query('ko')):
    """
    Look up frequency rank and definition for a list of words.
    lang: 'ko' (Korean) or 'uk' (Ukrainian). Defaults to 'ko'.
    """
    if not word_list:
        raise HTTPException(status_code=400, detail="word_list cannot be empty")

    frequency_map = get_frequency_map(lang)
    definitions = load_definitions()
    deepl_source_lang = {'uk': 'UK', 'es': 'ES', 'en': 'EN', 'ko': 'KO'}.get(lang, 'KO')
    results = []

    for word in word_list:
        rank = frequency_map.get(word)
        found_form = word

        if not rank and lang == 'ko':
            for form in strip_korean_particles(word):
                rank = frequency_map.get(form)
                if rank:
                    found_form = form
                    break

        definition = definitions.get(found_form) or definitions.get(word)
        if not definition:
            definition = (
                translate(found_form, source_lang=deepl_source_lang)
                or translate(word, source_lang=deepl_source_lang)
                or "definition not available"
            )

        results.append({
            'word': word,
            'dictionary_form': found_form,
            'definition': definition,
            'rank': rank or 10001,
            'language': lang,
        })

    return {"words": results}
