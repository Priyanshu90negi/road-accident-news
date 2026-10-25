from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup


USER_AGENT = "DehradunAccidentResearch/1.0 (research RSS reader)"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html.unescape(value), "html.parser").get_text(" ")).strip()


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_link(entry: ET.Element) -> str:
    for element in entry.iter():
        if element.tag.rsplit("}", 1)[-1] == "link":
            if element.attrib.get("href"):
                return element.attrib["href"].strip()
            if element.text and element.text.strip():
                return element.text.strip()
    return ""


def _entry_date(entry: ET.Element) -> str:
    for element in entry.iter():
        if element.tag.rsplit("}", 1)[-1] in {"pubDate", "published", "updated", "date"}:
            parsed = parse_datetime((element.text or "").strip())
            if parsed:
                return parsed.date().isoformat()
    return ""


def fetch_feed(source: dict[str, str], timeout: int = 20) -> list[dict[str, str]]:
    response = requests.get(
        source["url"],
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    articles = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for element in entry.iter():
            key = element.tag.rsplit("}", 1)[-1]
            if key in {"title", "description", "summary", "content", "encoded"}:
                value = "".join(element.itertext()).strip()
                if value:
                    fields.setdefault(key, value)
        headline = clean_text(fields.get("title", ""))
        article_url = _entry_link(entry)
        if not headline or not article_url:
            continue
        # Feeds vary: description may be a short teaser while content:encoded or
        # Atom content contains the full article. Keep the richest available
        # feed text instead of always preferring the first summary field.
        summary = max(
            (fields.get(key, "") for key in ("encoded", "content", "description", "summary")),
            key=lambda value: len(clean_text(value)),
            default="",
        )
        feed_body = max(
            (fields.get(key, "") for key in ("encoded", "content")),
            key=lambda value: len(clean_text(value)),
            default="",
        )
        articles.append(
            {
                "source": source["name"],
                "feed_url": source["url"],
                "language": source.get("language", ""),
                "headline": headline,
                "article_url": article_url,
                "publication_date": _entry_date(entry),
                "rss_text": clean_text(summary),
                # Preserve publisher-supplied body content as a fallback if
                # the linked page is unavailable or disallowed by robots.txt.
                "article_text": clean_text(feed_body),
                "resolved_url": "",
                "discovery_status": "pending_filter",
                "discovery_reason": "",
                "extraction_status": "pending_filter",
                "extraction_error": "",
                "review_flag": "true",
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
    return articles


def fetch_news_sitemap(source: dict[str, str], timeout: int = 20) -> list[dict[str, str]]:
    response = requests.get(
        source["url"],
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    articles = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "url":
            continue
        values = {
            element.tag.rsplit("}", 1)[-1]: (element.text or "").strip()
            for element in entry.iter()
            if element.text and element.text.strip()
        }
        headline = clean_text(values.get("title", ""))
        article_url = values.get("loc", "")
        publication_date = parse_datetime(values.get("publication_date", ""))
        if not headline or not article_url or not publication_date:
            continue
        articles.append(
            {
                "source": source["name"],
                "feed_url": source["url"],
                "language": source.get("language", ""),
                "headline": headline,
                "article_url": article_url,
                "publication_date": publication_date.date().isoformat(),
                "rss_text": "",
                "article_text": "",
                "resolved_url": "",
                "discovery_status": "pending_filter",
                "discovery_reason": "",
                "extraction_status": "pending_filter",
                "extraction_error": "",
                "review_flag": "true",
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
    return articles


def fetch_source(source: dict[str, str], timeout: int = 20) -> list[dict[str, str]]:
    source_type = source.get("type", "rss")
    if source_type == "rss":
        return fetch_feed(source, timeout)
    if source_type == "news_sitemap":
        return fetch_news_sitemap(source, timeout)
    raise ValueError(f"Unsupported source type {source_type!r} for {source['name']!r}")
