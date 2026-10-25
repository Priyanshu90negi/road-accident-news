"""Discover, extract, process, and store Dehradun accident news."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from dehradun_scraper.discovery import fetch_source
from dehradun_scraper.extraction import ArticleExtractor
from dehradun_scraper.preprocessing import (
    STRUCTURED_FIELDS,
    extract_accident_date,
    extract_area,
    extract_cause,
    extract_count,
    is_relevant,
    preprocess,
)
from dehradun_scraper.storage import RAW_FIELDS, store_sqlite, write_csv


def load_sources(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as source_file:
        sources = json.load(source_file)
    valid = isinstance(sources, list) and all(
        isinstance(source, dict)
        and isinstance(source.get("name"), str)
        and isinstance(source.get("url"), str)
        and source["url"].startswith(("https://", "http://"))
        for source in sources
    )
    if not valid:
        raise ValueError("Sources must be a JSON list of objects with name and HTTP(S) url fields.")
    return sources


def select_extraction_sample(
    articles: list[dict[str, str]], extract_limit: int
) -> list[dict[str, str]]:
    if extract_limit <= 0:
        return articles
    by_language: dict[str, list[dict[str, str]]] = {}
    for article in articles:
        by_language.setdefault(article.get("language", ""), []).append(article)
    selected = []
    while len(selected) < extract_limit and by_language:
        for language in list(by_language):
            selected.append(by_language[language].pop(0))
            if not by_language[language]:
                del by_language[language]
            if len(selected) == extract_limit:
                break
    return selected


def classify_articles(
    articles: list[dict[str, str]],
) -> list[dict[str, str]]:
    eligible = []
    seen_urls: set[str] = set()
    for article in articles:
        url = article["article_url"].split("#", 1)[0].rstrip("/").casefold()
        if not is_relevant(article):
            article["discovery_status"] = "filtered_irrelevant"
            article["discovery_reason"] = (
                "Feed headline/excerpt did not pass the 2025, Dehradun, and road-accident filters."
            )
            article["extraction_status"] = "not_applicable"
            article["extraction_error"] = ""
            article["review_flag"] = "false"
        elif url in seen_urls:
            article["discovery_status"] = "duplicate"
            article["discovery_reason"] = "Article URL already appeared in the eligible feed results."
            article["extraction_status"] = "not_applicable"
            article["extraction_error"] = ""
            article["review_flag"] = "false"
        else:
            seen_urls.add(url)
            article["discovery_status"] = "eligible"
            article["discovery_reason"] = ""
            article["extraction_status"] = "awaiting_extraction"
            article["extraction_error"] = ""
            article["review_flag"] = "true"
            eligible.append(article)
    return eligible


def mark_unselected_articles(
    eligible: list[dict[str, str]], selected: list[dict[str, str]]
) -> None:
    selected_urls = {article["article_url"] for article in selected}
    for article in eligible:
        if article["article_url"] in selected_urls:
            continue
        article["extraction_status"] = "not_attempted_limit"
        article["extraction_error"] = "Not attempted: extraction limit reached."
        article["review_flag"] = "true"


def collect(sources: list[dict[str, str]], output_dir: Path, extract_limit: int) -> int:
    articles: list[dict[str, str]] = []
    failures = 0
    for source in sources:
        try:
            articles.extend(fetch_source(source))
        except (requests.RequestException, ET.ParseError, OSError, ValueError) as error:
            failures += 1
            print(f"RSS source failed ({source['name']}): {error}", file=sys.stderr)

    eligible = classify_articles(articles)

    selected = select_extraction_sample(eligible, extract_limit)
    extractor = ArticleExtractor()
    for article in selected:
        result = extractor.extract(article["article_url"])
        # Keep whichever permitted source supplied more text. Some publishers
        # include the full story in RSS even when their article page is blocked.
        if len(result.text) >= len(article.get("article_text", "")):
            article["article_text"] = result.text
        article["resolved_url"] = result.resolved_url
        article["extraction_status"] = result.status
        article["extraction_error"] = result.error
        article["review_flag"] = "false" if result.status == "extracted" else "true"
        if result.error:
            print(
                f"Article extraction {result.status} ({article['article_url']}): {result.error}",
                file=sys.stderr,
            )
    mark_unselected_articles(eligible, selected)

    records = preprocess(articles)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "accident_news_raw.csv", RAW_FIELDS, articles)
    write_csv(
        output_dir / "article_extraction_review.csv",
        RAW_FIELDS,
        (
            article
            for article in articles
            if article["discovery_status"] == "eligible" and article["review_flag"] == "true"
        ),
    )
    write_csv(output_dir / "dehradun_accidents_2025.csv", STRUCTURED_FIELDS, records)
    store_sqlite(output_dir / "dehradun_accidents.sqlite3", articles, records)

    counts: dict[str, int] = {}
    for article in selected:
        status = article["extraction_status"]
        counts[status] = counts.get(status, 0) + 1
    status_summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(
        f"Collected {len(articles)} RSS entries; {len(eligible)} passed initial filters; "
        f"attempted {len(selected)} extractions ({status_summary or 'none'}); "
        f"saved {len(records)} structured records."
    )
    if failures == len(sources) and sources:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument(
        "--extract-limit",
        type=int,
        default=20,
        help="Maximum relevant article pages to fetch (default: 20; use 0 for no limit).",
    )
    args = parser.parse_args()
    try:
        sources = load_sources(args.sources)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Cannot load RSS sources: {error}", file=sys.stderr)
        return 2
    return collect(sources, args.out_dir, args.extract_limit)


if __name__ == "__main__":
    raise SystemExit(main())
