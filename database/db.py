"""SQLite persistence for original articles and reviewed accident records."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "accident_news.db"
ARTICLE_FIELDS = (
    "headline", "article_text", "source_name", "source_url", "published_date", "language"
)
RECORD_FIELDS = (
    "accident_date", "accident_time", "district", "area", "accident_type", "cause",
    "cause_status", "vehicles_involved", "deaths", "injuries", "weather",
    "extraction_confidence", "review_status", "accident_date_status", "scope_status",
    "scope_reason", "possible_duplicate", "extracted_fields_json", "field_sources_json",
    "conflict_notes_json",
)
EDITABLE_FIELDS = frozenset({
    "accident_date", "accident_time", "district", "area", "accident_type", "cause",
    "cause_status", "vehicles_involved", "deaths", "injuries", "weather", "review_status",
    "accident_date_status", "scope_status", "scope_reason",
})


class DuplicateArticleError(ValueError):
    """Raised when either the source URL or normalized text already exists."""

    def __init__(self, existing_article_id: int):
        self.existing_article_id = existing_article_id
        super().__init__(f"This article duplicates article #{existing_article_id}.")


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


def initialize_database(path: str | Path | None = None) -> Path:
    db_file = database_path(path)
    schema_path = Path(__file__).with_name("schema.sql")
    with closing(connect(db_file)) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
    return db_file


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


def insert_article(article: dict[str, Any], path: str | Path | None = None) -> int:
    text_hash = _normalized_text_hash(article["article_text"])
    url_key = _normalized_url(article["source_url"])
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
                article["headline"], article["article_text"], article["source_name"],
                article["source_url"], url_key, text_hash, article["published_date"],
                article["language"], _now(),
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


def insert_accident_record(
    article_id: int, record: dict[str, Any], path: str | Path | None = None
) -> int:
    values = {field: record.get(field) for field in RECORD_FIELDS}
    values["created_at"] = values["updated_at"] = _now()
    columns = ("article_id",) + RECORD_FIELDS + ("created_at", "updated_at")
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            f"INSERT INTO accident_records ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            (article_id,) + tuple(values[field] for field in RECORD_FIELDS) + (
                values["created_at"], values["updated_at"]
            ),
        )
        accident_id = int(cursor.lastrowid)
        # A date + locality match across different articles is a review hint,
        # never an automatic merge or deletion.
        if record.get("accident_date") and record.get("area"):
            matches = connection.execute(
                """SELECT accident_id FROM accident_records
                   WHERE article_id != ? AND accident_date = ?
                     AND lower(coalesce(area, '')) = lower(?)
                     AND coalesce(district, '') = coalesce(?, '')""",
                (article_id, record["accident_date"], record["area"], record.get("district")),
            ).fetchall()
            if matches:
                ids = [int(row["accident_id"]) for row in matches]
                connection.executemany(
                    "UPDATE accident_records SET possible_duplicate = 1, "
                    "review_status = CASE WHEN review_status = 'verified' "
                    "THEN review_status ELSE 'needs_review' END, updated_at = ? "
                    "WHERE accident_id = ?",
                    ((_now(), record_id) for record_id in ids + [accident_id]),
                )
        return accident_id


def get_accident_record(
    accident_id: int, path: str | Path | None = None
) -> dict[str, Any] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            """SELECT r.*, a.headline, a.article_text, a.source_name, a.source_url,
                      a.published_date, a.language
               FROM accident_records r JOIN news_articles a USING (article_id)
               WHERE r.accident_id = ?""",
            (accident_id,),
        ).fetchone()
    return dict(row) if row else None


def list_accident_records(
    article_id: int | None = None,
    *,
    verified_only: bool = False,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    query = """SELECT r.*, a.headline, a.source_name, a.source_url,
                      a.published_date, a.language
               FROM accident_records r JOIN news_articles a USING (article_id)"""
    clauses: list[str] = []
    params: list[Any] = []
    if article_id is not None:
        clauses.append("r.article_id = ?")
        params.append(article_id)
    if verified_only:
        clauses.append("r.review_status = 'verified'")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.accident_date, r.accident_id"
    with closing(connect(path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def update_accident_record(
    accident_id: int,
    updates: dict[str, Any],
    *,
    field_sources_json: str,
    path: str | Path | None = None,
) -> None:
    permitted = {key: value for key, value in updates.items() if key in EDITABLE_FIELDS}
    if not permitted:
        raise ValueError("No editable accident fields were supplied.")
    permitted["field_sources_json"] = field_sources_json
    permitted["updated_at"] = _now()
    assignments = ", ".join(f"{field} = ?" for field in permitted)
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            f"UPDATE accident_records SET {assignments} WHERE accident_id = ?",
            tuple(permitted.values()) + (accident_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Accident record {accident_id} was not found.")
