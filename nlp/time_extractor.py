"""English and Hindi clock-time extraction."""

from __future__ import annotations

import re

from .preprocess import clean_text


_CLOCK = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
_HINDI_CLOCK = re.compile(
    r"(?P<period>सुबह|प्रातः|दोपहर|शाम|रात|रात्रि)\s*(?P<hour>\d{1,2})"
    r"(?::(?P<minute>\d{2}))?\s*(?:बजे)?",
    re.IGNORECASE,
)


def extract_times(text: str) -> list[str]:
    text = clean_text(text)
    values: list[str] = []
    for match in _CLOCK.finditer(text):
        hour, minute = int(match["hour"]), int(match["minute"] or 0)
        ampm = (match["ampm"] or "").replace(".", "").lower()
        has_minutes = ":" in match.group(0)
        left_context = text[max(0, match.start() - 14):match.start()]
        right_context = text[match.end():min(len(text), match.end() + 12)]
        has_time_marker = bool(
            re.search(r"\b(?:at|around|about|approximately|near)\s*$", left_context, re.I)
            or re.search(r"^\s*(?:o'clock|बजे)", right_context, re.I)
        )
        if not ampm and re.search(r"(?:सुबह|प्रातः|दोपहर|शाम|रात|रात्रि)\s*$", left_context):
            continue
        # Ignore bare years and implausible clocks.
        if hour > 23 or minute > 59 or (not ampm and not has_minutes and not has_time_marker):
            continue
        if ampm:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        values.append(f"{hour:02d}:{minute:02d}")
    for match in _HINDI_CLOCK.finditer(text):
        hour, minute = int(match["hour"]), int(match["minute"] or 0)
        period = match["period"]
        if not 1 <= hour <= 12 or minute > 59:
            continue
        if period in {"दोपहर", "शाम", "रात", "रात्रि"} and hour != 12:
            hour += 12
        elif period in {"सुबह", "प्रातः"} and hour == 12:
            hour = 0
        values.append(f"{hour:02d}:{minute:02d}")
    return list(dict.fromkeys(values))


def extract_accident_time(text: str) -> tuple[str | None, str]:
    values = extract_times(text)
    if len(values) == 1:
        return values[0], "extracted"
    if len(values) > 1:
        return None, "conflict"
    return None, "unknown"
