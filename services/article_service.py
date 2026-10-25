"""Validation and operations for manually collected source articles."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlsplit
from typing import Any

from database import db


def validate_article(article: dict[str, Any]) -> dict[str, Any]:
    required = ("headline", "article_text", "source_name", "source_url", "published_date", "language")
    missing = [name for name in required if article.get(name) is None or article.get(name) == ""]
    if missing:
        raise ValueError("Complete all required fields: " + ", ".join(missing) + ".")
    cleaned = dict(article)
    if not isinstance(article["article_text"], str) or not article["article_text"].strip():
        raise ValueError("Full article text is required.")
    # Preserve the original body byte-for-character as received by Streamlit.
    cleaned["article_text"] = article["article_text"]
    for name in ("headline", "source_name", "source_url"):
        cleaned[name] = str(article[name]).strip()
        if not cleaned[name]:
            raise ValueError(f"{name.replace('_', ' ').capitalize()} is required.")
    parts = urlsplit(cleaned["source_url"])
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Original article URL must be a valid HTTP or HTTPS URL.")
    publication = article["published_date"]
    if isinstance(publication, (date, datetime)):
        cleaned["published_date"] = publication.date().isoformat() if isinstance(publication, datetime) else publication.isoformat()
    else:
        try:
            cleaned["published_date"] = date.fromisoformat(str(publication)).isoformat()
        except ValueError as error:
            raise ValueError("Publication date must use YYYY-MM-DD format.") from error
    if cleaned["language"] not in {"English", "Hindi"}:
        raise ValueError("Language must be English or Hindi.")
    return cleaned


def save_article(article: dict[str, Any], path=None) -> int:
    return db.insert_article(validate_article(article), path)


def get_article(article_id: int, path=None) -> dict[str, Any] | None:
    return db.get_article(article_id, path)


def list_articles(path=None) -> list[dict[str, Any]]:
    return db.list_articles(path)
