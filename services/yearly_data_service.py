"""Year-derived record management and synchronized yearly CSV exports."""

from __future__ import annotations

import logging
import json
import re
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import pandas as pd

from database import db
from .export_service import export_dataframe, write_dataframe_atomically


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSED_DIR = Path(os.environ.get("ACCIDENT_PROCESSED_DIR", PROJECT_ROOT / "data" / "processed"))
ALL_YEARS_EXPORT_PATH = Path(os.environ.get("ACCIDENT_ALL_YEARS_CSV", PROJECT_ROOT / "data" / "exports" / "all_years.csv"))
MISSING_TEXT = {"", "unknown", "n/a", "na", "null", "none", "nan"}
EDITABLE_RECORD_FIELDS = (
    "headline", "published_date", "language", "source_name", "source_url", "accident_date",
    "accident_time", "district", "area", "accident_type", "cause", "cause_status",
    "vehicles_involved", "deaths", "injuries", "weather", "review_status",
)


class YearlyCsvSyncError(RuntimeError):
    """The DB transaction committed, but one or more CSV snapshots failed."""

    def __init__(
        self,
        years: Iterable[int],
        failures: dict[int, str],
        *,
        operation_result: dict[str, Any] | None = None,
    ):
        self.years = tuple(sorted(set(years)))
        self.failures = failures
        self.operation_result = operation_result or {}
        details = "; ".join(f"{year}: {message}" for year, message in sorted(failures.items()))
        super().__init__(f"Database changes were saved, but yearly CSV synchronization failed ({details}).")


def validate_year(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Enter a four-digit year from 0001 to 9999.")
    if isinstance(value, int):
        if 1 <= value <= 9999:
            return value
        raise ValueError("Year must be between 0001 and 9999.")
    raw = str(value).strip()
    if not re.fullmatch(r"\d{4}", raw):
        raise ValueError("Enter a four-digit year from 0001 to 9999.")
    year = int(raw)
    if not 1 <= year <= 9999:
        raise ValueError("Year must be between 0001 and 9999.")
    return year


def yearly_csv_path(year: Any, processed_dir: str | Path | None = None) -> Path:
    normalized_year = validate_year(year)
    return Path(processed_dir or DEFAULT_PROCESSED_DIR).expanduser().resolve() / f"accidents_{normalized_year:04d}.csv"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    normalized = str(value).strip()
    if normalized.casefold() in MISSING_TEXT:
        return None
    return normalized


def _optional_date(value: Any, field_name: str) -> str | None:
    value = _optional_text(value)
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format or be blank if unknown.") from error


def _optional_count(value: Any, field_name: str) -> int | None:
    value = _optional_text(value)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (ValueError, TypeError) as error:
        try:
            as_float = float(value)
            if as_float.is_integer():
                parsed = int(as_float)
            else:
                raise ValueError
        except ValueError:
            raise ValueError(f"{field_name.capitalize()} must be a non-negative whole number or blank.") from error
    if parsed < 0:
        raise ValueError(f"{field_name.capitalize()} must be non-negative.")
    return parsed


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize import/form data; occurrence year is always derived."""
    normalized = dict(record)
    aliases = {"accident_cause": "cause", "casualties": "injuries", "article_url": "source_url", "source": "source_name"}
    for alias, canonical in aliases.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]

    headline = _optional_text(normalized.get("headline"))
    if not headline:
        raise ValueError("Headline is required.")
    result: dict[str, Any] = {"headline": headline}
    for field in ("published_date", "accident_date"):
        result[field] = _optional_date(normalized.get(field), field.replace("_", " ").capitalize())
    supplied_year = _optional_text(normalized.get("occurrence_year", normalized.get("year")))
    occurrence_year = int(result["accident_date"][:4]) if result["accident_date"] else None
    if supplied_year is not None:
        parsed_year = validate_year(supplied_year)
        if occurrence_year is None:
            raise ValueError("Occurrence year cannot be assigned without an accident date.")
        if parsed_year != occurrence_year:
            raise ValueError("Occurrence year conflicts with accident_date; the date determines the year.")
    result["occurrence_year"] = occurrence_year

    language = _optional_text(normalized.get("language"))
    if language:
        folded = language.casefold()
        if folded in {"en", "english"}:
            language = "English"
        elif folded in {"hi", "hindi"}:
            language = "Hindi"
        else:
            raise ValueError("Language must be English or Hindi.")
    result["language"] = language
    for field in (
        "source_name", "source_url", "accident_time", "district", "area", "accident_type",
        "cause", "cause_status", "vehicles_involved", "weather",
    ):
        result[field] = _optional_text(normalized.get(field))
    if result["accident_time"] and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", result["accident_time"]):
        raise ValueError("Accident time must use 24-hour HH:MM format or be blank if unknown.")
    if result["source_url"]:
        parsed_url = urlsplit(result["source_url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Source URL must be a valid HTTP or HTTPS URL, or be blank.")
    for field in ("deaths", "injuries"):
        result[field] = _optional_count(normalized.get(field), field)
    confidence = _optional_text(normalized.get("extraction_confidence"))
    if confidence is None:
        result["extraction_confidence"] = None
    else:
        try:
            confidence_value = float(confidence)
        except ValueError as error:
            raise ValueError("Extraction confidence must be between 0 and 1.") from error
        if not 0 <= confidence_value <= 1:
            raise ValueError("Extraction confidence must be between 0 and 1.")
        result["extraction_confidence"] = confidence_value

    review_status = _optional_text(normalized.get("review_status")) or "pending"
    if review_status not in {"pending", "verified", "needs_review"}:
        raise ValueError("Review status must be pending, needs_review, or verified.")
    result["review_status"] = review_status
    result["accident_date_status"] = _optional_text(normalized.get("accident_date_status")) or "unknown"
    district = result["district"]
    if district and district.casefold() != "dehradun":
        result["scope_status"] = "out_of_scope"
        result["scope_reason"] = f"District is {district}; target district is Dehradun."
    elif district and occurrence_year:
        result["scope_status"] = "in_scope"
        result["scope_reason"] = f"Incident date ({occurrence_year}) and Dehradun district are known."
    else:
        result["scope_status"] = "unknown"
        result["scope_reason"] = "Incident date or district could not be established."
    result["possible_duplicate"] = int(normalized.get("possible_duplicate") or 0)
    result["extracted_fields_json"] = normalized.get("extracted_fields_json") or "{}"
    result["field_sources_json"] = normalized.get("field_sources_json") or "{}"
    result["conflict_notes_json"] = normalized.get("conflict_notes_json") or "[]"
    article_id = _optional_text(normalized.get("article_id"))
    if article_id is not None:
        try:
            article_id = int(article_id)
        except ValueError as error:
            raise ValueError("Article ID must be a positive integer or blank.") from error
        if article_id < 1:
            raise ValueError("Article ID must be a positive integer or blank.")
    result["article_id"] = article_id
    result["record_key"] = _optional_text(normalized.get("record_key"))
    result["external_record_id"] = normalized.get("external_record_id") or None
    return result


def sync_year(year: Any, *, db_path=None, processed_dir=None) -> Path:
    year = validate_year(year)
    target = yearly_csv_path(year, processed_dir)
    dataframe = export_dataframe(year=year, year_basis="accident", db_path=db_path)
    return write_dataframe_atomically(dataframe, target)


def sync_years(years: Iterable[int], *, db_path=None, processed_dir=None) -> list[Path]:
    normalized_years = sorted({validate_year(year) for year in years})
    paths: list[Path] = []
    failures: dict[int, str] = {}
    for year in normalized_years:
        try:
            paths.append(sync_year(year, db_path=db_path, processed_dir=processed_dir))
        except Exception as error:
            LOGGER.exception("Failed to synchronize annual CSV for %s", year)
            failures[year] = str(error)
    if failures:
        raise YearlyCsvSyncError(normalized_years, failures)
    return paths


def _validate_selected_year(record: dict[str, Any], selected_year: int | None, year_basis: str) -> None:
    if selected_year is None:
        return
    selected_year = validate_year(selected_year)
    if year_basis == "accident":
        record_year = record.get("occurrence_year")
        if record_year is not None and int(record_year) != selected_year:
            raise ValueError(f"Accident date belongs to {record_year}, not selected occurrence year {selected_year}.")
    elif year_basis == "publication":
        published = record.get("published_date")
        if published and int(published[:4]) != selected_year:
            raise ValueError(f"Publication date belongs to {published[:4]}, not selected publication year {selected_year}.")
        if not published:
            raise ValueError("A publication date is required when validating by publication year.")
    else:
        raise ValueError("Year filter must use accident or publication date.")


def add_records(
    records: Iterable[dict[str, Any]],
    *,
    selected_year: Any = None,
    year_basis: str = "accident",
    db_path=None,
    processed_dir=None,
) -> dict[str, Any]:
    selected_year = validate_year(selected_year) if selected_year is not None else None
    prepared: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    unassigned = 0
    for index, raw in enumerate(records, start=1):
        try:
            normalized = normalize_record(raw)
            if normalized["article_id"] and not db.get_article(normalized["article_id"], db_path):
                raise ValueError(f"Article ID {normalized['article_id']} does not exist in the central database.")
            _validate_selected_year(normalized, selected_year, year_basis)
            if normalized["occurrence_year"] is None:
                unassigned += 1
            prepared.append(normalized)
        except ValueError as error:
            validation_errors.append(f"Record {index}: {error}")

    try:
        db_result = db.insert_accident_records(prepared, db_path) if prepared else {"inserted_ids": [], "skipped": 0, "years": set()}
    except Exception:
        LOGGER.exception("Accident record batch failed; SQLite transaction rolled back")
        raise
    years = set(db_result["years"])
    years.update(
        int(record["occurrence_year"])
        for record in prepared
        if record.get("occurrence_year") is not None
    )
    result = {
        "selected_year": selected_year,
        "saved": len(db_result["inserted_ids"]),
        "inserted_ids": db_result["inserted_ids"],
        "duplicates_skipped": db_result["skipped"],
        "validation_errors": validation_errors,
        "unassigned_year": unassigned,
        "affected_years": sorted(years),
    }
    try:
        paths = sync_years(years, db_path=db_path, processed_dir=processed_dir) if years else []
    except YearlyCsvSyncError as error:
        error.operation_result = result
        raise
    return {**result, "csv_paths": paths}


def add_record(
    record: dict[str, Any],
    *,
    selected_year: Any = None,
    year_basis: str = "accident",
    db_path=None,
    processed_dir=None,
) -> dict[str, Any]:
    return add_records(
        [record], selected_year=selected_year, year_basis=year_basis,
        db_path=db_path, processed_dir=processed_dir,
    )


def list_records(year: Any = None, *, year_basis: str = "accident", db_path=None) -> list[dict[str, Any]]:
    normalized_year = validate_year(year) if year is not None else None
    return db.list_accident_records(year=normalized_year, year_basis=year_basis, path=db_path)


def available_years(*, year_basis: str = "accident", db_path=None) -> list[int]:
    return db.list_record_years(year_basis=year_basis, path=db_path)


def update_record(
    accident_id: int,
    updates: dict[str, Any],
    *,
    db_path=None,
    processed_dir=None,
    field_sources_json: str | None = None,
) -> dict[str, Any]:
    current = db.get_accident_record(accident_id, db_path)
    if not current:
        raise ValueError(f"Accident record {accident_id} was not found.")
    merged = {field: current.get(field) for field in EDITABLE_RECORD_FIELDS}
    merged.update({key: value for key, value in updates.items() if key in EDITABLE_RECORD_FIELDS})
    normalized = normalize_record(merged)
    values = {field: normalized.get(field) for field in EDITABLE_RECORD_FIELDS}
    values.update({
        "scope_status": normalized["scope_status"],
        "scope_reason": normalized["scope_reason"],
        "accident_date_status": normalized["accident_date_status"],
    })
    if normalized["accident_date"] != current.get("accident_date"):
        values["accident_date_status"] = "manual" if normalized["accident_date"] else "unknown"
    if normalized["cause"] != current.get("cause"):
        values["cause_status"] = "manual" if normalized["cause"] else "unknown"
    if field_sources_json is None:
        try:
            extracted = json.loads(current.get("extracted_fields_json") or "{}")
        except json.JSONDecodeError:
            extracted = {}
        sources = {}
        for field in (
            "accident_date", "accident_time", "district", "area", "accident_type", "cause",
            "vehicles_involved", "deaths", "injuries", "weather",
        ):
            automatic = extracted.get(field)
            manual = normalized.get(field)
            if str(automatic) != str(manual) and (automatic is not None or manual is not None):
                sources[field] = "manual"
            elif manual is None:
                sources[field] = "unknown"
            else:
                sources[field] = "automatic"
        field_sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    try:
        old_year, new_year = db.update_accident_record(
            accident_id, values, field_sources_json=field_sources_json, path=db_path
        )
    except Exception:
        LOGGER.exception("Accident record update failed for %s", accident_id)
        raise
    years = {year for year in (old_year, new_year) if year is not None}
    result = {"accident_id": accident_id, "old_year": old_year, "new_year": new_year}
    try:
        paths = sync_years(years, db_path=db_path, processed_dir=processed_dir) if years else []
    except YearlyCsvSyncError as error:
        error.operation_result = result
        raise
    return {**result, "csv_paths": paths}


def delete_record(accident_id: int, *, db_path=None, processed_dir=None) -> dict[str, Any]:
    try:
        year = db.delete_accident_record(accident_id, db_path)
    except Exception:
        LOGGER.exception("Accident record deletion failed for %s", accident_id)
        raise
    result = {"accident_id": accident_id, "deleted_year": year}
    try:
        paths = sync_years([year], db_path=db_path, processed_dir=processed_dir) if year is not None else []
    except YearlyCsvSyncError as error:
        error.operation_result = result
        raise
    return {**result, "csv_paths": paths}


def import_csv(
    csv_file: str | Path,
    *,
    selected_year: Any = None,
    year_basis: str = "accident",
    db_path=None,
    processed_dir=None,
) -> dict[str, Any]:
    dataframe = pd.read_csv(csv_file, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    rows = dataframe.to_dict(orient="records")
    return add_records(
        rows, selected_year=selected_year, year_basis=year_basis,
        db_path=db_path, processed_dir=processed_dir,
    )


def export_year(
    year: Any,
    *,
    output_path: str | Path | None = None,
    db_path=None,
) -> Path:
    year = validate_year(year)
    target = Path(output_path) if output_path else yearly_csv_path(year)
    dataframe = export_dataframe(year=year, year_basis="accident", db_path=db_path)
    return write_dataframe_atomically(dataframe, target)


def export_all_years(
    *,
    output_path: str | Path | None = None,
    db_path=None,
) -> Path:
    dataframe = export_dataframe(db_path=db_path)
    target = Path(output_path) if output_path else ALL_YEARS_EXPORT_PATH
    return write_dataframe_atomically(dataframe, target)
