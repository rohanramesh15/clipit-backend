from pathlib import Path
from typing import Dict, List, Optional

from app.services import ukrainian_lemmatizer


def lemmatize_for_language(word: str, language: str) -> Optional[str]:
    """Return a base-form lemma for languages that need it, else None."""
    if language == 'uk':
        return ukrainian_lemmatizer.lemmatize_word(word)
    return None

LANGUAGE_PARTICLES = {
    'ko': ['이', '가', '을', '를', '에', '에서', '와', '과', '의', '로', '으로', '도', '만', '은', '는'],
    'uk': ['і', 'й', 'та', 'що', 'як', 'але', 'або', 'це', 'не', 'на', 'в', 'у', 'з', 'до', 'за'],
    'en': ['the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 'she', 'or', 'an'],
}

# Path relative to the backend root (where main.py lives)
_DATA_DIR = Path(__file__).parent.parent.parent / 'data'


def load_frequency_map(language: str = 'ko', custom_path: Optional[str] = None) -> Dict[str, int]:
    default_paths = {
        'ko': _DATA_DIR / 'frequency_lists' / 'korean_freq_topik.txt',
        'uk': _DATA_DIR / 'frequency_lists' / 'ukrainian_freq.txt',
        'en': _DATA_DIR / 'frequency_lists' / 'english_freq.txt',
    }
    full_path = Path(custom_path) if custom_path else default_paths.get(language)
    if not full_path:
        raise ValueError(f"No frequency list for language: {language}")

    frequency_map: Dict[str, int] = {}
    with open(full_path, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) >= 2:
                word, rank = parts[0].strip(), parts[1].strip()
                if word and rank:
                    frequency_map[word] = int(rank)
    return frequency_map


def is_common_particle(word: str, rank: int, language: str = 'ko') -> bool:
    if rank <= 100:
        return True
    return word in LANGUAGE_PARTICLES.get(language, [])


def filter_vocabulary(
    words: List[str],
    frequency_map: Dict[str, int],
    language: str = 'ko'
) -> List[Dict]:
    """Return all words found in the frequency list, excluding very common particles.

    For inflected languages (e.g. Ukrainian) the frequency list is keyed by
    lemma, while subtitle words are surface forms. We therefore try a direct
    match first and fall back to the word's lemma, deduping by lemma so an
    inflected form and its base form don't both produce a card.
    """
    filtered = []
    seen = set()
    for word in words:
        rank = frequency_map.get(word)
        dedup_key = word

        if rank is None:
            lemma = lemmatize_for_language(word, language)
            if lemma and lemma != word:
                lemma_rank = frequency_map.get(lemma)
                if lemma_rank is not None:
                    rank = lemma_rank
                    dedup_key = lemma

        if rank is None:
            continue
        if is_common_particle(word, rank, language):
            continue
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        filtered.append({
            'word': word,
            'rank': rank,
            'language': language
        })

    filtered.sort(key=lambda x: x['rank'])
    return filtered


def get_vocab_stats(filtered_vocab: List[Dict]) -> Dict:
    return {'total': len(filtered_vocab)}


def get_difficulty(rank: int, language: str = 'ko') -> str:
    if rank <= 500:
        return 'beginner'
    elif rank <= 2000:
        return 'elementary'
    elif rank <= 5000:
        return 'intermediate'
    elif rank <= 10000:
        return 'advanced'
    else:
        return 'expert'


def filter_by_priority_mode(
    video_words: List[str],
    frequency_map: Dict[str, int],
    user_vocab: List[Dict],  # [{"word": "...", "translation": "..."}]
    priority_mode: str = "mixed",
    language: str = "ko"
) -> List[Dict]:
    """
    Filter video words based on user's priority mode.

    Args:
        video_words: Words extracted from video subtitles
        frequency_map: Standard frequency list
        user_vocab: User's uploaded vocabulary words
        priority_mode: 'uploaded_only', 'frequency_only', or 'mixed'
        language: Language code

    Returns:
        Filtered vocabulary list with source indicators
    """
    video_word_set = set(video_words)
    user_vocab_set = {w['word'] for w in user_vocab}
    user_vocab_map = {w['word']: w['translation'] for w in user_vocab}

    if priority_mode == "uploaded_only":
        # Only show words from video that are in user's uploaded list
        result = []
        for idx, uv in enumerate(user_vocab):
            if uv['word'] in video_word_set:
                result.append({
                    'word': uv['word'],
                    'rank': idx + 1,  # Use upload order as rank
                    'language': language,
                    'source': 'uploaded',
                    'user_translation': uv['translation']
                })
        return result

    elif priority_mode == "frequency_only":
        # Only show words from video that are in frequency list (current behavior)
        filtered = filter_vocabulary(video_words, frequency_map, language)
        for item in filtered:
            item['source'] = 'frequency'
            # If user has a translation for this word, include it
            if item['word'] in user_vocab_map:
                item['user_translation'] = user_vocab_map[item['word']]
        return filtered

    else:  # "mixed" - default
        # Show words from video that are in EITHER uploaded list OR frequency list
        result = []
        seen_words = set()

        # First, add uploaded words that appear in video
        for idx, uv in enumerate(user_vocab):
            if uv['word'] in video_word_set and uv['word'] not in seen_words:
                result.append({
                    'word': uv['word'],
                    'rank': idx + 1,
                    'language': language,
                    'source': 'uploaded',
                    'user_translation': uv['translation']
                })
                seen_words.add(uv['word'])

        # Then, add frequency words that appear in video (excluding already seen)
        freq_filtered = filter_vocabulary(video_words, frequency_map, language)
        for item in freq_filtered:
            if item['word'] not in seen_words:
                item['source'] = 'frequency'
                if item['word'] in user_vocab_map:
                    item['user_translation'] = user_vocab_map[item['word']]
                result.append(item)
                seen_words.add(item['word'])

        return result
