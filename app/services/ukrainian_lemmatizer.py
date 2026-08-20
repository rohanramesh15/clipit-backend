"""
Ukrainian lemmatizer.

Maps inflected Ukrainian surface forms (e.g. "роки", "часу", "містом") to their
base-form lemma (e.g. "рік", "час", "місто") so they can be matched against the
lemma-based frequency list.

Uses pymorphy3 with the Ukrainian dictionary (pymorphy3-dicts-uk). If those
packages are not installed the lemmatizer degrades gracefully to a lowercase
pass-through (so the server never crashes — matching simply falls back to the
previous exact-match behavior).
"""

import functools
from typing import Optional

_morph = None
_morph_unavailable = False


def _get_morph():
    """Lazily build a singleton MorphAnalyzer for Ukrainian."""
    global _morph, _morph_unavailable
    if _morph is not None:
        return _morph
    if _morph_unavailable:
        return None
    try:
        import pymorphy3
        _morph = pymorphy3.MorphAnalyzer(lang='uk')
    except Exception:
        # pymorphy3 / pymorphy3-dicts-uk not installed, or init failed.
        _morph_unavailable = True
        _morph = None
    return _morph


@functools.lru_cache(maxsize=50000)
def lemmatize_word(word: str) -> str:
    """Return the base-form lemma of a single Ukrainian word (lowercased)."""
    if not word:
        return word
    lowered = word.lower()
    morph = _get_morph()
    if morph is None:
        return lowered
    try:
        parses = morph.parse(lowered)
        if parses:
            return (parses[0].normal_form or lowered).lower()
    except Exception:
        pass
    return lowered


def is_available() -> bool:
    """True if the morphological analyzer loaded successfully."""
    return _get_morph() is not None
