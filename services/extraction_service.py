"""Extraction persistence and human-review updates."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from database import db
from nlp.pipeline import extract_accidents


def extract_and_save_article(article_id: int, path=None) -> list[dict[str, Any]]:
    article = db.get_article(article_id, path)
    if not article:
        raise ValueError(f"Article {article_id} was not found.")
    existing = db.list_accident_records(article_id, path=path)
    if existing:
        return existing
    extracted = extract_accidents(
        article["article_text"],
        headline=article["headline"],
        published_date=article["published_date"],
        language=article["language"],
    )
    new_ids = [db.insert_accident_record(article_id, record, path) for record in extracted]
    return [db.get_accident_record(record_id, path) for record_id in new_ids]


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def save_review(
    accident_id: int,
    updates: dict[str, Any],
    *,
    review_status: str,
    path=None,
) -> None:
    current = db.get_accident_record(accident_id, path)
    if not current:
        raise ValueError(f"Accident record {accident_id} was not found.")
    if review_status not in {"pending", "verified", "needs_review"}:
        raise ValueError("Choose pending, verified, or needs_review as the review status.")

    normalized: dict[str, Any] = {}
    for field in ("accident_date", "accident_time", "district", "area", "accident_type", "cause", "vehicles_involved", "weather"):
        normalized[field] = _clean_optional_text(updates.get(field))
    incident_date = normalized["accident_date"]
    if incident_date:
        try:
            incident_date = date.fromisoformat(incident_date).isoformat()
        except ValueError as error:
            raise ValueError("Incident date must use YYYY-MM-DD format, or be blank if unknown.") from error
        normalized["accident_date"] = incident_date
    incident_time = normalized["accident_time"]
    if incident_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", incident_time):
        raise ValueError("Incident time must use 24-hour HH:MM format, or be blank if unknown.")
    for field in ("deaths", "injuries"):
        raw = updates.get(field)
        raw = "" if raw is None else str(raw).strip()
        if not raw:
            normalized[field] = None
        elif raw.isdecimal() and int(raw) >= 0:
            normalized[field] = int(raw)
        else:
            raise ValueError(f"{field.capitalize()} must be a non-negative whole number or blank if unknown.")
    normalized["review_status"] = review_status

    try:
        event_year = date.fromisoformat(incident_date).year if incident_date else None
    except ValueError:
        event_year = None
    district = normalized["district"]
    if event_year is not None and event_year != 2025:
        scope_status = "out_of_scope"
        scope_reason = f"Incident year is {event_year}; target year is 2025."
    elif district and district.casefold() != "dehradun":
        scope_status = "out_of_scope"
        scope_reason = f"District is {district}; target district is Dehradun."
    elif event_year == 2025 and district and district.casefold() == "dehradun":
        scope_status = "in_scope"
        scope_reason = "Incident date and Dehradun district match the project scope."
    else:
        scope_status = "unknown"
        scope_reason = "Incident date or district has not been verified."
    normalized["scope_status"] = scope_status
    normalized["scope_reason"] = scope_reason
    if normalized["accident_date"] != current["accident_date"]:
        normalized["accident_date_status"] = "manual" if normalized["accident_date"] else "unknown"
    if normalized["cause"] != current["cause"]:
        normalized["cause_status"] = "manual" if normalized["cause"] else "unknown"

    try:
        extracted_values = json.loads(current.get("extracted_fields_json") or "{}")
    except json.JSONDecodeError:
        extracted_values = {}
    fields_for_source = (
        "accident_date", "accident_time", "district", "area", "accident_type", "cause",
        "vehicles_involved", "deaths", "injuries", "weather",
    )
    sources = {}
    for field in fields_for_source:
        automatic = extracted_values.get(field)
        manual = normalized.get(field)
        if str(manual) != str(automatic) and (manual is not None or automatic is not None):
            sources[field] = "manual"
        elif manual is None:
            sources[field] = "unknown"
        else:
            sources[field] = "automatic"

    db.update_accident_record(
        accident_id,
        normalized,
        field_sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True),
        path=path,
    )
