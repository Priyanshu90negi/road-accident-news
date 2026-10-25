"""Run independent rule-based extractors for one or more incident passages."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from .accident_detector import ACCIDENT_PATTERN, NEGATED_PATTERN, is_accident_relevant
from .casualty_extractor import extract_count
from .cause_extractor import (
    extract_accident_type,
    extract_cause,
    extract_vehicles,
    extract_weather,
)
from .date_extractor import extract_accident_date
from .location_extractor import extract_location
from .preprocess import clean_text, split_sentences
from .time_extractor import extract_accident_time


NEW_EVENT_MARKER = re.compile(
    r"\b(?:another|separate|second|different)\s+(?:road\s+)?(?:accident|crash|collision)\b|"
    r"\b(?:in|after)\s+another\s+(?:incident|crash)\b|"
    r"एक\s+अन्य\s+(?:हादसा|दुर्घटना)|दूसरे\s+(?:हादसे|दुर्घटना)",
    re.IGNORECASE,
)


def _specific_place(text: str) -> str | None:
    return extract_location(text)[1]


def segment_incidents(article_text: str) -> list[str]:
    """Split explicit separate incidents while keeping nearby detail sentences."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", clean_text(article_text)) if p.strip()]
    if not paragraphs:
        paragraphs = [clean_text(article_text)] if clean_text(article_text) else []

    segments: list[str] = []
    current = ""
    current_has_event = False
    for paragraph in paragraphs:
        sentences = split_sentences(paragraph)
        has_event = any(
            ACCIDENT_PATTERN.search(sentence) and not NEGATED_PATTERN.search(sentence)
            for sentence in sentences
        )
        if not has_event:
            if current:
                current += " " + paragraph
            continue

        if current_has_event:
            old_area = _specific_place(current)
            new_area = _specific_place(paragraph)
            old_date, _ = extract_accident_date(current)
            new_date, _ = extract_accident_date(paragraph)
            starts_separate = bool(NEW_EVENT_MARKER.search(paragraph))
            distinct_location = bool(old_area and new_area and old_area != new_area)
            distinct_date = bool(old_date and new_date and old_date != new_date)
            if starts_separate or distinct_location or distinct_date:
                segments.append(current.strip())
                current = ""
                current_has_event = False

        # A paragraph may contain two named incidents without a blank line.
        local: list[str] = []
        local_has_event = False
        for sentence in sentences:
            event_sentence = bool(
                ACCIDENT_PATTERN.search(sentence) and not NEGATED_PATTERN.search(sentence)
            )
            if event_sentence and local_has_event:
                prior = " ".join(local)
                prior_area = _specific_place(prior)
                sentence_area = _specific_place(sentence)
                prior_date, _ = extract_accident_date(prior)
                sentence_date, _ = extract_accident_date(sentence)
                if (NEW_EVENT_MARKER.search(sentence)
                        or (prior_area and sentence_area and prior_area != sentence_area)
                        or (prior_date and sentence_date and prior_date != sentence_date)):
                    if current:
                        segments.append(current.strip())
                    segments.append(prior.strip())
                    current = ""
                    current_has_event = False
                    local = []
                    local_has_event = False
            local.append(sentence)
            local_has_event = local_has_event or event_sentence
        local_text = " ".join(local).strip()
        if current and not current_has_event:
            current = (current + " " + local_text).strip()
        elif current and current_has_event:
            # Same-incident continuation, such as a casualty sentence in a
            # neighboring paragraph that repeats the word "crash".
            current = (current + " " + local_text).strip()
        else:
            current = local_text
        current_has_event = True

    if current.strip():
        segments.append(current.strip())
    return [segment for segment in segments if is_accident_relevant(segment)]


def _extract_one(segment: str, published_date: str | None) -> dict[str, Any]:
    accident_date, date_status = extract_accident_date(segment)
    accident_time, time_status = extract_accident_time(segment)
    district, area, location_status = extract_location(segment)
    cause, cause_status = extract_cause(segment)
    deaths, deaths_status = extract_count(segment, "deaths")
    injuries, injuries_status = extract_count(segment, "injuries")
    vehicles = extract_vehicles(segment)
    accident_type = extract_accident_type(segment)
    weather = extract_weather(segment)

    conflicts: list[str] = []
    if date_status == "conflict":
        conflicts.append("More than one incident date was found in this passage.")
    if time_status == "conflict":
        conflicts.append("More than one time was found in this passage.")
    if location_status == "ambiguous":
        conflicts.append("More than one locality was named; the first was retained for review.")
    if cause_status == "conflict":
        conflicts.append("More than one possible cause was found; cause was left blank.")
    if deaths_status == "conflict":
        conflicts.append("Conflicting death counts were found; deaths were left blank.")
    if injuries_status == "conflict":
        conflicts.append("Conflicting injury counts were found; injuries were left blank.")

    if accident_date:
        try:
            incident_year = date.fromisoformat(accident_date).year
        except ValueError:
            incident_year = None
    else:
        incident_year = None
    if district and district.casefold() != "dehradun":
        scope_status, scope_reason = "out_of_scope", f"District is {district}; target district is Dehradun."
    elif incident_year is not None and district and district.casefold() == "dehradun":
        scope_status, scope_reason = "in_scope", f"Incident date ({incident_year}) and Dehradun district were extracted."
    else:
        scope_status, scope_reason = "unknown", "Incident date or district could not be established from the article."

    values = {
        "accident_date": accident_date,
        "accident_time": accident_time,
        "district": district,
        "area": area,
        "accident_type": accident_type,
        "cause": cause,
        "vehicles_involved": ", ".join(vehicles) if vehicles else None,
        "deaths": deaths,
        "injuries": injuries,
        "weather": weather,
    }
    evidence = {
        field: {"value": value, "snippet": segment, "method": "rule-based-nlp"}
        for field, value in values.items()
        if value is not None
    }
    confidence_parts = [
        accident_date is not None, accident_time is not None, district is not None,
        area is not None, accident_type is not None, cause is not None,
        bool(vehicles), deaths is not None, injuries is not None, weather is not None,
    ]
    confidence = round(0.1 + 0.09 * sum(confidence_parts), 2)
    review_status = "needs_review" if conflicts or scope_status != "in_scope" else "pending"
    sources = {field: ("automatic" if value is not None else "unknown") for field, value in values.items()}
    return {
        **values,
        "cause_status": cause_status,
        "extraction_confidence": confidence,
        "review_status": review_status,
        "accident_date_status": date_status,
        "scope_status": scope_status,
        "scope_reason": scope_reason,
        "possible_duplicate": 0,
        "extracted_fields_json": json.dumps(values, ensure_ascii=False, sort_keys=True),
        "field_sources_json": json.dumps(sources, ensure_ascii=False, sort_keys=True),
        "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        "conflict_notes_json": json.dumps(conflicts, ensure_ascii=False),
    }


def extract_accidents(
    article_text: str,
    *,
    headline: str = "",
    published_date: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Extract event records without modifying the original article text.

    The publication date and language are accepted as context/metadata, but the
    publication date is never substituted for an incident date.
    """
    del language  # Reserved for optional language-specific model rules.
    if not is_accident_relevant(article_text):
        return []
    segments = segment_incidents(article_text)
    if not segments:
        return []
    if headline and segments:
        # Headline facts can supply casualty/vehicle details, but only for the
        # first event. Date extraction still requires incident wording.
        segments[0] = clean_text(headline) + ". " + segments[0]
    return [_extract_one(segment, published_date) for segment in segments]
