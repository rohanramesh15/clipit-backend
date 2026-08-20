import re
from typing import List


def is_korean_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3:
        return True
    if 0x1100 <= code <= 0x11FF:
        return True
    if 0x3130 <= code <= 0x318F:
        return True
    return False


def extract_korean_words(text: str) -> List[str]:
    if not text:
        return []
    tokens = text.split()
    korean_words = []
    for token in tokens:
        cleaned = re.sub(r'[^\w\s]', '', token)
        if not cleaned:
            continue
        if any(is_korean_char(c) for c in cleaned):
            korean_words.append(cleaned)
    seen = set()
    unique = []
    for w in korean_words:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique


def extract_korean_words_from_subtitles(subtitles: List[dict]) -> List[str]:
    all_words = []
    for sub in subtitles:
        korean_text = sub.get('korean')
        if korean_text:
            all_words.extend(extract_korean_words(korean_text))
    seen = set()
    unique = []
    for w in all_words:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique
