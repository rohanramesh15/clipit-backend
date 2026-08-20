"""
English lemmatization service.

Wraps spaCy's en_core_web_sm model. Same interface as spanish_lemmatizer.py.
"""

from typing import Iterable, List, Set

import spacy

_NLP = None


def _get_nlp():
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    return _NLP


# Common English function words / particles to exclude from vocab-lock audits.
ENGLISH_FUNCTION_WORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "so", "if", "of", "at", "by", "for",
    "to", "in", "on", "with", "from", "into", "over", "under", "out", "up",
    "down", "as", "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "done",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    "this", "that", "these", "those", "it", "its", "i", "you", "he", "she",
    "we", "they", "me", "him", "her", "us", "them", "my", "your", "his",
    "hers", "our", "their", "mine", "yours", "ours", "theirs", "myself",
    "yourself", "himself", "herself", "ourselves", "themselves",
    "not", "no", "yes", "very", "too", "so", "just", "only", "also",
    "than", "then", "there", "here", "where", "when", "why", "how",
    "who", "what", "which", "whose", "whom",
    "about", "after", "again", "ago", "all", "any", "because", "before",
    "between", "both", "during", "each", "few", "more", "most", "much",
    "other", "same", "some", "such", "through", "while", "yet",
}


def lemmatize(text: str) -> List[str]:
    if not text or not text.strip():
        return []
    doc = _get_nlp()(text)
    lemmas: List[str] = []
    for tok in doc:
        if tok.is_punct or tok.is_space or tok.like_num:
            continue
        lemma = tok.lemma_.lower().strip()
        if not lemma:
            continue
        if len(lemma) < 2:
            continue
        lemmas.append(lemma)
    return lemmas


def lemmatize_unique(text: str) -> Set[str]:
    return set(lemmatize(text))


def lemmatize_word(word: str) -> str:
    if not word or not word.strip():
        return ""
    lemmas = lemmatize(word)
    if not lemmas:
        return word.lower().strip()
    candidates = [l for l in lemmas if l not in ENGLISH_FUNCTION_WORDS]
    if candidates:
        return max(candidates, key=len)
    return lemmas[0]


def is_function_word(lemma: str) -> bool:
    return lemma.lower() in ENGLISH_FUNCTION_WORDS


def filter_content_lemmas(lemmas: Iterable[str]) -> List[str]:
    return [l for l in lemmas if not is_function_word(l)]
