"""Casualty count extraction. Missing and conflicting counts stay unknown."""

from __future__ import annotations

import re

from .preprocess import clean_text


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
}
NUMBER_PATTERN = re.compile(
    r"(?<!\w)(?P<number>\d[\d,]*|" + "|".join(
        sorted((re.escape(word) for word in NUMBER_WORDS), key=len, reverse=True)
    ) + r")(?!\w)",
    re.IGNORECASE,
)
DEATH_TERMS = re.compile(
    r"\b(?:killed|dead|died|deaths?|fatalities|fatality|succumbed)\b|"
    r"मौत|मृत्यु|मारे\s+गए|जान\s+गई",
    re.IGNORECASE,
)
INJURY_TERMS = re.compile(
    r"\b(?:injured|hurt|wounded|hospitali[sz]ed)\b|घायल",
    re.IGNORECASE,
)
ZERO_DEATHS = re.compile(
    r"\b(?:no\s+(?:one\s+)?(?:died|was\s+killed|deaths?|fatalities|people\s+(?:were\s+)?killed)|"
    r"none\s+(?:were\s+)?killed|not\s+a\s+single\s+person\s+(?:was\s+)?killed)\b|"
    r"(?:कोई|किसी)\s+की?\s*(?:मौत|मृत्यु)\s+नहीं|कोई\s+नहीं\s+मारा",
    re.IGNORECASE,
)
ZERO_INJURIES = re.compile(
    r"\b(?:no\s+(?:one\s+)?(?:was\s+)?(?:injuries|injured|people\s+(?:were\s+)?injured)|"
    r"none\s+(?:were\s+)?injured|not\s+a\s+single\s+person\s+(?:was\s+)?injured)\b|"
    r"कोई\s+घायल\s+नहीं|किसी\s+को\s+चोट\s+नहीं",
    re.IGNORECASE,
)
NEGATED_CASUALTY = re.compile(r"\b(?:not|never|no)\s+(?:(?:been|reported\s+as)\s+)?$", re.IGNORECASE)


def _number_value(token: str) -> int:
    if token.isdigit() or token.replace(",", "").isdigit():
        return int(token.replace(",", ""))
    return NUMBER_WORDS[token.casefold()]


def extract_count(text: str, kind: str) -> tuple[int | None, str]:
    text = clean_text(text)
    if kind not in {"deaths", "injuries"}:
        raise ValueError("kind must be 'deaths' or 'injuries'")
    terms = DEATH_TERMS if kind == "deaths" else INJURY_TERMS
    zero_pattern = ZERO_DEATHS if kind == "deaths" else ZERO_INJURIES
    values: list[int] = []
    for sentence in re.split(r"(?<=[.!?।॥])\s+|\n+", text):
        if zero_pattern.search(sentence):
            values.append(0)
            continue
        for term in terms.finditer(sentence):
            start, end = term.span()
            if NEGATED_CASUALTY.search(sentence[max(0, start - 18):start]):
                continue
            nearby_start, nearby_end = max(0, start - 32), min(len(sentence), end + 18)
            candidates = list(NUMBER_PATTERN.finditer(sentence, nearby_start, nearby_end))
            before = [match for match in candidates if match.end() <= start]
            after = [match for match in candidates if match.start() >= end]
            if before or after:
                nearest = before[-1] if before else after[0]
                try:
                    values.append(_number_value(nearest.group("number")))
                except (KeyError, ValueError):
                    continue
    distinct = list(dict.fromkeys(values))
    if len(distinct) == 1:
        return distinct[0], "extracted"
    if len(distinct) > 1:
        return None, "conflict"
    return None, "unknown"
