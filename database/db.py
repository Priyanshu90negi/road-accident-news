"""SQLite persistence for source articles and all-years accident records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "accident_news.db"
RECORD_FIELDS = (
    "headline", "published_date", "language", "source_name", "source_url", "source_url_key",
    "accident_date", "occurrence_year", "accident_time", "state", "district", "area", "accident_type",
    "cause", "cause_status", "vehicles_involved", "deaths", "injuries", "weather",
    "extraction_confidence", "review_status", "accident_date_status", "scope_status",
    "scope_reason", "possible_duplicate", "extracted_fields_json", "field_sources_json",
    "evidence_json", "conflict_notes_json",
)
EDITABLE_FIELDS = frozenset({
    "headline", "published_date", "language", "source_name", "source_url", "source_url_key",
    "accident_date", "accident_time", "state", "district", "area", "accident_type", "cause",
    "cause_status", "vehicles_involved", "deaths", "injuries", "weather", "review_status",
    "accident_date_status", "scope_status", "scope_reason",
})


class DuplicateArticleError(ValueError):
    """Raised when either an article URL or normalized article text already exists."""

    def __init__(self, existing_article_id: int):
        self.existing_article_id = existing_article_id
        super().__init__(f"This article duplicates article #{existing_article_id}.")


class DuplicateRecordError(ValueError):
    """Raised by single-record insert when an equivalent record already exists."""


class DuplicateScreenshotError(ValueError):
    """Raised when an uploaded image was already attached to a screenshot article."""

    def __init__(self, existing_screenshot_article_id: str):
        self.existing_screenshot_article_id = existing_screenshot_article_id
        super().__init__(
            "This screenshot was already uploaded to article "
            f"{existing_screenshot_article_id}."
        )


def database_path(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get("ACCIDENT_NEWS_DB") or DEFAULT_DB_PATH
    return Path(configured).expanduser().resolve()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_file = database_path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_file, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _schema_statements() -> list[str]:
    script = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _derive_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).year
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_text_hash(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalized_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.casefold()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), host, path, parts.query, ""))


def _record_key(record: dict[str, Any], article_id: int | None) -> str:
    supplied = record.get("record_key") or record.get("external_record_id")
    if supplied:
        return str(supplied)
    url_key = record.get("source_url_key")
    if not url_key and record.get("source_url"):
        url_key = _normalized_url(str(record["source_url"]))
    identity = {
        "accident_date": record.get("accident_date"),
        "accident_time": record.get("accident_time"),
        "district": (record.get("district") or "").strip().casefold(),
        "area": (record.get("area") or "").strip().casefold(),
        "accident_type": (record.get("accident_type") or "").strip().casefold(),
        "vehicles_involved": (record.get("vehicles_involved") or "").strip().casefold(),
        "deaths": record.get("deaths"),
        "injuries": record.get("injuries"),
        "headline": re.sub(r"\s+", " ", (record.get("headline") or "").strip()).casefold(),
    }
    basis = {"source_url": url_key, "article_id": article_id if not url_key else None, "record": identity}
    serialized = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _create_record_table_sql(statements: list[str]) -> str:
    return next(
        statement for statement in statements
        if statement.casefold().startswith("create table if not exists accident_records")
    )


def _migrate_article_table(connection: sqlite3.Connection, statements: list[str]) -> None:
    """Allow unknown screenshot metadata while preserving existing article rows and IDs."""
    info = connection.execute("PRAGMA table_info(news_articles)").fetchall()
    if not info:
        return
    columns = {row[1]: row for row in info}
    optional_fields = ("headline", "source_name", "source_url", "source_url_key", "published_date", "language")
    if all(field in columns and not columns[field][3] for field in optional_fields):
        return

    required_columns = {
        "article_id", "headline", "article_text", "source_name", "source_url",
        "source_url_key", "article_text_hash", "published_date", "language", "created_at",
    }
    if not required_columns.issubset(columns):
        raise sqlite3.DatabaseError("Cannot migrate news_articles: required columns are missing.")

    create_sql = next(
        statement for statement in statements
        if statement.casefold().startswith("create table if not exists news_articles")
    ).replace("CREATE TABLE IF NOT EXISTS news_articles", "CREATE TABLE news_articles_migrating", 1)
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE IF EXISTS news_articles_migrating")
        connection.execute(create_sql)
        connection.execute(
            """INSERT INTO news_articles_migrating
               (article_id, headline, article_text, source_name, source_url, source_url_key,
                article_text_hash, published_date, language, created_at)
               SELECT article_id, headline, article_text, source_name, source_url, source_url_key,
                      article_text_hash, published_date, language, created_at
               FROM news_articles"""
        )
        connection.execute("DROP TABLE news_articles")
        connection.execute("ALTER TABLE news_articles_migrating RENAME TO news_articles")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_evidence_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accident_records)")}
    if columns and "evidence_json" not in columns:
        connection.execute(
            "ALTER TABLE accident_records ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_state_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accident_records)")}
    if columns and "state" not in columns:
        connection.execute("ALTER TABLE accident_records ADD COLUMN state TEXT")
        columns.add("state")
    if "state" in columns:
        connection.execute(
            "UPDATE accident_records SET state = 'Uttarakhand' "
            "WHERE state IS NULL AND lower(trim(coalesce(district, ''))) = 'dehradun'"
        )


def _migrate_record_table(connection: sqlite3.Connection, statements: list[str]) -> None:
    info = connection.execute("PRAGMA table_info(accident_records)").fetchall()
    if not info:
        return
    columns = {row[1] for row in info}
    article_id_not_null = any(row[1] == "article_id" and row[3] for row in info)
    required_new = {"record_key", "occurrence_year", "headline", "source_url_key"}
    if required_new.issubset(columns) and not article_id_not_null:
        return

    old_rows = connection.execute(
        """SELECT r.*, a.headline AS article_headline, a.published_date AS article_published_date,
                  a.language AS article_language, a.source_name AS article_source_name,
                  a.source_url AS article_source_url
           FROM accident_records r LEFT JOIN news_articles a USING (article_id)"""
    ).fetchall()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE accident_records")
        connection.execute(_create_record_table_sql(statements))
        for row in old_rows:
            old = dict(row)
            accident_date = old.get("accident_date")
            district = old.get("district")
            occurrence_year = _derive_year(accident_date)
            if district and district.casefold() == "dehradun" and occurrence_year:
                scope_status, scope_reason = "in_scope", "Incident date and Dehradun district are known; year is derived from the incident date."
            elif district and district.casefold() != "dehradun":
                scope_status, scope_reason = "out_of_scope", f"District is {district}; target district is Dehradun."
            else:
                scope_status, scope_reason = "unknown", "Incident date or district could not be established from the article."
            source_url = old.get("article_source_url")
            values = {
                "record_key": f"legacy-record-{old['accident_id']}",
                "article_id": old.get("article_id"),
                "headline": old.get("article_headline") or old.get("headline") or "",
                "published_date": old.get("article_published_date") or old.get("publication_date"),
                "language": old.get("article_language"),
                "source_name": old.get("article_source_name") or old.get("source"),
                "source_url": source_url or old.get("article_url"),
                "source_url_key": _normalized_url(source_url) if source_url else None,
                "accident_date": accident_date,
                "occurrence_year": occurrence_year,
                "accident_time": old.get("accident_time"),
                "state": old.get("state") or ("Uttarakhand" if district and district.casefold() == "dehradun" else None),
                "district": district,
                "area": old.get("area"),
                "accident_type": old.get("accident_type"),
                "cause": old.get("cause"),
                "cause_status": old.get("cause_status"),
                "vehicles_involved": old.get("vehicles_involved"),
                "deaths": old.get("deaths"),
                "injuries": old.get("injuries", old.get("injured")),
                "weather": old.get("weather"),
                "extraction_confidence": old.get("extraction_confidence"),
                "review_status": old.get("review_status") or "pending",
                "accident_date_status": old.get("accident_date_status"),
                "scope_status": scope_status,
                "scope_reason": scope_reason,
                "possible_duplicate": old.get("possible_duplicate") or 0,
                "extracted_fields_json": old.get("extracted_fields_json") or "{}",
                "field_sources_json": old.get("field_sources_json") or "{}",
                "evidence_json": old.get("evidence_json") or "{}",
                "conflict_notes_json": old.get("conflict_notes_json") or "[]",
                "created_at": old.get("created_at") or _now(),
                "updated_at": old.get("updated_at") or _now(),
                "accident_id": old["accident_id"],
            }
            fields = tuple(values)
            connection.execute(
                f"INSERT INTO accident_records ({', '.join(fields)}) "
                f"VALUES ({', '.join('?' for _ in fields)})",
                tuple(values[field] for field in fields),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def initialize_database(path: str | Path | None = None) -> Path:
    db_file = database_path(path)
    statements = _schema_statements()
    with closing(connect(db_file)) as connection:
        for statement in statements:
            folded = statement.casefold()
            if folded.startswith("create index"):
                continue
            connection.execute(statement)
        _migrate_article_table(connection, statements)
        _migrate_record_table(connection, statements)
        _migrate_evidence_column(connection)
        _migrate_state_column(connection)
        for statement in statements:
            if statement.casefold().startswith("create index"):
                connection.execute(statement)
    return db_file


def insert_article(article: dict[str, Any], path: str | Path | None = None) -> int:
    text_hash = _normalized_text_hash(article["article_text"])
    source_url = article.get("source_url") or None
    url_key = _normalized_url(source_url) if source_url else None
    with closing(connect(path)) as connection, connection:
        duplicate = connection.execute(
            """SELECT article_id FROM news_articles
               WHERE source_url_key = ? OR article_text_hash = ? LIMIT 1""",
            (url_key, text_hash),
        ).fetchone()
        if duplicate:
            raise DuplicateArticleError(int(duplicate["article_id"]))
        cursor = connection.execute(
            """INSERT INTO news_articles
               (headline, article_text, source_name, source_url, source_url_key,
                article_text_hash, published_date, language, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article.get("headline") or None, article["article_text"], article.get("source_name") or None,
                source_url, url_key, text_hash, article.get("published_date") or None,
                article.get("language") or None, _now(),
            ),
        )
        return int(cursor.lastrowid)


def get_article(article_id: int, path: str | Path | None = None) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM news_articles WHERE article_id = ?", (article_id,)
        ).fetchone()
    return dict(row) if row else None


def list_articles(path: str | Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """SELECT article_id, headline, source_name, source_url, published_date,
                      language, created_at
               FROM news_articles ORDER BY published_date DESC, article_id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def find_screenshot_by_hash(image_sha256: str, path: str | Path | None = None) -> str | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT screenshot_article_id FROM screenshot_pages WHERE image_sha256 = ?",
            (image_sha256,),
        ).fetchone()
    return str(row["screenshot_article_id"]) if row else None


def insert_screenshot_draft(
    draft: dict[str, Any], pages: list[dict[str, Any]], path: str | Path | None = None
) -> str:
    screenshot_article_id = str(draft["screenshot_article_id"])
    with closing(connect(path)) as connection, connection:
        for page in pages:
            existing = connection.execute(
                "SELECT screenshot_article_id FROM screenshot_pages WHERE image_sha256 = ?",
                (page["image_sha256"],),
            ).fetchone()
            if existing:
                raise DuplicateScreenshotError(str(existing["screenshot_article_id"]))
        connection.execute(
            """INSERT INTO screenshot_articles
               (screenshot_article_id, headline, source_name, source_url, published_date,
                language, raw_ocr_text, reviewed_article_text, status, extraction_method,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (
                screenshot_article_id, draft.get("headline") or None,
                draft.get("source_name") or None, draft.get("source_url") or None,
                draft.get("published_date") or None, draft.get("language") or None,
                draft.get("raw_ocr_text") or "", draft.get("reviewed_article_text") or "",
                draft.get("extraction_method") or "tesseract-eng+hin",
                draft["created_at"], draft["updated_at"],
            ),
        )
        for page in pages:
            connection.execute(
                """INSERT INTO screenshot_pages
                   (screenshot_article_id, page_order, original_filename, image_path,
                    image_sha256, raw_ocr_text, ocr_confidence, ocr_error, readability_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    screenshot_article_id, page["page_order"], page["original_filename"],
                    page["image_path"], page["image_sha256"], page.get("raw_ocr_text") or "",
                    page.get("ocr_confidence"), page.get("ocr_error"),
                    page["readability_status"], page["created_at"],
                ),
            )
    return screenshot_article_id


def get_screenshot_draft(
    screenshot_article_id: str, path: str | Path | None = None
) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM screenshot_articles WHERE screenshot_article_id = ?",
            (screenshot_article_id,),
        ).fetchone()
        if row is None:
            return None
        draft = dict(row)
        draft["pages"] = [dict(page) for page in connection.execute(
            """SELECT * FROM screenshot_pages WHERE screenshot_article_id = ?
               ORDER BY page_order, screenshot_page_id""",
            (screenshot_article_id,),
        ).fetchall()]
        draft["records"] = [dict(record) for record in connection.execute(
            """SELECT * FROM screenshot_draft_records WHERE screenshot_article_id = ?
               ORDER BY record_number, screenshot_record_id""",
            (screenshot_article_id,),
        ).fetchall()]
    return draft


def list_screenshot_drafts(path: str | Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """SELECT a.*, COUNT(DISTINCT p.screenshot_page_id) AS page_count,
                      COUNT(DISTINCT r.screenshot_record_id) AS record_count
               FROM screenshot_articles a
               LEFT JOIN screenshot_pages p USING (screenshot_article_id)
               LEFT JOIN screenshot_draft_records r USING (screenshot_article_id)
               GROUP BY a.screenshot_article_id
               ORDER BY a.updated_at DESC, a.screenshot_article_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def update_screenshot_draft(
    screenshot_article_id: str,
    updates: dict[str, Any],
    path: str | Path | None = None,
) -> None:
    allowed = {"headline", "source_name", "source_url", "published_date", "language", "reviewed_article_text"}
    values = {key: (updates.get(key) or None) if key != "reviewed_article_text" else (updates.get(key) or "")
              for key in allowed if key in updates}
    if not values:
        return
    assignments = ", ".join(f"{field} = ?" for field in values)
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            f"UPDATE screenshot_articles SET {assignments}, updated_at = ? WHERE screenshot_article_id = ?",
            tuple(values.values()) + (_now(), screenshot_article_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")


def insert_screenshot_draft_records(
    screenshot_article_id: str,
    records: list[dict[str, Any]],
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    with closing(connect(path)) as connection, connection:
        existing = connection.execute(
            "SELECT COUNT(*) FROM screenshot_draft_records WHERE screenshot_article_id = ?",
            (screenshot_article_id,),
        ).fetchone()[0]
        if existing:
            return [dict(row) for row in connection.execute(
                """SELECT * FROM screenshot_draft_records WHERE screenshot_article_id = ?
                   ORDER BY record_number, screenshot_record_id""",
                (screenshot_article_id,),
            ).fetchall()]
        for number, record in enumerate(records, start=1):
            now = _now()
            connection.execute(
                """INSERT INTO screenshot_draft_records
                   (screenshot_article_id, record_number, record_json, evidence_json,
                    conflict_notes_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    screenshot_article_id, number,
                    json.dumps(record.get("record") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.get("evidence") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.get("conflicts") or [], ensure_ascii=False), now, now,
                ),
            )
        return [dict(row) for row in connection.execute(
            """SELECT * FROM screenshot_draft_records WHERE screenshot_article_id = ?
               ORDER BY record_number, screenshot_record_id""",
            (screenshot_article_id,),
        ).fetchall()]


def get_screenshot_draft_record(
    screenshot_record_id: int, path: str | Path | None = None
) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM screenshot_draft_records WHERE screenshot_record_id = ?",
            (screenshot_record_id,),
        ).fetchone()
    return dict(row) if row else None


def update_screenshot_draft_record(
    screenshot_record_id: int,
    *,
    record_json: str,
    status: str | None = None,
    central_accident_id: int | None = None,
    path: str | Path | None = None,
) -> None:
    if status is not None and status not in {"draft", "approved", "rejected"}:
        raise ValueError("Screenshot record status must be draft, approved, or rejected.")
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """UPDATE screenshot_draft_records SET record_json = ?,
               status = COALESCE(?, status), central_accident_id = COALESCE(?, central_accident_id),
               updated_at = ? WHERE screenshot_record_id = ?""",
            (record_json, status, central_accident_id, _now(), screenshot_record_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Screenshot record {screenshot_record_id} was not found.")


def set_screenshot_article_central(
    screenshot_article_id: str,
    central_article_id: int,
    *,
    status: str = "draft",
    path: str | Path | None = None,
) -> None:
    if status not in {"draft", "approved", "rejected"}:
        raise ValueError("Screenshot article status must be draft, approved, or rejected.")
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """UPDATE screenshot_articles SET central_article_id = ?, status = ?, updated_at = ?
               WHERE screenshot_article_id = ?""",
            (central_article_id, status, _now(), screenshot_article_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")


def set_screenshot_article_status(
    screenshot_article_id: str, status: str, path: str | Path | None = None
) -> None:
    if status not in {"draft", "approved", "rejected"}:
        raise ValueError("Screenshot article status must be draft, approved, or rejected.")
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE screenshot_articles SET status = ?, updated_at = ? WHERE screenshot_article_id = ?",
            (status, _now(), screenshot_article_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Screenshot article {screenshot_article_id} was not found.")


def get_accident_record_by_key(
    record_key: str, path: str | Path | None = None
) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT accident_id FROM accident_records WHERE record_key = ?", (record_key,)
        ).fetchone()
    return get_accident_record(int(row["accident_id"]), path) if row else None


def _article_metadata(connection: sqlite3.Connection, article_id: int | None) -> dict[str, Any]:
    if article_id is None:
        return {}
    row = connection.execute(
        "SELECT headline, published_date, language, source_name, source_url FROM news_articles WHERE article_id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Article {article_id} was not found.")
    return dict(row)


def insert_accident_records(
    records: list[dict[str, Any]], path: str | Path | None = None
) -> dict[str, Any]:
    inserted_ids: list[int] = []
    skipped = 0
    affected_years: set[int] = set()
    columns = ("record_key", "article_id") + RECORD_FIELDS + ("created_at", "updated_at")
    with closing(connect(path)) as connection, connection:
        for input_record in records:
            record = dict(input_record)
            article_id = int(record["article_id"]) if record.get("article_id") is not None else None
            metadata = _article_metadata(connection, article_id)
            for field, value in metadata.items():
                if record.get(field) in (None, ""):
                    record[field] = value
            record["source_url_key"] = (
                _normalized_url(record["source_url"]) if record.get("source_url") else None
            )
            record["occurrence_year"] = _derive_year(record.get("accident_date"))
            record_key = _record_key(record, article_id)
            if connection.execute(
                "SELECT 1 FROM accident_records WHERE record_key = ?", (record_key,)
            ).fetchone():
                skipped += 1
                continue
            values = {field: record.get(field) for field in RECORD_FIELDS}
            values["headline"] = values["headline"] or ""
            values["review_status"] = values["review_status"] or "pending"
            values["scope_status"] = values["scope_status"] or "unknown"
            values["scope_reason"] = values["scope_reason"] or ""
            values["possible_duplicate"] = int(values["possible_duplicate"] or 0)
            values["extracted_fields_json"] = values["extracted_fields_json"] or "{}"
            values["field_sources_json"] = values["field_sources_json"] or "{}"
            values["evidence_json"] = values["evidence_json"] or "{}"
            values["conflict_notes_json"] = values["conflict_notes_json"] or "[]"
            now = _now()
            cursor = connection.execute(
                f"INSERT INTO accident_records ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                (record_key, article_id) + tuple(values[field] for field in RECORD_FIELDS) + (now, now),
            )
            accident_id = int(cursor.lastrowid)
            inserted_ids.append(accident_id)
            if record.get("occurrence_year"):
                affected_years.add(int(record["occurrence_year"]))

            if record.get("accident_date") and record.get("area"):
                matches = connection.execute(
                    """SELECT accident_id, occurrence_year FROM accident_records
                       WHERE accident_id != ? AND accident_date = ?
                         AND lower(coalesce(area, '')) = lower(?)
                         AND lower(coalesce(district, '')) = lower(?)
                         AND source_url_key IS NOT ?""",
                    (
                        accident_id, record["accident_date"], record["area"],
                        record.get("district") or "", record.get("source_url_key"),
                    ),
                ).fetchall()
                if matches:
                    connection.execute(
                        "UPDATE accident_records SET possible_duplicate = 1, "
                        "review_status = CASE WHEN review_status = 'verified' "
                        "THEN review_status ELSE 'needs_review' END, updated_at = ? "
                        "WHERE accident_id = ?",
                        (now, accident_id),
                    )
                    for matched in matches:
                        connection.execute(
                            "UPDATE accident_records SET possible_duplicate = 1, "
                            "review_status = CASE WHEN review_status = 'verified' "
                            "THEN review_status ELSE 'needs_review' END, updated_at = ? "
                            "WHERE accident_id = ?",
                            (now, matched["accident_id"]),
                        )
                        if matched["occurrence_year"]:
                            affected_years.add(int(matched["occurrence_year"]))
        return {"inserted_ids": inserted_ids, "skipped": skipped, "years": affected_years}


def insert_accident_record(
    article_id: int | None, record: dict[str, Any], path: str | Path | None = None
) -> int:
    result = insert_accident_records([{"article_id": article_id, **record}], path)
    if not result["inserted_ids"]:
        raise DuplicateRecordError("An equivalent accident record already exists.")
    return result["inserted_ids"][0]


def get_accident_record(
    accident_id: int, path: str | Path | None = None
) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            """SELECT r.*, COALESCE(NULLIF(r.headline, ''), a.headline, '') AS headline,
                      COALESCE(r.published_date, a.published_date) AS published_date,
                      COALESCE(r.language, a.language) AS language,
                      COALESCE(r.source_name, a.source_name) AS source_name,
                      COALESCE(r.source_url, a.source_url) AS source_url,
                      a.article_text
               FROM accident_records r LEFT JOIN news_articles a USING (article_id)
               WHERE r.accident_id = ?""",
            (accident_id,),
        ).fetchone()
    return dict(row) if row else None


def list_accident_records(
    article_id: int | None = None,
    *,
    verified_only: bool = False,
    year: int | None = None,
    year_basis: str = "accident",
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    query = """SELECT r.*, COALESCE(NULLIF(r.headline, ''), a.headline, '') AS headline,
                      COALESCE(r.published_date, a.published_date) AS published_date,
                      COALESCE(r.language, a.language) AS language,
                      COALESCE(r.source_name, a.source_name) AS source_name,
                      COALESCE(r.source_url, a.source_url) AS source_url,
                      a.article_text
               FROM accident_records r LEFT JOIN news_articles a USING (article_id)"""
    clauses: list[str] = []
    params: list[Any] = []
    if article_id is not None:
        clauses.append("r.article_id = ?")
        params.append(article_id)
    if verified_only:
        clauses.append("r.review_status = 'verified'")
    if year is not None:
        if year_basis == "accident":
            clauses.append("r.occurrence_year = ?")
        elif year_basis == "publication":
            clauses.append("substr(COALESCE(r.published_date, a.published_date), 1, 4) = ?")
            params.append(f"{year:04d}")
        else:
            raise ValueError("year_basis must be 'accident' or 'publication'.")
        if year_basis == "accident":
            params.append(year)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.occurrence_year, r.accident_date, r.accident_id"
    with closing(connect(path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_record_years(
    *, year_basis: str = "accident", path: str | Path | None = None
) -> list[int]:
    if year_basis == "accident":
        query = "SELECT DISTINCT occurrence_year AS year FROM accident_records WHERE occurrence_year IS NOT NULL ORDER BY year"
    elif year_basis == "publication":
        query = """SELECT DISTINCT substr(COALESCE(r.published_date, a.published_date), 1, 4) AS year
                   FROM accident_records r LEFT JOIN news_articles a USING (article_id)
                   WHERE COALESCE(r.published_date, a.published_date) IS NOT NULL ORDER BY year"""
    else:
        raise ValueError("year_basis must be 'accident' or 'publication'.")
    with closing(connect(path)) as connection:
        rows = connection.execute(query).fetchall()
    result = []
    for row in rows:
        try:
            result.append(int(row["year"]))
        except (TypeError, ValueError):
            continue
    return result


def update_accident_record(
    accident_id: int,
    updates: dict[str, Any],
    *,
    field_sources_json: str | None = None,
    path: str | Path | None = None,
) -> tuple[int | None, int | None]:
    permitted = {key: value for key, value in updates.items() if key in EDITABLE_FIELDS}
    if not permitted:
        raise ValueError("No editable accident fields were supplied.")
    if field_sources_json is not None:
        permitted["field_sources_json"] = field_sources_json
    permitted["updated_at"] = _now()
    assignments = ", ".join(f"{field} = ?" for field in permitted)
    with closing(connect(path)) as connection, connection:
        old = connection.execute(
            "SELECT occurrence_year, accident_date FROM accident_records WHERE accident_id = ?",
            (accident_id,),
        ).fetchone()
        if old is None:
            raise ValueError(f"Accident record {accident_id} was not found.")
        if "accident_date" in permitted:
            permitted["occurrence_year"] = _derive_year(permitted["accident_date"])
        if "source_url" in permitted:
            permitted["source_url_key"] = (
                _normalized_url(permitted["source_url"]) if permitted["source_url"] else None
            )
        assignments = ", ".join(f"{field} = ?" for field in permitted)
        cursor = connection.execute(
            f"UPDATE accident_records SET {assignments} WHERE accident_id = ?",
            tuple(permitted.values()) + (accident_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Accident record {accident_id} was not found.")
        new = connection.execute(
            "SELECT occurrence_year FROM accident_records WHERE accident_id = ?", (accident_id,)
        ).fetchone()
    return old["occurrence_year"], new["occurrence_year"]


def delete_accident_record(accident_id: int, path: str | Path | None = None) -> int | None:
    with closing(connect(path)) as connection, connection:
        row = connection.execute(
            "SELECT occurrence_year FROM accident_records WHERE accident_id = ?", (accident_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Accident record {accident_id} was not found.")
        connection.execute("DELETE FROM accident_records WHERE accident_id = ?", (accident_id,))
    return row["occurrence_year"]
