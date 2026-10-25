from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

from .preprocessing import STRUCTURED_FIELDS


RAW_FIELDS = (
    "source", "feed_url", "language", "headline", "article_url", "resolved_url",
    "publication_date", "rss_text", "article_text", "extraction_status",
    "extraction_error", "discovery_status", "discovery_reason", "review_flag", "collected_at",
)


def write_csv(path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, str]]) -> None:
    pd.DataFrame(list(rows), columns=fields).to_csv(path, index=False, encoding="utf-8-sig")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            article_url TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            feed_url TEXT NOT NULL,
            language TEXT NOT NULL,
            headline TEXT NOT NULL,
            publication_date TEXT NOT NULL,
            resolved_url TEXT NOT NULL,
            rss_text TEXT NOT NULL,
            article_text TEXT NOT NULL,
            extraction_status TEXT NOT NULL,
            extraction_error TEXT NOT NULL,
            discovery_status TEXT NOT NULL DEFAULT 'legacy',
            discovery_reason TEXT NOT NULL DEFAULT '',
            review_flag TEXT NOT NULL,
            collected_at TEXT NOT NULL
        )
        """
    )
    article_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(articles)")
    }
    if "discovery_status" not in article_columns:
        connection.execute(
            "ALTER TABLE articles ADD COLUMN discovery_status TEXT NOT NULL DEFAULT 'legacy'"
        )
    if "discovery_reason" not in article_columns:
        connection.execute(
            "ALTER TABLE articles ADD COLUMN discovery_reason TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS accidents (
            accident_id TEXT PRIMARY KEY,
            headline TEXT NOT NULL,
            article_url TEXT NOT NULL REFERENCES articles(article_url),
            publication_date TEXT NOT NULL,
            accident_date TEXT NOT NULL,
            accident_date_status TEXT NOT NULL,
            area TEXT NOT NULL,
            area_status TEXT NOT NULL,
            cause TEXT NOT NULL,
            cause_status TEXT NOT NULL,
            injured TEXT NOT NULL,
            injured_status TEXT NOT NULL,
            deaths TEXT NOT NULL,
            deaths_status TEXT NOT NULL,
            source TEXT NOT NULL,
            extraction_status TEXT NOT NULL,
            review_flag TEXT NOT NULL
        )
        """
    )


def _migrate_legacy(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "accidents" not in tables:
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accidents)")}
    if "article_text" not in columns:
        return

    old_rows = connection.execute("SELECT * FROM accidents").fetchall()
    old_names = [row[1] for row in connection.execute("PRAGMA table_info(accidents)")]
    legacy = [dict(zip(old_names, row)) for row in old_rows]
    connection.execute("DROP TABLE accidents")
    _create_schema(connection)
    for row in legacy:
        url = row["article_url"]
        connection.execute(
            """
            INSERT OR IGNORE INTO articles
            (article_url, source, feed_url, language, headline, publication_date,
             resolved_url, rss_text, article_text, extraction_status, extraction_error,
             discovery_status, discovery_reason, review_flag, collected_at)
            VALUES (?, ?, '', '', ?, ?, '', ?, '', 'not_attempted', '', 'legacy', '',
                    'true', '')
            """,
            (url, row["source"], row["headline"], row["publication_date"], row["article_text"]),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO accidents
            (accident_id, headline, article_url, publication_date, accident_date,
             accident_date_status, area, area_status, cause, cause_status, injured,
             injured_status, deaths, deaths_status, source, extraction_status, review_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_attempted', 'true')
            """,
            tuple(row[field] for field in (
                "accident_id", "headline", "article_url", "publication_date", "accident_date",
                "accident_date_status", "area", "area_status", "cause", "cause_status",
                "injured", "injured_status", "deaths", "deaths_status", "source",
            )),
        )


def store_sqlite(path: Path, articles: list[dict[str, str]], records: list[dict[str, str]]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            _migrate_legacy(connection)
            _create_schema(connection)
            connection.executemany(
                """
                INSERT INTO articles
                (source, feed_url, language, headline, article_url, resolved_url, publication_date,
                 rss_text, article_text, extraction_status, extraction_error,
                 discovery_status, discovery_reason, review_flag, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_url) DO UPDATE SET
                    source=excluded.source, feed_url=excluded.feed_url, language=excluded.language,
                    headline=excluded.headline, publication_date=excluded.publication_date,
                    resolved_url=excluded.resolved_url, rss_text=excluded.rss_text,
                    article_text=excluded.article_text, extraction_status=excluded.extraction_status,
                    extraction_error=excluded.extraction_error, review_flag=excluded.review_flag,
                    discovery_status=excluded.discovery_status,
                    discovery_reason=excluded.discovery_reason,
                    collected_at=excluded.collected_at
                """,
                (
                    (
                        article.get("source", ""), article.get("feed_url", ""),
                        article.get("language", ""), article.get("headline", ""),
                        article.get("article_url", ""), article.get("resolved_url", ""),
                        article.get("publication_date", ""), article.get("rss_text", ""),
                        article.get("article_text", ""), article.get("extraction_status", "not_attempted"),
                        article.get("extraction_error", ""),
                        article.get("discovery_status", "pending_filter"),
                        article.get("discovery_reason", ""), article.get("review_flag", "true"),
                        article.get("collected_at", ""),
                    )
                    for article in articles
                ),
            )
            connection.executemany(
                f"""
                INSERT INTO accidents ({", ".join(STRUCTURED_FIELDS)})
                VALUES ({", ".join("?" for _ in STRUCTURED_FIELDS)})
                ON CONFLICT(accident_id) DO UPDATE SET
                    {", ".join(f"{field}=excluded.{field}" for field in STRUCTURED_FIELDS if field != "accident_id")}
                """,
                (tuple(record[field] for field in STRUCTURED_FIELDS) for record in records),
            )
    finally:
        connection.close()
