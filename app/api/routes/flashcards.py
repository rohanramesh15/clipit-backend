import json
import re
from pathlib import Path
from typing import List, Set
from fastapi import APIRouter, HTTPException, Body
from app.services.subtitle_service import load_cached_subtitles, load_cached_subtitles_ukrainian
from app.api.routes.netflix import load_cached_netflix_subtitles
from app.services.vocab_service import load_frequency_map
from app.services import ukrainian_lemmatizer
from app.services.deepl_service import translate, translate_word_in_context
from app.api.routes.vocabulary import get_frequency_map

router = APIRouter()

# Patterns for sentences that should be skipped (music placeholders, etc.)
SKIP_SENTENCE_PATTERNS = [
    re.compile(r'^\s*\[.*music.*\]\s*$', re.IGNORECASE),  # [music], [Music playing], etc.
    re.compile(r'^\s*\[.*음악.*\]\s*$'),  # [음악] Korean for music
    re.compile(r'^\s*♪.*♪\s*$'),  # ♪...♪ music notes
    re.compile(r'^\s*\[singing\]\s*$', re.IGNORECASE),  # [singing]
    re.compile(r'^\s*\[.*sings.*\]\s*$', re.IGNORECASE),  # [person sings]
    re.compile(r'^\s*\[instrumental\]\s*$', re.IGNORECASE),  # [instrumental]
    re.compile(r'^\s*\[.*музика.*\]\s*$', re.IGNORECASE),  # [музика] Ukrainian for music
]


def is_music_placeholder(sentence: str) -> bool:
    """Check if a sentence is a music/singing placeholder that should be skipped."""
    if not sentence:
        return False
    for pattern in SKIP_SENTENCE_PATTERNS:
        if pattern.match(sentence):
            return True
    return False


def definitions_are_similar(def1: str, def2: str, threshold: float = 0.8) -> bool:
    """
    Check if two definitions are similar enough to be considered duplicates.
    Uses simple word overlap comparison.
    """
    if not def1 or not def2:
        return False

    # Normalize
    words1 = set(def1.lower().split())
    words2 = set(def2.lower().split())

    if not words1 or not words2:
        return def1.lower() == def2.lower()

    # Calculate Jaccard similarity
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    similarity = intersection / union if union > 0 else 0

    return similarity >= threshold


def get_definition_hash(definition: str) -> str:
    """Create a short hash of a definition for deduplication."""
    import hashlib
    normalized = definition.lower().strip()[:50]
    return hashlib.md5(normalized.encode()).hexdigest()[:8]

# Definitions loaded once at first request
_DEFINITIONS: dict | None = None
_SKIPPED_SENTENCES: dict | None = None
_DATA_DIR = Path(__file__).parent.parent.parent.parent / 'data'


def load_skipped_sentences() -> dict:
    """Load skipped sentences from file. Format: {word: [sentence1, sentence2, ...]}"""
    global _SKIPPED_SENTENCES
    if _SKIPPED_SENTENCES is None:
        skipped_file = _DATA_DIR / 'skipped_sentences.json'
        if skipped_file.exists():
            with open(skipped_file, 'r', encoding='utf-8') as f:
                _SKIPPED_SENTENCES = json.load(f)
        else:
            _SKIPPED_SENTENCES = {}
    return _SKIPPED_SENTENCES


def save_skipped_sentences(data: dict) -> None:
    """Save skipped sentences to file."""
    global _SKIPPED_SENTENCES
    _SKIPPED_SENTENCES = data
    skipped_file = _DATA_DIR / 'skipped_sentences.json'
    skipped_file.parent.mkdir(parents=True, exist_ok=True)
    with open(skipped_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_skipped_for_word(word: str) -> Set[str]:
    """Get set of skipped sentences for a word."""
    skipped = load_skipped_sentences()
    return set(skipped.get(word, []))


def load_definitions() -> dict:
    global _DEFINITIONS
    if _DEFINITIONS is None:
        defs_file = _DATA_DIR / 'definitions.json'
        if defs_file.exists():
            with open(defs_file, 'r', encoding='utf-8') as f:
                _DEFINITIONS = json.load(f)
        else:
            _DEFINITIONS = {}
    return _DEFINITIONS


def strip_korean_particles(word: str) -> List[str]:
    if not word:
        return []
    particles = [
        '으로', '로', '에서', '에게', '에',
        '이', '가', '을', '를', '은', '는',
        '과', '와', '의', '도', '만', '부터', '까지',
        '예요', '이에요', '여요', '이다', '입니다',
        '했다', '합니다', '해요',
        '던', '았', '었', '였', '고', '니', '지',
    ]
    possible_forms = [word]
    for particle in particles:
        if word.endswith(particle) and len(word) > len(particle):
            base = word[:-len(particle)]
            if base:
                possible_forms.append(base)
                if base.endswith('하'):
                    possible_forms.append(base + '다')
                if len(base) >= 2:
                    possible_forms.append(base + '다')
    return possible_forms


def get_verb_stem(word: str) -> str | None:
    """Extract verb stem from dictionary form (ends in 다)."""
    if word.endswith('다') and len(word) >= 2:
        return word[:-1]
    return None


def find_sentence_for_word(word: str, subtitles: list, skipped_sentences: Set[str] = None) -> dict:
    if skipped_sentences is None:
        skipped_sentences = set()

    # Common Korean particles that can follow a noun
    particles = ['이', '가', '을', '를', '은', '는', '의', '에', '도', '만', '와', '과', '로', '으로', '에서', '에게', '부터', '까지', '요', '야', ' ', ',', '.', '?', '!']

    # Common verb endings for conjugated forms
    verb_endings = [
        '요', '어요', '아요', '여요',  # polite present
        '었어요', '았어요', '였어요',  # polite past
        '을게요', '을래요', '을까요',  # future/intention
        '고', '서', '면', '니까', '지만',  # connective
        '는', '은', '을', '던',  # modifiers
        '습니다', '습니까', '세요', '셨어요',  # formal
        '어', '아', '여', '지', '네', '군', '냐',  # casual
        '었어', '았어', '였어',  # casual past
    ]

    # First pass: exact word match (followed by particle/punctuation/space/end)
    for sub in subtitles:
        korean = sub.get('korean', '')
        if not korean or word not in korean:
            continue
        if korean in skipped_sentences:
            continue
        if is_music_placeholder(korean):
            continue

        idx = korean.find(word)
        while idx != -1:
            end_idx = idx + len(word)
            if end_idx == len(korean) or any(korean[end_idx:].startswith(p) for p in particles):
                if idx == 0 or korean[idx-1] == ' ':
                    start = sub.get('start', 0)
                    end = sub.get('end', start + 5)
                    return {
                        'sentence': korean,
                        'translation': sub.get('english', 'No translation available'),
                        'timestamp': int(start),
                        'end_timestamp': int(end) + 1,
                        'matched_form': word,
                    }
            idx = korean.find(word, end_idx)

    # Second pass: if word is a verb (ends in 다), search for conjugated forms
    stem = get_verb_stem(word)
    if stem:
        for sub in subtitles:
            korean = sub.get('korean', '')
            if not korean or stem not in korean:
                continue
            if korean in skipped_sentences:
                continue
            if is_music_placeholder(korean):
                continue

            idx = korean.find(stem)
            while idx != -1:
                # Check if stem is followed by a verb ending
                after_stem = korean[idx + len(stem):]
                matched_ending = None
                for ending in verb_endings:
                    if after_stem.startswith(ending):
                        matched_ending = ending
                        break

                if matched_ending:
                    # Make sure it's not part of a compound (check char before)
                    if idx == 0 or korean[idx-1] == ' ':
                        start = sub.get('start', 0)
                        end = sub.get('end', start + 5)
                        return {
                            'sentence': korean,
                            'translation': sub.get('english', 'No translation available'),
                            'timestamp': int(start),
                            'end_timestamp': int(end) + 1,
                            'matched_form': stem + matched_ending,
                        }
                idx = korean.find(stem, idx + 1)

    # Third pass: fall back to any substring match
    for sub in subtitles:
        korean = sub.get('korean', '')
        if korean and word in korean and korean not in skipped_sentences and not is_music_placeholder(korean):
            start = sub.get('start', 0)
            end = sub.get('end', start + 5)
            return {
                'sentence': korean,
                'translation': sub.get('english', 'No translation available'),
                'timestamp': int(start),
                'end_timestamp': int(end) + 1,
                'matched_form': word,
            }

    return {'sentence': word, 'translation': 'No translation available', 'timestamp': 0, 'end_timestamp': 5, 'matched_form': word}


def find_sentence_for_word_ukrainian(word: str, subtitles: list, skipped_sentences: Set[str] = None) -> dict:
    """Find a subtitle sentence containing the Ukrainian word."""
    if skipped_sentences is None:
        skipped_sentences = set()

    boundary_chars = [' ', ',', '.', '?', '!', ':', ';', '—', '-', '(', ')']

    for sub in subtitles:
        ukrainian = sub.get('ukrainian', '')
        if not ukrainian or word not in ukrainian:
            continue
        if ukrainian in skipped_sentences:
            continue
        if is_music_placeholder(ukrainian):
            continue

        idx = ukrainian.find(word)
        while idx != -1:
            end_idx = idx + len(word)
            after = ukrainian[end_idx:] if end_idx < len(ukrainian) else ''
            before_ok = idx == 0 or ukrainian[idx - 1] in boundary_chars
            after_ok = not after or after[0] in boundary_chars
            if before_ok and after_ok:
                start = sub.get('start', 0)
                end = sub.get('end', start + 5)
                return {
                    'sentence': ukrainian,
                    'translation': sub.get('english', 'No translation available'),
                    'timestamp': int(start),
                    'end_timestamp': int(end) + 1,
                    'matched_form': word,
                }
            idx = ukrainian.find(word, end_idx)

    # Fallback: any substring match
    for sub in subtitles:
        ukrainian = sub.get('ukrainian', '')
        if ukrainian and word in ukrainian and ukrainian not in skipped_sentences and not is_music_placeholder(ukrainian):
            start = sub.get('start', 0)
            end = sub.get('end', start + 5)
            return {
                'sentence': ukrainian,
                'translation': sub.get('english', 'No translation available'),
                'timestamp': int(start),
                'end_timestamp': int(end) + 1,
                'matched_form': word,
            }

    return {'sentence': word, 'translation': 'No translation available', 'timestamp': 0, 'end_timestamp': 5, 'matched_form': word}


@router.post("/flashcard-data")
async def get_flashcard_data(request: dict = Body(...)):
    """
    Generate flashcard data for a list of words from a video.
    Body: { video_id, words: [...], word_source: "essential"|"selected", language: "ko"|"uk" }
    """
    video_id = request.get('video_id')
    words = request.get('words', [])
    word_source = request.get('word_source', 'essential')
    language = request.get('language', 'ko')

    if not video_id or not words:
        raise HTTPException(status_code=400, detail="video_id and words are required")

    # Handle Netflix videos (prefixed with netflix_)
    if video_id.startswith('netflix_'):
        subtitle_data = load_cached_netflix_subtitles(video_id, language)
    elif language == 'uk':
        subtitle_data = load_cached_subtitles_ukrainian(video_id)
    else:
        subtitle_data = load_cached_subtitles(video_id)

    if not subtitle_data:
        raise HTTPException(status_code=404, detail=f"Subtitles not found for {video_id}")

    subtitles = subtitle_data['subtitles']
    frequency_map = get_frequency_map(language)
    definitions = load_definitions()
    user_definitions = load_user_definitions()

    deepl_source_lang = {'uk': 'UK', 'ko': 'KO'}.get(language, 'KO')

    flashcards = []
    for word in words:
        skipped = get_skipped_for_word(word)

        if language == 'uk':
            sentence_data = find_sentence_for_word_ukrainian(word, subtitles, skipped)
            # Include the lemma so the rank + dictionary form resolve against the
            # lemma-based Ukrainian frequency list.
            uk_lemma = ukrainian_lemmatizer.lemmatize_word(word)
            possible_forms = [word, uk_lemma] if uk_lemma != word else [word]
        else:
            sentence_data = find_sentence_for_word(word, subtitles, skipped)
            possible_forms = strip_korean_particles(word)

        dictionary_form = word
        rank = frequency_map.get(word)

        if not rank:
            for form in possible_forms:
                rank = frequency_map.get(form)
                if rank:
                    dictionary_form = form
                    break

        if not rank:
            dictionary_form = possible_forms[-1] if possible_forms else word
            rank = 10001

        # User definitions first, then definitions.json, then context-aware DeepL as fallback
        user_key = f"{language}:{dictionary_form}"
        user_key_alt = f"{language}:{word}"
        definition = (
            user_definitions.get(user_key)
            or user_definitions.get(user_key_alt)
            or definitions.get(dictionary_form)
            or definitions.get(word)
        )
        if not definition:
            # Use context-aware translation for better accuracy with polysemous words
            context = sentence_data.get('sentence', '')
            definition = (
                translate_word_in_context(dictionary_form, context, source_lang=deepl_source_lang)
                or translate_word_in_context(word, context, source_lang=deepl_source_lang)
                or translate(dictionary_form, source_lang=deepl_source_lang)
                or translate(word, source_lang=deepl_source_lang)
                or "definition not available"
            )

        sentence_translation = sentence_data['translation']
        if (not sentence_translation or sentence_translation == 'No translation available') and sentence_data['sentence']:
            sentence_translation = translate(sentence_data['sentence'], source_lang=deepl_source_lang) or 'No translation available'

        flashcards.append({
            'target_word': word,
            'dictionary_form': dictionary_form,
            'english': definition,
            'definition_hash': get_definition_hash(definition),
            'sentence': sentence_data['sentence'],
            'sentence_translation': sentence_translation,
            'timestamp': sentence_data['timestamp'],
            'end_timestamp': sentence_data['end_timestamp'],
            'video_id': video_id,
            'rank': rank,
            'language': language,
        })

    return {
        'video_id': video_id,
        'word_source': word_source,
        'total_cards': len(flashcards),
        'flashcards': flashcards
    }


@router.post("/flashcard-skip")
async def skip_flashcard_sentence(request: dict = Body(...)):
    """
    Skip a sentence for a word so it won't be used in future flashcards.
    Body: { word, sentence }
    """
    word = request.get('word')
    sentence = request.get('sentence')

    if not word or not sentence:
        raise HTTPException(status_code=400, detail="word and sentence are required")

    skipped = load_skipped_sentences()
    if word not in skipped:
        skipped[word] = []

    if sentence not in skipped[word]:
        skipped[word].append(sentence)
        save_skipped_sentences(skipped)

    return {"status": "ok", "word": word, "skipped_count": len(skipped[word])}


# User definition overrides storage
_USER_DEFINITIONS: dict | None = None


def load_user_definitions() -> dict:
    """Load user definition overrides. Format: {word: definition}"""
    global _USER_DEFINITIONS
    if _USER_DEFINITIONS is None:
        user_defs_file = _DATA_DIR / 'user_definitions.json'
        if user_defs_file.exists():
            with open(user_defs_file, 'r', encoding='utf-8') as f:
                _USER_DEFINITIONS = json.load(f)
        else:
            _USER_DEFINITIONS = {}
    return _USER_DEFINITIONS


def save_user_definitions(data: dict) -> None:
    """Save user definition overrides."""
    global _USER_DEFINITIONS
    _USER_DEFINITIONS = data
    user_defs_file = _DATA_DIR / 'user_definitions.json'
    user_defs_file.parent.mkdir(parents=True, exist_ok=True)
    with open(user_defs_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.put("/flashcard-definition")
async def update_flashcard_definition(request: dict = Body(...)):
    """
    Update the English definition for a word.
    Body: { word, definition, language }
    """
    word = request.get('word')
    definition = request.get('definition')
    language = request.get('language', 'ko')

    if not word or not definition:
        raise HTTPException(status_code=400, detail="word and definition are required")

    # Store with language prefix to support multiple languages
    key = f"{language}:{word}"
    user_defs = load_user_definitions()
    user_defs[key] = definition
    save_user_definitions(user_defs)

    return {"status": "ok", "word": word, "definition": definition, "language": language}
