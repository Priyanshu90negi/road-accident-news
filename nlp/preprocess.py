"""Analysis-only text normalization. The stored article body is never changed."""

from __future__ import annotations

import re
import unicodedata


_DIGIT_TRANSLATION = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def clean_text(text: str) -> str:
    """Normalize Unicode and whitespace for analysis without mutating source text."""
    normalized = unicodedata.normalize("NFC", normalize_digits(text or ""))
    return re.sub(r"[\t\r\f\v ]+", " ", normalized).strip()


def split_sentences(text: str) -> list[str]:
    normalized = clean_text(text)
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?।॥])\s+|\n+", normalized)
        if sentence.strip()
    ]
