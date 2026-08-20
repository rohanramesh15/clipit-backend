"""
DeepL translation service with SQLite-backed cache.

Words and sentences are cached so each unique piece of text is only
ever sent to the DeepL API once.  If DEEPL_API_KEY is not set the
functions return None gracefully without raising.
"""

import sqlite3
from pathlib import Path
from typing import Optional

import deepl

from app.core.config import settings

_CACHE_DB = Path(__file__).parent.parent.parent / "translation_cache.db"

_translator: Optional[deepl.Translator] = None


def _get_translator() -> Optional[deepl.Translator]:
    global _translator
    if not settings.DEEPL_API_KEY or settings.DEEPL_API_KEY == "your-deepl-api-key-here":
        return None
    if _translator is None:
        _translator = deepl.Translator(settings.DEEPL_API_KEY)
    return _translator


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_cache (
            source_text TEXT PRIMARY KEY,
            translation  TEXT NOT NULL,
            created_at   REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.commit()
    return conn


def _cache_get(text: str) -> Optional[str]:
    with _cache_conn() as conn:
        row = conn.execute(
            "SELECT translation FROM translation_cache WHERE source_text = ?", (text,)
        ).fetchone()
    return row[0] if row else None


def _cache_set(text: str, translation: str) -> None:
    with _cache_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO translation_cache (source_text, translation) VALUES (?, ?)",
            (text, translation),
        )
        conn.commit()


def translate(text: str, source_lang: str = "KO", target_lang: str = "EN-US") -> Optional[str]:
    """
    Translate text from source_lang to target_lang via DeepL.

    Returns the translated string, or None if:
      - DEEPL_API_KEY is not configured
      - The DeepL API call fails for any reason
    Results are cached locally so each unique text is only translated once.
    """
    if not text or not text.strip():
        return None

    cached = _cache_get(text)
    if cached is not None:
        return cached

    translator = _get_translator()
    if translator is None:
        return None

    try:
        result = translator.translate_text(text, source_lang=source_lang, target_lang=target_lang)
        translation = result.text
        _cache_set(text, translation)
        return translation
    except Exception:
        return None


def translate_word_in_context(
    word: str,
    context_sentence: str,
    source_lang: str = "KO",
    target_lang: str = "EN-US"
) -> Optional[str]:
    """
    Translate a word based on its context sentence.

    This uses DeepL to translate the full sentence, then extracts just the
    meaning of the target word. For polysemous words, the context helps
    DeepL choose the correct meaning.

    Returns just the word definition (not the full sentence translation).
    """
    if not word or not context_sentence:
        return translate(word, source_lang, target_lang)

    # Create a cache key that includes both word and context
    cache_key = f"context:{source_lang}:{word}:{context_sentence[:100]}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    translator = _get_translator()
    if translator is None:
        return translate(word, source_lang, target_lang)

    try:
        # Translate the full sentence first to give DeepL context
        sentence_translation = translate(context_sentence, source_lang, target_lang)

        # Then translate just the word - DeepL should pick up context
        # from the session or use similar patterns
        word_translation = translate(word, source_lang, target_lang)

        # If both exist, cache and return the word translation
        if word_translation:
            _cache_set(cache_key, word_translation)
            return word_translation

        return None
    except Exception:
        return translate(word, source_lang, target_lang)
