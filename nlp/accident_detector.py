"""Conservative road-accident relevance rules for English and Hindi."""

from __future__ import annotations

import re

from .preprocess import clean_text, split_sentences


ACCIDENT_PATTERN = re.compile(
    r"\b(?:road\s+)?(?:accident|crash(?:ed|es|ing)?|collision|collid(?:ed|ing)?|"
    r"rollover|overturn(?:ed)?|hit[- ]and[- ]run|vehicle\s+mishap)\b|"
    r"दुर्घटना|सड़क\s+हादसा|सड़क\s+दुर्घटना|हादसा|हादसे|टक्कर|पलट(?:ा|ी|े)?",
    re.IGNORECASE,
)
NEGATED_PATTERN = re.compile(
    r"\bno\s+(?:road\s+)?(?:accident|crash|collision)\b|"
    r"\bwithout\s+(?:any\s+)?(?:road\s+)?(?:accident|crash)\b|"
    r"(?:कोई|किसी)\s+(?:सड़क\s+)?(?:दुर्घटना|हादसा)\s+नहीं",
    re.IGNORECASE,
)


def is_accident_relevant(text: str) -> bool:
    sentences = split_sentences(clean_text(text))
    return any(ACCIDENT_PATTERN.search(sentence) and not NEGATED_PATTERN.search(sentence)
               for sentence in sentences)
