"""
Spanish lemmatization service.

Wraps spaCy's es_core_news_sm model to reduce inflected forms to their
dictionary lemmas. Used by the chat orchestrator to:
  - Check whether a word in an AI reply is in the user's known-vocab set
  - Normalize user-uploaded vocabulary words and FSRS card surface forms

Loaded once at import time (~1s); subsequent calls are fast.
"""

from typing import Iterable, List, Set

import spacy

_NLP = None


def _get_nlp():
    """Lazily load the spaCy Spanish model on first use."""
    global _NLP
    if _NLP is None:
        # Disable parser/NER for speed — we only need tokenization + lemmatization
        _NLP = spacy.load("es_core_news_sm", disable=["parser", "ner"])
    return _NLP


# Spanish function words / particles to exclude from vocab-lock audits.
# These are grammatical glue, not vocabulary the user needs to "know."
SPANISH_FUNCTION_WORDS: Set[str] = {
    "el", "la", "los", "las", "un", "uno", "una", "unos", "unas",
    "de", "del", "a", "al", "en", "con", "por", "para", "sin", "sobre",
    "y", "o", "u", "ni", "pero", "sino", "que", "como", "si", "porque",
    "se", "me", "te", "le", "les", "lo", "los", "las", "nos", "os",
    "mi", "tu", "su", "mis", "tus", "sus",
    "yo", "tú", "él", "ella", "usted", "nosotros", "nosotras", "vosotros",
    "vosotras", "ellos", "ellas", "ustedes",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "aquel", "aquella", "aquellos", "aquellas",
    "no", "sí", "muy", "más", "menos", "tan", "tanto", "tan",
    "ser", "estar", "haber", "tener",
    "es", "son", "era", "fue", "será", "sea",
    "está", "están", "estaba", "estuvo", "esté",
    "ha", "han", "había", "haber", "habido",
    "hay", "hubo", "habrá",
    "este", "esta", "estos", "estas",
    "ya", "aún", "todavía", "siempre", "nunca", "jamás",
    "aquí", "ahí", "allí", "acá", "allá",
}


def lemmatize(text: str) -> List[str]:
    """
    Lemmatize Spanish text, returning lemmas in order.

    Filters out:
      - Punctuation
      - Whitespace
      - Numbers
      - Pure stopwords (function_words above)
      - Tokens shorter than 2 characters (unless they're real Spanish words)

    Returns lowercased lemmas. Empty list if text is empty/None.
    """
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
    """Lemmatize and return the unique set of lemmas (order not preserved)."""
    return set(lemmatize(text))


def lemmatize_word(word: str) -> str:
    """
    Return the single best lemma for a one-word input (e.g. an FSRS surface form).
    Falls back to the lowercased input if nothing useful comes back.
    """
    if not word or not word.strip():
        return ""
    lemmas = lemmatize(word)
    if not lemmas:
        return word.lower().strip()
    # Prefer the longest non-function-word lemma when multiple come back
    candidates = [l for l in lemmas if l not in SPANISH_FUNCTION_WORDS]
    if candidates:
        return max(candidates, key=len)
    return lemmas[0]


def is_function_word(lemma: str) -> bool:
    """True if the lemma is a Spanish particle/function word."""
    return lemma.lower() in SPANISH_FUNCTION_WORDS


def filter_content_lemmas(lemmas: Iterable[str]) -> List[str]:
    """Drop function words, return only content lemmas."""
    return [l for l in lemmas if not is_function_word(l)]
