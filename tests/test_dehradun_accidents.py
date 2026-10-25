import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from dehradun_accidents import (
    classify_articles,
    mark_unselected_articles,
    preprocess,
    select_extraction_sample,
)
from dehradun_scraper.discovery import fetch_news_sitemap
from dehradun_scraper.extraction import ArticleExtractor
from dehradun_scraper.storage import store_sqlite


class AccidentPipelineTests(unittest.TestCase):
    def test_filters_wrong_year_and_irrelevant_items(self):
        items = [
            self.article("Dehradun road accident", "2024-12-31", "A road accident in Dehradun."),
            self.article("Dehradun weather", "2025-01-02", "Rainfall expected in Dehradun."),
            self.article("Delhi road accident", "2025-01-02", "A road accident in Delhi."),
        ]
        self.assertEqual(preprocess(items), [])

    def test_extraction_sample_balances_languages(self):
        articles = [
            {"language": "en", "article_url": f"https://example.com/en/{index}"}
            for index in range(4)
        ] + [
            {"language": "hi", "article_url": f"https://example.com/hi/{index}"}
            for index in range(4)
        ]
        sample = select_extraction_sample(articles, 4)
        self.assertEqual([article["language"] for article in sample], ["en", "hi", "en", "hi"])

    def test_classifies_unattempted_rows_without_claiming_a_block(self):
        eligible = self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")
        irrelevant = self.article("Dehradun weather", "2025-03-03", "Rain expected in Dehradun.")
        irrelevant["article_url"] = "https://example.com/weather"
        duplicate = dict(eligible)
        found = classify_articles([eligible, irrelevant, duplicate])
        self.assertEqual(found, [eligible])
        self.assertEqual(eligible["extraction_status"], "awaiting_extraction")
        self.assertEqual(irrelevant["extraction_status"], "not_applicable")
        self.assertEqual(irrelevant["discovery_status"], "filtered_irrelevant")
        self.assertEqual(duplicate["extraction_status"], "not_applicable")
        self.assertEqual(duplicate["discovery_status"], "duplicate")

    def test_capped_articles_get_explicit_not_attempted_status(self):
        first = self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")
        second = self.article("Dehradun crash", "2025-03-04", "Crash reported in Dehradun.")
        second["article_url"] = "https://example.com/second-story"
        eligible = classify_articles([first, second])
        first["extraction_status"] = "blocked_by_robots"
        mark_unselected_articles(eligible, [first])
        self.assertEqual(first["extraction_status"], "blocked_by_robots")
        self.assertEqual(second["extraction_status"], "not_attempted_limit")
        self.assertIn("limit", second["extraction_error"])

    def test_extracts_facts_and_marks_cause_as_suspected(self):
        item = self.article(
            "Two injured in Dehradun crash",
            "2025-06-10",
            "On 9 June 2025, a speeding car crashed near Mussoorie. Two people were injured.",
        )
        record = preprocess([item])[0]
        self.assertEqual(record["accident_date"], "2025-06-09")
        self.assertEqual(record["area"], "Mussoorie")
        self.assertEqual(record["injured"], "2")
        self.assertEqual(record["deaths"], "unknown")
        self.assertEqual(record["cause"], "speeding")
        self.assertEqual(record["cause_status"], "suspected")
        self.assertTrue(record["article_url"].startswith("https://"))

    def test_missing_details_keep_record_flagged_for_review(self):
        article = self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")
        record = preprocess([article])[0]
        self.assertEqual(record["review_flag"], "true")
        self.assertEqual(record["extraction_status"], "not_attempted")

    def test_deduplicates_same_story_url_and_title(self):
        first = self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")
        duplicate = dict(first, source="Another source")
        self.assertEqual(len(preprocess([first, duplicate])), 1)

    def test_extracts_english_and_hindi_dates_and_counts(self):
        english = self.article(
            "Dehradun crash",
            "2025-06-10",
            "The crash happened June 9, 2025, and two people were injured.",
        )
        hindi = self.article(
            "देहरादून सड़क हादसा",
            "2025-06-10",
            "9 जून 2025 को हादसे में दो लोग घायल हुए।",
        )
        self.assertEqual(preprocess([english])[0]["accident_date"], "2025-06-09")
        hindi_record = preprocess([hindi])[0]
        self.assertEqual(hindi_record["accident_date"], "2025-06-09")
        self.assertEqual(hindi_record["injured"], "2")

    def test_sqlite_stores_raw_text_separately_and_upserts(self):
        article = self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")
        article.update(article_text="Full text", extraction_status="extracted", review_flag="false")
        record = preprocess([article])[0]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "accidents.sqlite3"
            store_sqlite(database, [article], [record])
            store_sqlite(database, [article], [record])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM accidents").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT article_text FROM articles").fetchone()[0], "Full text")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(accidents)")}
                self.assertNotIn("article_text", columns)

    def test_sqlite_migrates_existing_accidents_table(self):
        record = preprocess(
            [self.article("Dehradun road accident", "2025-03-03", "Accident reported in Dehradun.")]
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    CREATE TABLE accidents (
                        accident_id TEXT PRIMARY KEY, headline TEXT, article_url TEXT,
                        publication_date TEXT, accident_date TEXT, accident_date_status TEXT,
                        area TEXT, area_status TEXT, cause TEXT, cause_status TEXT,
                        injured TEXT, injured_status TEXT, deaths TEXT, deaths_status TEXT,
                        article_text TEXT, source TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO accidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(
                        record[field]
                        for field in (
                            "accident_id", "headline", "article_url", "publication_date",
                            "accident_date", "accident_date_status", "area", "area_status",
                            "cause", "cause_status", "injured", "injured_status", "deaths",
                            "deaths_status",
                        )
                    )
                    + ("legacy RSS excerpt", record["source"]),
                )
                connection.commit()
            store_sqlite(database, [], [])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM accidents").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT rss_text FROM articles").fetchone()[0],
                    "legacy RSS excerpt",
                )

    def test_extractor_respects_robots_before_fetching_article(self):
        extractor = ArticleExtractor(minimum_interval=0)
        robots = Mock(status_code=200, ok=True, text="User-agent: *\nDisallow: /story")
        with patch.object(extractor, "_request", return_value=robots) as request:
            result = extractor.extract("https://example.com/story")
        self.assertEqual(result.status, "blocked_by_robots")
        request.assert_called_once_with("https://example.com/robots.txt", allow_redirects=True)

    def test_extractor_logs_http_failure_as_a_status(self):
        extractor = ArticleExtractor(minimum_interval=0)
        robots = Mock(status_code=404, ok=False)
        article_response = requests.ConnectionError("connection refused")
        with patch.object(extractor, "_request", side_effect=[robots, article_response]):
            result = extractor.extract("https://example.com/story")
        self.assertEqual(result.status, "http_error")
        self.assertIn("connection refused", result.error)

    def test_news_sitemap_extracts_article_title_date_and_url(self):
        response = Mock(
            content=(
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                b'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
                b'<url><loc>https://example.com/story</loc><news:news>'
                b'<news:publication><news:name>Example</news:name>'
                b'<news:language>en</news:language></news:publication>'
                b'<news:publication_date>2025-03-03T12:00:00Z</news:publication_date>'
                b'<news:title>Dehradun road accident</news:title>'
                b'</news:news></url></urlset>'
            )
        )
        response.raise_for_status = Mock()
        with patch("dehradun_scraper.discovery.requests.get", return_value=response):
            rows = fetch_news_sitemap(
                {"name": "Example", "type": "news_sitemap", "url": "https://example.com/news.xml"}
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["publication_date"], "2025-03-03")
        self.assertEqual(rows[0]["article_url"], "https://example.com/story")

    @staticmethod
    def article(headline, publication_date, article_text):
        return {
            "source": "Test",
            "feed_url": "https://example.com/rss",
            "language": "en",
            "headline": headline,
            "article_url": "https://example.com/story",
            "resolved_url": "",
            "publication_date": publication_date,
            "rss_text": article_text,
            "article_text": article_text,
            "extraction_status": "not_attempted",
            "extraction_error": "",
            "review_flag": "true",
            "collected_at": "2025-01-01T00:00:00+00:00",
        }


if __name__ == "__main__":
    unittest.main()
