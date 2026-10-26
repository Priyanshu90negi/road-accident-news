"""Tests for the manual collection, extraction, review, and export workflow."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from database import db
from database.db import DuplicateArticleError
from nlp.pipeline import extract_accidents
from nlp.evaluate import evaluate_file
from services import article_service, export_service, extraction_service


class ManualWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "accident_news.db"
        self.processed_dir = Path(self.temp_dir.name) / "processed"
        db.initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def article(self, **updates):
        article = {
            "headline": "Two injured in Dehradun road accident",
            "article_text": (
                "On 9 June 2025, a speeding car crashed near Mussoorie at 10:30 pm. "
                "Two people were injured and one person died."
            ),
            "source_name": "Example News",
            "source_url": "https://example.com/news/story-1",
            "published_date": "2025-06-10",
            "language": "English",
        }
        article.update(updates)
        return article

    def test_saves_and_retrieves_long_original_article_unchanged(self):
        body = ("पूरा समाचार: देहरादून में सड़क हादसा।\n" * 12_000) + "  अंत।\n"
        article_id = article_service.save_article(self.article(article_text=body), self.database)
        saved = article_service.get_article(article_id, self.database)
        self.assertEqual(saved["article_text"], body)
        self.assertGreater(len(saved["article_text"]), 100_000)

    def test_detects_duplicate_by_normalized_url_and_text(self):
        article_service.save_article(self.article(), self.database)
        with self.assertRaises(DuplicateArticleError) as duplicate:
            article_service.save_article(
                self.article(source_url="https://EXAMPLE.com/news/story-1/#top", article_text="different body"),
                self.database,
            )
        self.assertEqual(duplicate.exception.existing_article_id, 1)
        with self.assertRaises(DuplicateArticleError):
            article_service.save_article(
                self.article(source_url="https://example.com/another", article_text="  " + self.article()["article_text"].upper() + "  "),
                self.database,
            )

    def test_extracts_english_fields_and_keeps_unknown_values_blank(self):
        article = self.article(article_text=(
            "On 9 June 2025, a speeding car collided near Mussoorie at 10:30 pm. "
            "Two people were injured and one person died."
        ))
        original = article["article_text"]
        records = extract_accidents(original, headline=article["headline"], published_date="2025-06-10")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["accident_date"], "2025-06-09")
        self.assertEqual(record["accident_time"], "22:30")
        self.assertEqual(record["district"], "Dehradun")
        self.assertEqual(record["area"], "Mussoorie")
        self.assertEqual(record["cause"], "speeding")
        self.assertEqual(record["deaths"], 1)
        self.assertEqual(record["injuries"], 2)
        self.assertEqual(article["article_text"], original)

    def test_regression_ford_car_falls_into_gorge_article_extracts_and_saves(self):
        text = (
            "A 39-year-old man was killed and two others were injured after the Ford car they were "
            "travelling in fell into a nearly 100-metre-deep gorge near Chillad in Tyuni, "
            "Dehradun district, on October 2, SDRF officials said."
        )
        article_id = article_service.save_article(self.article(
            headline="Man killed, two injured after car falls into gorge near Tyuni",
            article_text=text,
            source_name="Example News",
            source_url="https://example.com/news/gorge-accident",
            published_date="2026-10-03",
        ), self.database)

        diagnostics: list[str] = []
        records = extraction_service.extract_and_save_article(
            article_id, self.database, processed_dir=self.processed_dir, diagnostics=diagnostics
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["accident_date"], "2026-10-02")
        self.assertIsNone(record["accident_time"])
        self.assertEqual(record["state"], "Uttarakhand")
        self.assertEqual(record["district"], "Dehradun")
        self.assertEqual(record["area"], "Chillad, Tyuni")
        self.assertEqual(record["vehicles_involved"], "car")
        self.assertEqual(record["accident_type"], "car fell into a gorge")
        self.assertEqual(record["deaths"], 1)
        self.assertEqual(record["injuries"], 2)
        self.assertIsNone(record["cause"])
        self.assertEqual(record["source_name"], "Example News")
        self.assertEqual(record["source_url"], "https://example.com/news/gorge-accident")
        self.assertEqual(db.get_article(article_id, self.database)["article_text"], text)
        self.assertEqual(diagnostics, [])

    def test_accident_detection_accepts_overturns_crashes_and_collisions(self):
        for text in (
            "A bus overturned in Dehradun.",
            "A truck crashed near Tyuni.",
            "A car collided with a bus in Dehradun.",
            "A car collides with a bus in Dehradun.",
        ):
            with self.subTest(text=text):
                self.assertEqual(len(extract_accidents(text)), 1)

    def test_ndtv_ramming_article_is_saved_with_supported_fields(self):
        text = (
            "A car rammed into three vehicles in Raipur, Dehradun, allegedly under the influence "
            "of alcohol."
        )
        article_id = article_service.save_article(self.article(
            headline="Car rams into three vehicles in Raipur",
            article_text=text,
            source_name="NDTV",
            source_url="https://www.ndtv.com/example/raipur-accident",
            published_date="2026-10-03",
            language="English",
        ), self.database)

        diagnostics: list[str] = []
        with patch("services.extraction_service.extract_accidents", wraps=extract_accidents) as extractor:
            records = extraction_service.extract_and_save_article(
                article_id, self.database, processed_dir=self.processed_dir, diagnostics=diagnostics
            )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["article_id"], article_id)
        extractor.assert_called_once()
        self.assertEqual(extractor.call_args.args[0], text)
        self.assertEqual(record["accident_type"], "collision")
        self.assertEqual(record["vehicles_involved"], "car")
        self.assertEqual(record["area"], "Raipur")
        self.assertEqual(record["district"], "Dehradun")
        self.assertEqual(record["cause"], "impaired driving")
        self.assertEqual(record["cause_status"], "suspected")
        self.assertIsNone(record["accident_date"])
        self.assertIsNone(record["accident_time"])
        self.assertIsNone(record["deaths"])
        self.assertIsNone(record["injuries"])
        self.assertEqual(record["source_name"], "NDTV")
        self.assertEqual(db.get_article(article_id, self.database)["article_text"], text)
        self.assertEqual(diagnostics, [])

    def test_times_of_india_gorge_article_extracts_deaths_and_injuries(self):
        text = (
            "A car fell into a deep gorge near Chillad, Tyuni, Dehradun, resulting in one death "
            "and two injuries."
        )
        article_id = article_service.save_article(self.article(
            headline="One killed, two injured as car falls into gorge near Tyuni",
            article_text=text,
            source_name="Times of India",
            source_url="https://timesofindia.indiatimes.com/example/tyuni-accident",
            published_date="2026-10-03",
            language="English",
        ), self.database)

        diagnostics: list[str] = []
        records = extraction_service.extract_and_save_article(
            article_id, self.database, processed_dir=self.processed_dir, diagnostics=diagnostics
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["article_id"], article_id)
        self.assertEqual(record["accident_type"], "car fell into a gorge")
        self.assertEqual(record["vehicles_involved"], "car")
        self.assertEqual(record["area"], "Chillad, Tyuni")
        self.assertEqual(record["district"], "Dehradun")
        self.assertEqual(record["deaths"], 1)
        self.assertEqual(record["injuries"], 2)
        self.assertIsNone(record["accident_date"], "Publication date must not become incident date.")
        self.assertIsNone(record["accident_time"])
        self.assertIsNone(record["cause"])
        self.assertEqual(record["source_name"], "Times of India")
        self.assertEqual(db.get_article(article_id, self.database)["article_text"], text)
        self.assertEqual(diagnostics, [])

    def test_extraction_service_reports_detection_exception_and_invalid_output(self):
        article_id = article_service.save_article(self.article(
            article_text="The weather was clear across Dehradun.",
            source_url="https://example.com/no-accident",
        ), self.database)
        diagnostics: list[str] = []
        self.assertEqual(
            extraction_service.extract_and_save_article(
                article_id, self.database, diagnostics=diagnostics
            ),
            [],
        )
        self.assertTrue(any("Accident not detected" in item for item in diagnostics))

        exception_article_id = article_service.save_article(self.article(
            article_text="A car crashed in Dehradun.",
            source_url="https://example.com/extraction-exception",
        ), self.database)
        diagnostics = []
        with patch("services.extraction_service.extract_accidents", side_effect=RuntimeError("parser offline")):
            self.assertEqual(
                extraction_service.extract_and_save_article(
                    exception_article_id, self.database, diagnostics=diagnostics
                ),
                [],
            )
        self.assertTrue(any("Extraction failed" in item for item in diagnostics))

        invalid_article_id = article_service.save_article(self.article(
            article_text="A bus overturned in Tyuni.",
            source_url="https://example.com/invalid-output",
        ), self.database)
        diagnostics = []
        with patch("services.extraction_service.extract_accidents", return_value={"bad": "shape"}):
            self.assertEqual(
                extraction_service.extract_and_save_article(
                    invalid_article_id, self.database, diagnostics=diagnostics
                ),
                [],
            )
        self.assertTrue(any("Invalid output format" in item for item in diagnostics))

    def test_extraction_service_reports_validation_rejection_and_zero_accepted(self):
        article_id = article_service.save_article(self.article(
            article_text="A car crashed in Dehradun.",
            source_url="https://example.com/invalid-date-record",
        ), self.database)
        diagnostics: list[str] = []
        with patch("services.extraction_service.extract_accidents", return_value=[{"accident_date": "not-a-date"}]):
            records = extraction_service.extract_and_save_article(
                article_id, self.database, diagnostics=diagnostics
            )
        self.assertEqual(records, [])
        self.assertTrue(any("Record rejected during validation" in item for item in diagnostics))
        self.assertTrue(any("zero records were accepted" in item for item in diagnostics))

    def test_hindi_dates_times_numbers_and_unicode(self):
        text = "9 जून 2025 को देहरादून में सड़क हादसा हुआ। दो लोग घायल हुए और एक व्यक्ति की मौत हो गई। रात 10 बजे बस और कार टकराईं।"
        record = extract_accidents(text, language="Hindi")[0]
        self.assertEqual(record["accident_date"], "2025-06-09")
        self.assertEqual(record["accident_time"], "22:00")
        self.assertEqual(record["district"], "Dehradun")
        self.assertEqual(record["deaths"], 1)
        self.assertEqual(record["injuries"], 2)
        self.assertIn("बस", text)

    def test_publication_date_is_not_used_as_incident_date(self):
        records = extract_accidents(
            "A car crash was reported in Dehradun. Two people were injured.",
            published_date="2025-06-10",
        )
        self.assertEqual(records[0]["accident_date"], None)
        self.assertEqual(records[0]["scope_status"], "unknown")

    def test_unknown_is_not_zero_but_explicit_zero_is_preserved(self):
        unknown = extract_accidents("A crash occurred in Dehradun in 2025.")[0]
        explicit = extract_accidents("A crash occurred in Dehradun in 2025. No one was injured.")[0]
        self.assertIsNone(unknown["deaths"])
        self.assertIsNone(unknown["injuries"])
        self.assertEqual(explicit["injuries"], 0)

    def test_conflicting_counts_are_left_unknown_and_flagged(self):
        record = extract_accidents(
            "A Dehradun crash occurred on 2025-02-03. Two people were injured, later reports said three were injured."
        )[0]
        self.assertIsNone(record["injuries"])
        self.assertEqual(record["review_status"], "needs_review")
        self.assertIn("Conflicting injury counts", json.loads(record["conflict_notes_json"])[0])

    def test_negated_cause_and_casualty_claims_are_not_extracted(self):
        record = extract_accidents(
            "A crash occurred in Dehradun on 2025-03-03. "
            "Police said speeding was not the cause, and two people were not injured."
        )[0]
        self.assertIsNone(record["cause"])
        self.assertIsNone(record["injuries"])

    def test_manual_annotation_evaluation_reports_exact_match_metrics(self):
        result = evaluate_file()
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual(result["fields"]["accident_date"]["f1"], 1.0)
        self.assertEqual(result["fields"]["accident_type"]["f1"], 1.0)

    def test_multiple_incidents_in_one_article_create_separate_records(self):
        text = (
            "On 2025-01-05, a car crash occurred near Mussoorie. One person was injured.\n\n"
            "Another accident occurred on 2025-01-08 in Doiwala when a bus overturned. Two people died."
        )
        records = extract_accidents(text)
        self.assertEqual(len(records), 2)
        self.assertEqual({record["area"] for record in records}, {"Mussoorie", "Doiwala"})
        self.assertEqual({record["accident_date"] for record in records}, {"2025-01-05", "2025-01-08"})

    def test_historical_incident_year_is_kept_in_scope(self):
        record = extract_accidents("A crash occurred in Dehradun on 1899-12-31.")[0]
        self.assertEqual(record["scope_status"], "in_scope")
        self.assertEqual(record["accident_date"], "1899-12-31")

    def test_review_records_manual_provenance_and_verified_filter(self):
        article_id = article_service.save_article(self.article(), self.database)
        records = extraction_service.extract_and_save_article(article_id, self.database, processed_dir=self.processed_dir)
        self.assertEqual(len(records), 1)
        accident_id = records[0]["accident_id"]
        extraction_service.save_review(
            accident_id,
            {
                "accident_date": "2025-06-09", "accident_time": "22:30", "district": "Dehradun",
                "area": "Mussoorie", "accident_type": "collision", "cause": "speeding",
                "vehicles_involved": "car", "deaths": "0", "injuries": "2", "weather": "",
            },
            review_status="verified",
            path=self.database,
            processed_dir=self.processed_dir,
        )
        saved = db.get_accident_record(accident_id, self.database)
        self.assertEqual(saved["deaths"], 0)
        self.assertEqual(saved["review_status"], "verified")
        self.assertEqual(json.loads(saved["field_sources_json"])["deaths"], "manual")
        self.assertEqual(len(db.list_accident_records(verified_only=True, path=self.database)), 1)

    def test_source_article_text_is_not_copied_into_accident_rows(self):
        article_id = article_service.save_article(self.article(), self.database)
        extraction_service.extract_and_save_article(article_id, self.database, processed_dir=self.processed_dir)
        with closing(db.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(accident_records)")}
        self.assertNotIn("article_text", columns)

    def test_possible_duplicate_incident_is_flagged_across_articles(self):
        first_id = article_service.save_article(self.article(), self.database)
        first_records = extraction_service.extract_and_save_article(first_id, self.database, processed_dir=self.processed_dir)
        second_id = article_service.save_article(
            self.article(
                headline="Car crash near Mussoorie", source_name="Another News",
                source_url="https://another.example/story",
                article_text=self.article()["article_text"] + " This outlet also interviewed a witness.",
            ),
            self.database,
        )
        second_records = extraction_service.extract_and_save_article(second_id, self.database, processed_dir=self.processed_dir)
        self.assertEqual(len(first_records), 1)
        self.assertEqual(len(second_records), 1)
        self.assertEqual(db.get_accident_record(first_records[0]["accident_id"], self.database)["possible_duplicate"], 1)
        self.assertEqual(second_records[0]["possible_duplicate"], 1)

    def test_csv_has_excel_bom_unicode_and_verified_filter(self):
        article_id = article_service.save_article(
            self.article(
                headline="देहरादून सड़क हादसा",
                article_text="9 जून 2025 को देहरादून में सड़क हादसा हुआ। दो लोग घायल हुए।",
                source_name="स्थानीय समाचार",
                language="Hindi",
            ),
            self.database,
        )
        record = extraction_service.extract_and_save_article(article_id, self.database, processed_dir=self.processed_dir)[0]
        unverified_path = Path(self.temp_dir.name) / "all.csv"
        export_service.export_csv(unverified_path, db_path=self.database)
        raw = unverified_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("स्थानीय समाचार".encode("utf-8"), raw)
        with unverified_path.open(encoding="utf-8-sig", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual(rows[0]["injuries"], "2")
        self.assertEqual(rows[0]["deaths"], "")
        self.assertEqual(rows[0]["review_status"], "pending")
        extraction_service.save_review(
            record["accident_id"],
            {
                "accident_date": "2025-06-09", "district": "Dehradun", "area": "Dehradun",
                "accident_type": "road accident", "cause": "", "vehicles_involved": "",
                "deaths": "", "injuries": "2", "weather": "",
            },
            review_status="verified",
            path=self.database,
            processed_dir=self.processed_dir,
        )
        verified = export_service.export_csv_bytes(verified_only=True, db_path=self.database)
        self.assertIn(b"verified", verified)


if __name__ == "__main__":
    unittest.main()
