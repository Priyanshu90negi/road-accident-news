"""Incident date extraction with sentence-level accident context."""

from __future__ import annotations

import re
from datetime import date

from .accident_detector import ACCIDENT_PATTERN
from .preprocess import clean_text
from .ner import extract_named_entities


MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4,
    "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9,
    "अक्टूबर": 10, "नवंबर": 11, "दिसंबर": 12,
}
MONTH_PATTERN = "|".join(sorted((re.escape(value) for value in MONTHS), key=len, reverse=True))
DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)"),
    re.compile(rf"(?<![\w])([0-3]?\d)(?:st|nd|rd|th)?\s+({MONTH_PATTERN})\s*,?\s*(20\d{{2}})(?!\d)", re.I),
    re.compile(rf"(?<![\w])({MONTH_PATTERN})\s+([0-3]?\d)(?:st|nd|rd|th)?,?\s+(20\d{{2}})(?!\d)", re.I),
)


def _parse_date_patterns(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for index, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(text):
            try:
                if index == 0:
                    parsed = date(int(match[1]), int(match[2]), int(match[3]))
                elif index == 1:
                    # News reporting from India conventionally uses DD/MM/YYYY.
                    parsed = date(int(match[3]), int(match[2]), int(match[1]))
                elif index == 2:
                    parsed = date(int(match[3]), MONTHS[match[2].casefold()], int(match[1]))
                else:
                    parsed = date(int(match[3]), MONTHS[match[1].casefold()], int(match[2]))
            except (ValueError, KeyError):
                continue
            found.append((match.start(), match.end(), parsed.isoformat()))
    return found


def _date_candidates(text: str) -> list[tuple[int, int, str]]:
    found = _parse_date_patterns(text)
    for entity in extract_named_entities(text):
        if entity["label"] == "DATE":
            entity_start = int(entity["start"])
            found.extend(
                (entity_start + start, entity_start + end, value)
                for start, end, value in _parse_date_patterns(clean_text(str(entity["text"])))
            )
    return sorted(found)


def extract_date_mentions(text: str) -> list[str]:
    return list(dict.fromkeys(value for _, _, value in _date_candidates(clean_text(text))))


def extract_accident_date(text: str) -> tuple[str | None, str]:
    """Return (ISO date, status); dates need nearby incident wording."""
    contextual_dates: list[str] = []
    for sentence in re.split(r"(?<=[.!?।॥])\s+|\n+", clean_text(text)):
        candidates = _date_candidates(sentence)
        if not candidates:
            continue
        for start, end, value in candidates:
            left = sentence[max(0, start - 70):start]
            right = sentence[end:min(len(sentence), end + 70)]
            nearby = left + sentence[start:end] + right
            publication_before = re.search(
                r"\b(?:published|posted|updated|article\s+date|report\s+dated)\b|"
                r"प्रकाशित|अपडेट|रिपोर्ट\s+की\s+तारीख",
                left[-35:],
                re.IGNORECASE,
            )
            publication_after = re.search(
                r"^.{0,24}\b(?:was\s+)?(?:published|posted|updated)\b|"
                r"^.{0,24}(?:प्रकाशित|अपडेट)",
                right,
                re.IGNORECASE,
            )
            has_incident_context = bool(
                ACCIDENT_PATTERN.search(nearby)
                or re.search(
                    r"\b(?:incident|occurred|happened|took\s+place|vehicle\s+lost\s+control)\b|"
                    r"घटना|हुआ|हुई|हुए|के\s+दौरान|में\s+हादसा",
                    nearby,
                    re.IGNORECASE,
                )
            )
            if has_incident_context and not publication_before and not publication_after:
                contextual_dates.append(value)
    distinct = list(dict.fromkeys(contextual_dates))
    if len(distinct) == 1:
        return distinct[0], "extracted"
    if len(distinct) > 1:
        return None, "conflict"
    return None, "unknown"
