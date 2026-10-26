"""OCR-backed screenshot drafts with human approval into the central data service."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from database import db
from database.db import DuplicateArticleError, DuplicateScreenshotError
from nlp.pipeline import extract_accidents
from . import yearly_data_service
from .yearly_data_service import YearlyCsvSyncError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCREENSHOT_DIR = Path(
    os.environ.get("ACCIDENT_SCREENSHOT_DIR", PROJECT_ROOT / "data" / "screenshots")
)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000


class OCRUnavailableError(RuntimeError):
    """Raised when Tesseract or the English/Hindi language data is unavailable."""


class ScreenshotValidationError(ValueError):
    """Raised when an uploaded screenshot is invalid or unsupported."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tesseract_binary() -> str:
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        return configured
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    candidates = (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"D:\tesseract\tesseract.exe"),
        Path("/usr/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise OCRUnavailableError(
        "Tesseract OCR was not found. Install it with English and Hindi language data, "
        "or set TESSERACT_CMD to the executable path."
    )


def tesseract_languages() -> set[str]:
    binary = _tesseract_binary()
    try:
        result = subprocess.run(
            [binary, "--list-langs"], capture_output=True, check=True,
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OCRUnavailableError(f"Could not inspect Tesseract language data: {error}") from error
    return {line.strip() for line in result.stdout.splitlines()[1:] if line.strip()}


def _run_tesseract(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, capture_output=True, check=False, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OCRUnavailableError(f"Tesseract OCR could not process this image: {error}") from error


def _read_confidence(tsv_text: str) -> float | None:
    values: list[float] = []
    for row in csv.DictReader(io.StringIO(tsv_text), delimiter="\t"):
        raw = row.get("conf", "")
        try:
            confidence = float(raw)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            values.append(confidence)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _preprocess_image(image_bytes: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ScreenshotValidationError("Image is too large to process safely.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image, cutoff=1)
        largest_side = max(image.size)
        if largest_side < 1800:
            scale = min(2.0, 1800 / largest_side)
            image = image.resize(
                (round(image.width * scale), round(image.height * scale)),
                Image.Resampling.LANCZOS,
            )
        return image
    except ScreenshotValidationError:
        raise
    except Exception as error:
        raise ScreenshotValidationError(f"Could not read image: {error}") from error


def ocr_image(image_bytes: bytes, filename: str = "screenshot.png") -> dict[str, Any]:
    """Preprocess and OCR one image; retain low-confidence and unreadable outcomes."""
    try:
        binary = _tesseract_binary()
        available = tesseract_languages()
        missing = {"eng", "hin"} - available
        if missing:
            raise OCRUnavailableError(
                "Tesseract is missing required language data: " + ", ".join(sorted(missing))
            )
        image = _preprocess_image(image_bytes)
        with tempfile.TemporaryDirectory(prefix="accident-ocr-") as temp_dir:
            input_path = Path(temp_dir) / "preprocessed.png"
            image.save(input_path, format="PNG", dpi=(300, 300))

            # OSD is best-effort: sparse or blurry pages may not have enough signal.
            orientation = _run_tesseract(
                [binary, str(input_path), "stdout", "-l", "osd", "--psm", "0"], timeout=20
            )
            if orientation.returncode == 0:
                match = re.search(r"Rotate:\s*(\d+)", orientation.stdout)
                if match:
                    degrees = int(match.group(1)) % 360
                    if degrees:
                        image = image.rotate(-degrees, expand=True)
                        image.save(input_path, format="PNG", dpi=(300, 300))

            text_result = _run_tesseract(
                [binary, str(input_path), "stdout", "-l", "eng+hin", "--psm", "3"]
            )
            if text_result.returncode != 0:
                error_text = (text_result.stderr or "Tesseract returned an error.").strip()
                return {
                    "raw_ocr_text": "", "ocr_confidence": None,
                    "readability_status": "unreadable", "ocr_error": error_text,
                }
            confidence_result = _run_tesseract(
                [binary, str(input_path), "stdout", "-l", "eng+hin", "--psm", "3", "tsv"]
            )
            raw_text = text_result.stdout.rstrip("\r\n")
            confidence = _read_confidence(confidence_result.stdout)
            useful_chars = re.findall(r"[A-Za-z0-9\u0900-\u097f]", raw_text)
            if len(useful_chars) < 8 or not raw_text.strip():
                status = "unreadable"
            elif confidence is None or confidence < 50:
                status = "uncertain"
            else:
                status = "readable"
            return {
                "raw_ocr_text": raw_text,
                "ocr_confidence": confidence,
                "readability_status": status,
                "ocr_error": None,
            }
    except OCRUnavailableError as error:
        return {
            "raw_ocr_text": "", "ocr_confidence": None,
            "readability_status": "unreadable", "ocr_error": str(error),
        }


def _validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata)
    for field in ("headline", "source_name", "source_url"):
        value = cleaned.get(field)
        cleaned[field] = str(value).strip() or None if value is not None else None
    published = cleaned.get("published_date")
    if published:
        try:
            cleaned["published_date"] = date.fromisoformat(str(published).strip()).isoformat()
        except ValueError as error:
            raise ValueError("Publication date must use YYYY-MM-DD format, or be blank if unknown.") from error
    else:
        cleaned["published_date"] = None
    language = cleaned.get("language") or None
    if language not in {None, "English", "Hindi"}:
        raise ValueError("Language must be English or Hindi, or unknown.")
    cleaned["language"] = language
    source_url = cleaned.get("source_url")
    if source_url:
        parts = urlsplit(source_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("Source URL must be a valid HTTP or HTTPS URL, or be blank.")
    return cleaned


def create_draft(
    uploads: list[tuple[str, bytes]],
    metadata: dict[str, Any],
    *,
    image_root: str | Path | None = None,
    db_path: str | Path | None = None,
    ocr_function: Callable[[bytes, str], dict[str, Any]] | None = None,
) -> str:
    """Save original images, OCR output and article metadata as one draft."""
    if not uploads:
        raise ScreenshotValidationError("Upload at least one JPG, JPEG, or PNG screenshot.")
    cleaned_metadata = _validate_metadata(metadata)
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for filename, content in uploads:
        suffix = Path(filename).suffix.casefold()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ScreenshotValidationError(f"Unsupported image type for {filename!r}; use JPG, JPEG, or PNG.")
        if not content:
            raise ScreenshotValidationError(f"{filename!r} is empty.")
        if len(content) > MAX_IMAGE_BYTES:
            raise ScreenshotValidationError(f"{filename!r} exceeds the 25 MB per-image limit.")
        # Verify that the bytes really contain an image before OCR or disk writes.
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except Exception as error:
            raise ScreenshotValidationError(f"{filename!r} is not a readable JPG, JPEG, or PNG image.") from error
        image_hash = hashlib.sha256(content).hexdigest()
        if image_hash in seen:
            raise ScreenshotValidationError(f"{filename!r} duplicates another image in this upload batch.")
        seen.add(image_hash)
        existing = db.find_screenshot_by_hash(image_hash, db_path)
        if existing:
            raise DuplicateScreenshotError(existing)
        prepared.append({
            "original_filename": Path(filename).name,
            "suffix": suffix,
            "content": content,
            "image_sha256": image_hash,
        })

    screenshot_article_id = str(uuid.uuid4())
    root = Path(image_root or DEFAULT_SCREENSHOT_DIR).expanduser().resolve()
    article_dir = root / screenshot_article_id
    page_data: list[dict[str, Any]] = []
    raw_outputs: list[str] = []
    ocr = ocr_function or ocr_image
    try:
        article_dir.mkdir(parents=True, exist_ok=False)
        for index, page in enumerate(prepared, start=1):
            target = article_dir / f"page-{index:03d}{page['suffix']}"
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(page["content"])
            os.replace(temporary, target)
            ocr_result = ocr(page["content"], page["original_filename"])
            page_data.append({
                "page_order": index,
                "original_filename": page["original_filename"],
                "image_path": str(target),
                "image_sha256": page["image_sha256"],
                "raw_ocr_text": ocr_result.get("raw_ocr_text") or "",
                "ocr_confidence": ocr_result.get("ocr_confidence"),
                "readability_status": ocr_result.get("readability_status", "unreadable"),
                "ocr_error": ocr_result.get("ocr_error"),
                "created_at": _now(),
            })
            raw_outputs.append(ocr_result.get("raw_ocr_text") or "")
        aggregate = "\n\n".join(
            f"[Screenshot {index}: {page['original_filename']}]\n{raw_outputs[index - 1]}"
            for index, page in enumerate(prepared, start=1)
        )
        now = _now()
        db.insert_screenshot_draft(
            {
                "screenshot_article_id": screenshot_article_id,
                **cleaned_metadata,
                "raw_ocr_text": aggregate,
                "reviewed_article_text": aggregate,
                "extraction_method": "tesseract-eng+hin",
                "created_at": now,
                "updated_at": now,
            },
            page_data,
            db_path,
        )
    except Exception:
        shutil.rmtree(article_dir, ignore_errors=True)
        raise
    return screenshot_article_id


def get_draft(screenshot_article_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    draft = db.get_screenshot_draft(screenshot_article_id, db_path)
    if not draft:
        return None
    for row in draft["records"]:
        row["record"] = json.loads(row.pop("record_json") or "{}")
        row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
        row["conflicts"] = json.loads(row.pop("conflict_notes_json") or "[]")
    return draft


def list_drafts(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    return db.list_screenshot_drafts(db_path)


def save_draft(
    screenshot_article_id: str,
    updates: dict[str, Any],
    db_path: str | Path | None = None,
) -> None:
    draft = db.get_screenshot_draft(screenshot_article_id, db_path)
    if not draft:
        raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")
    if draft.get("central_article_id") is not None:
        raise ValueError("Article metadata and reviewed text are locked after the first incident is approved.")
    normalized = _validate_metadata(updates)
    reviewed_text = updates.get("reviewed_article_text")
    if not isinstance(reviewed_text, str):
        raise ValueError("Reviewed article text must be text.")
    normalized["reviewed_article_text"] = reviewed_text
    db.update_screenshot_draft(screenshot_article_id, normalized, db_path)


def extract_draft_records(
    screenshot_article_id: str, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    draft = db.get_screenshot_draft(screenshot_article_id, db_path)
    if not draft:
        raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")
    if not (draft.get("reviewed_article_text") or "").strip():
        raise ValueError("Reviewed article text is empty. Correct or enter text before extraction.")
    extracted = extract_accidents(
        draft["reviewed_article_text"],
        headline=draft.get("headline") or "",
        published_date=draft.get("published_date"),
        language=draft.get("language"),
    )
    records: list[dict[str, Any]] = []
    for index, record in enumerate(extracted, start=1):
        item = {
            "headline": draft.get("headline") or "",
            "published_date": draft.get("published_date"),
            "language": draft.get("language"),
            "source_name": draft.get("source_name"),
            "source_url": draft.get("source_url"),
            **record,
            "review_status": "pending",
            "record_key": f"screenshot-{screenshot_article_id}-incident-{index}",
        }
        records.append({
            "record": item,
            "evidence": json.loads(record.get("evidence_json") or "{}"),
            "conflicts": json.loads(record.get("conflict_notes_json") or "[]"),
        })
    db.insert_screenshot_draft_records(screenshot_article_id, records, db_path)
    saved_draft = get_draft(screenshot_article_id, db_path)
    return saved_draft["records"] if saved_draft else []


def save_draft_record(
    screenshot_record_id: int,
    updates: dict[str, Any],
    *,
    status: str = "draft",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    current = db.get_screenshot_draft_record(screenshot_record_id, db_path)
    if not current:
        raise ValueError(f"Screenshot record {screenshot_record_id} was not found.")
    if current["status"] == "approved":
        raise ValueError("An approved screenshot record cannot be edited here.")
    if status not in {"draft", "rejected"}:
        raise ValueError("A screenshot record can be saved as draft or rejected.")
    values = json.loads(current["record_json"] or "{}")
    allowed = {
        "headline", "published_date", "language", "source_name", "source_url",
        "accident_date", "accident_time", "state", "district", "area", "accident_type", "cause",
        "cause_status", "vehicles_involved", "deaths", "injuries", "weather",
    }
    values.update({key: value for key, value in updates.items() if key in allowed})
    normalized = yearly_data_service.normalize_record(values, require_headline=False)
    if normalized["accident_date"] != values.get("accident_date"):
        normalized["accident_date_status"] = "manual" if normalized["accident_date"] else "unknown"
    if normalized["cause"] != values.get("cause"):
        normalized["cause_status"] = "manual" if normalized["cause"] else "unknown"
    try:
        extracted_values = json.loads(values.get("extracted_fields_json") or "{}")
    except json.JSONDecodeError:
        extracted_values = {}
    sources = {}
    for field in (
        "accident_date", "accident_time", "state", "district", "area", "accident_type", "cause",
        "vehicles_involved", "deaths", "injuries", "weather",
    ):
        automatic = extracted_values.get(field)
        reviewed = normalized.get(field)
        if str(automatic) != str(reviewed) and (automatic is not None or reviewed is not None):
            sources[field] = "manual"
        elif reviewed is None:
            sources[field] = "unknown"
        else:
            sources[field] = "automatic"
    normalized["field_sources_json"] = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    db.update_screenshot_draft_record(
        screenshot_record_id,
        record_json=json.dumps(normalized, ensure_ascii=False, sort_keys=True),
        status=status,
        path=db_path,
    )
    return normalized


def approve_draft_record(
    screenshot_record_id: int,
    *,
    db_path: str | Path | None = None,
    processed_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Promote one reviewed draft through the existing central yearly data service."""
    draft_record = db.get_screenshot_draft_record(screenshot_record_id, db_path)
    if not draft_record:
        raise ValueError(f"Screenshot record {screenshot_record_id} was not found.")
    if draft_record["status"] == "approved" and draft_record.get("central_accident_id"):
        return {"accident_id": int(draft_record["central_accident_id"]), "already_approved": True}
    if draft_record["status"] == "rejected":
        raise ValueError("A rejected screenshot record cannot be approved. Save it as a draft first.")

    screenshot_article_id = draft_record["screenshot_article_id"]
    draft = db.get_screenshot_draft(screenshot_article_id, db_path)
    if not draft:
        raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")

    central_article_id = draft.get("central_article_id")
    if central_article_id is None:
        central_article = {
            "headline": draft.get("headline"),
            "article_text": draft.get("reviewed_article_text") or "",
            "source_name": draft.get("source_name"),
            "source_url": draft.get("source_url"),
            "published_date": draft.get("published_date"),
            "language": draft.get("language"),
        }
        try:
            central_article_id = db.insert_article(central_article, db_path)
        except DuplicateArticleError as duplicate:
            central_article_id = duplicate.existing_article_id
        db.set_screenshot_article_central(
            screenshot_article_id, int(central_article_id), status="draft", path=db_path
        )

    record = json.loads(draft_record["record_json"] or "{}")
    record["article_id"] = int(central_article_id)
    record["record_key"] = f"screenshot-{screenshot_article_id}-incident-{draft_record['record_number']}"
    record["review_status"] = "verified"
    record["evidence_json"] = draft_record.get("evidence_json") or "{}"
    record["conflict_notes_json"] = draft_record.get("conflict_notes_json") or "[]"

    sync_error: YearlyCsvSyncError | None = None
    try:
        result = yearly_data_service.add_record(
            record,
            allow_unknown_headline=True,
            db_path=db_path,
            processed_dir=processed_dir,
        )
    except YearlyCsvSyncError as error:
        sync_error = error
        result = error.operation_result
    saved_record = db.get_accident_record_by_key(record["record_key"], db_path)
    if not saved_record:
        if result.get("validation_errors"):
            raise ValueError("Approval failed validation: " + "; ".join(result["validation_errors"]))
        raise RuntimeError("The central database did not return the approved record.")

    db.update_screenshot_draft_record(
        screenshot_record_id,
        record_json=json.dumps(record, ensure_ascii=False, sort_keys=True),
        status="approved",
        central_accident_id=int(saved_record["accident_id"]),
        path=db_path,
    )
    db.set_screenshot_article_status(screenshot_article_id, "approved", db_path)
    if sync_error:
        raise sync_error
    return {**result, "accident_id": int(saved_record["accident_id"])}


def reject_draft_article(
    screenshot_article_id: str, db_path: str | Path | None = None
) -> None:
    draft = db.get_screenshot_draft(screenshot_article_id, db_path)
    if not draft:
        raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")
    if draft.get("central_article_id") is not None:
        raise ValueError("This article already has approved records and cannot be rejected as a whole.")
    db.set_screenshot_article_status(screenshot_article_id, "rejected", db_path)

