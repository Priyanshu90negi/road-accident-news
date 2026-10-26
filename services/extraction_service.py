"""Extraction persistence and human-review updates."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from database import db
from nlp.accident_detector import is_accident_relevant
from nlp.pipeline import extract_accidents
from . import yearly_data_service


LOGGER = logging.getLogger(__name__)


def extract_and_save_article(
    article_id: int,
    path=None,
    *,
    processed_dir=None,
    diagnostics: list[str] | None = None,
) -> list[dict[str, Any]]:
    article = db.get_article(article_id, path)
    if not article:
        LOGGER.error("Selected article id=%s did not resolve to a database article", article_id)
        raise ValueError(f"Article {article_id} was not found.")
    LOGGER.info(
        "Resolved extraction selection article_id=%s stored_article_id=%s headline=%r source=%r "
        "publication_date=%r language=%r",
        article_id,
        article.get("article_id"),
        article.get("headline"),
        article.get("source_name"),
        article.get("published_date"),
        article.get("language"),
    )
    existing = db.list_accident_records(article_id, path=path)
    if existing:
        return existing
    article_text = article.get("article_text") or ""
    if not isinstance(article_text, str) or not article_text.strip():
        message = f"Article {article_id} has empty article text; extraction was not run."
        LOGGER.warning(message)
        if diagnostics is not None:
            diagnostics.append(message)
        return []
    LOGGER.debug(
        "Exact article text passed to NLP; article_id=%s chars=%s text=%r",
        article_id,
        len(article_text),
        article_text,
    )
    try:
        extracted = extract_accidents(
            article_text,
            headline=article["headline"],
            published_date=article["published_date"],
            language=article["language"],
        )
    except Exception as error:
        message = f"Extraction failed for article {article_id}: {error}"
        LOGGER.exception("Extraction failed for article %s", article_id)
        if diagnostics is not None:
            diagnostics.append(message)
        return []
    output_type = type(extracted).__name__
    output_count = len(extracted) if isinstance(extracted, list) else "n/a"
    output_shapes = [
        sorted(str(key) for key in record.keys()) if isinstance(record, dict) else type(record).__name__
        for record in extracted[:5]
    ] if isinstance(extracted, list) else []
    LOGGER.info(
        "Extractor returned article_id=%s output_type=%s count=%s first_item_shapes=%r",
        article_id,
        output_type,
        output_count,
        output_shapes,
    )
    if not isinstance(extracted, list):
        message = (
            "Invalid output format: the extractor must return a list of record dictionaries; "
            f"it returned {output_type}."
        )
        LOGGER.error("%s article_id=%s", message, article_id)
        if diagnostics is not None:
            diagnostics.append(message)
        return []
    invalid_items = [index for index, record in enumerate(extracted, start=1) if not isinstance(record, dict)]
    for index in invalid_items:
        message = f"Invalid output format: extracted item {index} is not a record dictionary."
        LOGGER.error("%s article_id=%s", message, article_id)
        if diagnostics is not None:
            diagnostics.append(message)
    extracted = [record for record in extracted if isinstance(record, dict)]
    if invalid_items and not extracted:
        message = "Invalid output format: the extractor returned no valid record dictionaries."
        LOGGER.error("%s article_id=%s", message, article_id)
        if diagnostics is not None:
            diagnostics.append(message)
        return []
    if not extracted:
        try:
            detected = is_accident_relevant(article_text)
        except Exception as error:
            message = f"Extraction exception while checking an empty extractor result: {error}"
            LOGGER.exception("Accident detection diagnostic failed for article %s", article_id)
            if diagnostics is not None:
                diagnostics.append(message)
            return []
        if detected:
            message = (
                "Extraction returned zero records after accident detection; no record reached validation. "
                "Check the incident segmentation and parsing log."
            )
            LOGGER.warning("Article %s matched accident detection but yielded no records", article_id)
        else:
            message = "Accident not detected in the article text; no record reached validation."
            LOGGER.info("Article %s rejected because no accident event was detected", article_id)
        if diagnostics is not None:
            diagnostics.append(message)
        return []
    batch = [
        {
            "article_id": article_id,
            "record_key": f"article-{article_id}-incident-{index + 1}",
            "headline": article["headline"],
            "published_date": article["published_date"],
            "language": article["language"],
            "source_name": article["source_name"],
            "source_url": article["source_url"],
            **record,
        }
        for index, record in enumerate(extracted)
    ]
    result = yearly_data_service.add_records(batch, db_path=path, processed_dir=processed_dir)
    for error in result["validation_errors"]:
        message = f"Record rejected during validation: {error}"
        LOGGER.warning("%s article_id=%s", message, article_id)
        if diagnostics is not None:
            diagnostics.append(message)
    if not result["saved"]:
        message = (
            f"Extraction succeeded with {len(extracted)} record(s), but zero records were accepted; "
            f"validation rejections={len(result['validation_errors'])}, "
            f"duplicates skipped={result['duplicates_skipped']}."
        )
        LOGGER.warning("%s article_id=%s", message, article_id)
        if diagnostics is not None:
            diagnostics.append(message)
    else:
        LOGGER.info(
            "Extraction accepted article_id=%s records=%s validation_rejections=%s duplicates=%s",
            article_id,
            result["saved"],
            len(result["validation_errors"]),
            result["duplicates_skipped"],
        )
    if result["saved"]:
        return [db.get_accident_record(record_id, path) for record_id in result["inserted_ids"]]
    return db.list_accident_records(article_id, path=path)


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
    processed_dir=None,
) -> None:
    current = db.get_accident_record(accident_id, path)
    if not current:
        raise ValueError(f"Accident record {accident_id} was not found.")
    if review_status not in {"pending", "verified", "needs_review"}:
        raise ValueError("Choose pending, verified, or needs_review as the review status.")

    normalized: dict[str, Any] = {}
    for field in ("accident_date", "accident_time", "state", "district", "area", "accident_type", "cause", "vehicles_involved", "weather"):
        normalized[field] = _clean_optional_text(updates.get(field))
    if not normalized["state"] and (normalized["district"] or "").casefold() == "dehradun":
        normalized["state"] = "Uttarakhand"
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
    if district and district.casefold() != "dehradun":
        scope_status = "out_of_scope"
        scope_reason = f"District is {district}; target district is Dehradun."
    elif event_year is not None and district and district.casefold() == "dehradun":
        scope_status = "in_scope"
        scope_reason = f"Incident date ({event_year}) and Dehradun district were checked."
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
        "accident_date", "accident_time", "state", "district", "area", "accident_type", "cause",
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

    yearly_data_service.update_record(
        accident_id,
        normalized,
        field_sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True),
        db_path=path,
        processed_dir=processed_dir,
    )
