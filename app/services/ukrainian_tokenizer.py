"""
Ukrainian text tokenizer.

Detects Ukrainian Cyrillic characters and extracts vocabulary words
from subtitle data, mirroring the interface of korean_tokenizer.py.
"""

import re
from typing import List


def is_ukrainian_char(char: str) -> bool:
    """Return True if the character is a Cyrillic character (used in Ukrainian)."""
    code = ord(char)
    # Cyrillic block: U+0400–U+04FF
    if 0x0400 <= code <= 0x04FF:
        return True
    # Cyrillic Supplement: U+0500–U+052F
    if 0x0500 <= code <= 0x052F:
        return True
    return False


def extract_ukrainian_words(text: str) -> List[str]:
    """
    Split text into tokens and return only tokens that contain Ukrainian characters.
    Strips punctuation from each token.
    """
    if not text:
        return []

    tokens = text.split()
    words = []
    for token in tokens:
        # Strip punctuation
        clean = re.sub(r'[^\w\s]', '', token, flags=re.UNICODE).strip()
        if clean and any(is_ukrainian_char(c) for c in clean):
            words.append(clean.lower())
    return words


def extract_ukrainian_words_from_subtitles(subtitles: List[dict]) -> List[str]:
    """
    Extract unique Ukrainian words from a list of subtitle dicts.
    Expects each subtitle to have a 'ukrainian' key (or 'korean' key as fallback
    for subtitle data that hasn't been re-keyed yet).
    """
    seen = set()
    words = []
    for sub in subtitles:
        text = sub.get('ukrainian') or sub.get('target', '')
        if not text:
            continue
        for word in extract_ukrainian_words(text):
            if word not in seen:
                seen.add(word)
                words.append(word)
    return words
