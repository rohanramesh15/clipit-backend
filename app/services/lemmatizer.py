"""
Language-agnostic lemmatizer dispatcher.

Routes to the per-language lemmatizer module based on language code.
This is what chat services should depend on instead of hardcoding
spanish_lemmatizer.
"""

from typing import Iterable, List, Set

from app.services import spanish_lemmatizer
from app.services import english_lemmatizer


def lemmatize(text: str, language: str) -> List[str]:
    if language == "en":
        return english_lemmatizer.lemmatize(text)
    return spanish_lemmatizer.lemmatize(text)  # default: 'es'


def lemmatize_word(word: str, language: str) -> str:
    if language == "en":
        return english_lemmatizer.lemmatize_word(word)
    return spanish_lemmatizer.lemmatize_word(word)


def filter_content_lemmas(lemmas: Iterable[str], language: str) -> List[str]:
    if language == "en":
        return english_lemmatizer.filter_content_lemmas(lemmas)
    return spanish_lemmatizer.filter_content_lemmas(lemmas)


def function_words(language: str) -> Set[str]:
    if language == "en":
        return english_lemmatizer.ENGLISH_FUNCTION_WORDS
    return spanish_lemmatizer.SPANISH_FUNCTION_WORDS


def get_nlp(language: str):
    if language == "en":
        return english_lemmatizer._get_nlp()
    return spanish_lemmatizer._get_nlp()
