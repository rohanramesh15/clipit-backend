"""
English surface-form tokenizer.

Mirrors the interface of spanish_tokenizer.py — used by the legacy vocabulary /
mining / flashcards pipelines that work with raw words (not lemmas).
For chat features that need lemmas, use english_lemmatizer.py instead.
"""

import re
from typing import List


def is_english_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    if 0x0041 <= code <= 0x005A:  # A-Z
        return True
    if 0x0061 <= code <= 0x007A:  # a-z
        return True
    # Apostrophes used inside English words ("don't", "it's")
    if char in "'’":
        return True
    return False


def extract_english_words(text: str) -> List[str]:
    if not text:
        return []
    tokens = text.split()
    words = []
    for token in tokens:
        clean = re.sub(r'[^\w\s\'’]', '', token, flags=re.UNICODE).strip()
        if clean and any(is_english_char(c) for c in clean):
            words.append(clean.lower())
    return words


def extract_english_words_from_subtitles(subtitles: List[dict]) -> List[str]:
    """
    Extract unique English words. Reads the 'english' subtitle key — note that
    this is the same key used for translation aids in other-language videos,
    but for English-target it IS the target text.
    """
    seen = set()
    words = []
    for sub in subtitles:
        text = sub.get('english', '')
        if not text:
            continue
        for word in extract_english_words(text):
            if word not in seen:
                seen.add(word)
                words.append(word)
    return words
