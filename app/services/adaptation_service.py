"""
Mid-session difficulty adaptation.

Looks at the user's last 3 turns and decides whether the AI's next reply
should stay at the target level, nudge up (user is comfortable), or
nudge down (user is struggling).

Signals used:
  - mean lemma count per turn (proxy for utterance complexity)
  - ratio of off-vocab lemmas in the user's input (proxy for stretch)
  - presence of code-switches or very short replies (struggle signals)

The output is a small adaptation hint string that gets appended to the
system instruction — invisible to the user, but it shapes the next reply.
"""

from typing import List, Optional

from app.services.lemmatizer import lemmatize, filter_content_lemmas
from app.services.vocab_profile_service import VocabProfile


def _compliance(lemmas: List[str], known: set[str]) -> float:
    if not lemmas:
        return 0.0
    matches = sum(1 for l in lemmas if l in known)
    return matches / len(lemmas)


def compute_adaptation(
    last_user_turns: List[str],
    profile: VocabProfile,
    language: str = "es",
) -> tuple[int, Optional[str]]:
    """
    Returns (nudge, hint) where:
      - nudge: -1 (ease down), 0 (stay), +1 (push up)
      - hint: optional sentence appended to the system prompt
    """
    if not last_user_turns:
        return (0, None)

    known = profile.known_lemmas | profile.learning_lemmas
    lemma_counts: List[int] = []
    compliances: List[float] = []
    has_english_word = False

    for text in last_user_turns:
        if not text or not text.strip():
            continue
        lemmas = filter_content_lemmas(lemmatize(text, language), language)
        lemma_counts.append(len(lemmas))
        compliances.append(_compliance(lemmas, known))
        # Crude code-switch detection: a string of pure ASCII letters > 3 chars
        # not present in the known set may be English fallback.
        for tok in text.split():
            clean = "".join(c for c in tok if c.isalpha())
            if len(clean) > 3 and clean.isascii() and clean.lower() not in known:
                has_english_word = True
                break

    if not lemma_counts:
        return (0, None)

    avg_lemmas = sum(lemma_counts) / len(lemma_counts)
    avg_compliance = sum(compliances) / len(compliances) if compliances else 0.0

    # ── Decide ────────────────────────────────────────────────────────────
    # Comfortable: long replies with high vocab compliance
    if avg_lemmas >= 6 and avg_compliance >= 0.7 and not has_english_word:
        return (
            +1,
            "The learner has been responding fluently and using vocabulary "
            "well above their target level. Push slightly: introduce one "
            "more sophisticated structure or a slightly rarer word."
        )

    # Struggling: short replies, English fallback, or low compliance
    if avg_lemmas <= 2 or has_english_word or avg_compliance < 0.3:
        return (
            -1,
            "The learner has been giving short or hesitant replies and may "
            "be struggling. Slow down: shorter sentences, simpler vocabulary, "
            "and ask a concrete easy question to invite a response."
        )

    return (0, None)
