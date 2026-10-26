from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageFilter

from database import db
from database.db import DuplicateScreenshotError
from services import screenshot_service, yearly_data_service
from services.yearly_data_service import YearlyCsvSyncError


class ScreenshotWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "accident_news.db"
        self.images = self.root / "screenshots"
        self.processed = self.root / "processed"
        db.initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def image_bytes(color=(255, 255, 255)) -> bytes:
        image = Image.new("RGB", (240, 160), color=color)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def create_draft(self, text, *, metadata=None, count=1, ocr_status="readable", ocr_confidence=88):
        tint = sum(map(ord, text or "unreadable")) % 256
        uploads = [
            (f"page-{index + 1}.png", self.image_bytes((255 - index, tint, 255)))
            for index in range(count)
        ]

        def fake_ocr(content, filename):
            if ocr_status == "unreadable":
                return {
                    "raw_ocr_text": "", "ocr_confidence": None,
                    "readability_status": "unreadable", "ocr_error": "No legible text found.",
                }
            return {
                "raw_ocr_text": text if filename == "page-1.png" else f"Continuation: {text}",
                "ocr_confidence": ocr_confidence,
                "readability_status": ocr_status,
                "ocr_error": None,
            }

        return screenshot_service.create_draft(
            uploads,
            metadata or {},
            image_root=self.images,
            db_path=self.database,
            ocr_function=fake_ocr,
        )

    def test_multiple_pages_preserve_originals_and_raw_ocr_separately(self):
        screenshot_id = self.create_draft("English page one", count=2)
        draft = screenshot_service.get_draft(screenshot_id, self.database)
        self.assertEqual(len(draft["pages"]), 2)
        self.assertIn("English page one", draft["raw_ocr_text"])
        self.assertIn("Continuation: English page one", draft["raw_ocr_text"])
        self.assertEqual([page["page_order"] for page in draft["pages"]], [1, 2])
        for page in draft["pages"]:
            self.assertTrue(Path(page["image_path"]).is_file())
            self.assertEqual(
                Path(page["image_path"]).read_bytes(),
                self.image_bytes((255 - page["page_order"] + 1, sum(map(ord, "English page one")) % 256, 255)),
            )

        original_raw = draft["raw_ocr_text"]
        screenshot_service.save_draft(
            screenshot_id,
            {
                "headline": "Test headline",
                "source_name": "Example News",
                "source_url": "",
                "published_date": "",
                "language": "English",
                "reviewed_article_text": "Corrected body text, separate from raw OCR.",
            },
            self.database,
        )
        updated = screenshot_service.get_draft(screenshot_id, self.database)
        self.assertEqual(updated["raw_ocr_text"], original_raw)
        self.assertEqual(updated["reviewed_article_text"], "Corrected body text, separate from raw OCR.")

    def test_duplicate_image_submission_is_detected(self):
        image = self.image_bytes()
        screenshot_id = screenshot_service.create_draft(
            [("same.png", image)], {}, image_root=self.images, db_path=self.database,
            ocr_function=lambda *_: {
                "raw_ocr_text": "first image text", "ocr_confidence": 80,
                "readability_status": "readable", "ocr_error": None,
            },
        )
        with self.assertRaises(DuplicateScreenshotError) as duplicate:
            screenshot_service.create_draft(
                [("renamed.png", image)], {}, image_root=self.images, db_path=self.database,
                ocr_function=lambda *_: self.fail("Duplicate images should be caught before OCR."),
            )
        self.assertEqual(duplicate.exception.existing_screenshot_article_id, screenshot_id)

    def test_unreadable_page_can_be_transcribed_manually(self):
        screenshot_id = self.create_draft(
            "", ocr_status="unreadable", metadata={"published_date": "2024-06-10"}
        )
        page = screenshot_service.get_draft(screenshot_id, self.database)["pages"][0]
        self.assertEqual(page["readability_status"], "unreadable")
        self.assertEqual(page["ocr_error"], "No legible text found.")
        screenshot_service.save_draft(
            screenshot_id,
            {
                "headline": "Unknown headline",
                "source_name": "",
                "source_url": "",
                "published_date": "2024-06-10",
                "language": "English",
                "reviewed_article_text": "A car crash occurred in Dehradun. Two people were injured.",
            },
            self.database,
        )
        extracted = screenshot_service.extract_draft_records(screenshot_id, self.database)
        self.assertEqual(len(extracted), 1)

    def test_tesseract_discovery_configures_pytesseract_command(self):
        pytesseract_stub = SimpleNamespace(pytesseract=SimpleNamespace(tesseract_cmd=""))
        version_result = subprocess.CompletedProcess(
            [r"D:\tesseract\tesseract.exe", "--version"], 0,
            stdout="tesseract v5.5.3\n", stderr="",
        )
        with (
            patch.dict(os.environ, {"TESSERACT_CMD": "", "TESSERACT_FALLBACK_CMD": ""}),
            patch("services.screenshot_service.shutil.which", return_value=r"D:\tesseract\tesseract.exe"),
            patch("services.screenshot_service.subprocess.run", return_value=version_result) as run,
            patch("services.screenshot_service.pytesseract", pytesseract_stub),
        ):
            binary = screenshot_service._tesseract_binary()

        self.assertEqual(binary, r"D:\tesseract\tesseract.exe")
        self.assertEqual(pytesseract_stub.pytesseract.tesseract_cmd, binary)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [binary, "--version"])

    def test_tesseract_uses_configured_fallback_when_path_discovery_fails(self):
        fallback = self.root / "fallback" / "tesseract.exe"
        fallback.parent.mkdir()
        fallback.write_bytes(b"test executable placeholder")
        pytesseract_stub = SimpleNamespace(pytesseract=SimpleNamespace(tesseract_cmd=""))
        version_result = subprocess.CompletedProcess(
            [str(fallback), "--version"], 0, stdout="tesseract v5.5.3\n", stderr="",
        )
        with (
            patch.dict(os.environ, {
                "TESSERACT_CMD": "", "TESSERACT_FALLBACK_CMD": str(fallback),
            }),
            patch("services.screenshot_service.shutil.which", return_value=None),
            patch("services.screenshot_service.subprocess.run", return_value=version_result),
            patch("services.screenshot_service.pytesseract", pytesseract_stub),
        ):
            binary = screenshot_service._tesseract_binary()

        self.assertEqual(binary, str(fallback))
        self.assertEqual(pytesseract_stub.pytesseract.tesseract_cmd, str(fallback))

    def test_invalid_tesseract_override_has_actionable_error(self):
        configured = str(self.root / "missing" / "tesseract.exe")
        with (
            patch.dict(os.environ, {"TESSERACT_CMD": configured}),
            patch("services.screenshot_service.subprocess.run", side_effect=FileNotFoundError(configured)),
        ):
            with self.assertRaises(screenshot_service.OCRUnavailableError) as error:
                screenshot_service._tesseract_binary()

        self.assertIn("TESSERACT_CMD", str(error.exception))
        self.assertIn(configured, str(error.exception))

    def test_ocr_reports_missing_language_codes_and_trained_data_files(self):
        with (
            patch.dict(os.environ, {"TESSDATA_PREFIX": ""}),
            patch("services.screenshot_service._tesseract_binary", return_value=str(self.root / "tesseract.exe")),
            patch("services.screenshot_service._tesseract_language_info", return_value=({"eng"}, None)),
        ):
            result = screenshot_service.ocr_image(b"not needed when language data is incomplete")

        self.assertEqual(result["readability_status"], "unreadable")
        self.assertIn("language codes not listed: hin", result["ocr_error"])
        self.assertIn("hin.traineddata", result["ocr_error"])

    def test_conflicting_counts_and_missing_dates_are_not_invented(self):
        conflict_text = (
            "A Dehradun crash occurred on 2025-02-03. Two people were injured, "
            "later reports said three were injured."
        )
        screenshot_id = self.create_draft(conflict_text)
        records = screenshot_service.extract_draft_records(screenshot_id, self.database)
        record = records[0]
        self.assertIsNone(record["record"]["injuries"])
        self.assertEqual(record["record"]["accident_date"], "2025-02-03")
        self.assertTrue(any("Conflicting injury counts" in note for note in record["conflicts"]))
        self.assertEqual(db.list_accident_records(path=self.database), [])

        unknown_id = self.create_draft("A car crash occurred in Dehradun. Two people were injured.")
        unknown = screenshot_service.extract_draft_records(unknown_id, self.database)[0]
        self.assertIsNone(unknown["record"]["accident_date"])
        self.assertIsNone(unknown["record"].get("occurrence_year"))

    def test_approval_uses_central_service_evidence_and_arbitrary_accident_year(self):
        text = (
            "On 9 June 1892, a speeding car collided near Mussoorie at 10:30 pm. "
            "Two people were injured and one person died."
        )
        screenshot_id = self.create_draft(
            text,
            metadata={
                "headline": "Historical crash report", "source_name": "Example News",
                "published_date": "2025-06-10", "language": "English",
            },
        )
        records = screenshot_service.extract_draft_records(screenshot_id, self.database)
        self.assertEqual(len(records), 1)
        screenshot_record_id = records[0]["screenshot_record_id"]
        screenshot_service.save_draft_record(
            screenshot_record_id,
            {
                "accident_date": "1892-06-09", "accident_time": "22:30",
                "district": "Dehradun", "area": "Mussoorie", "deaths": 1, "injuries": 2,
            },
            db_path=self.database,
        )
        approved = screenshot_service.approve_draft_record(
            screenshot_record_id, db_path=self.database, processed_dir=self.processed
        )
        incident = db.get_accident_record(approved["accident_id"], self.database)
        self.assertEqual(incident["occurrence_year"], 1892)
        self.assertEqual(incident["published_date"], "2025-06-10")
        self.assertEqual(incident["review_status"], "verified")
        self.assertIn("Mussoorie", json.loads(incident["evidence_json"])["accident_date"]["snippet"])
        self.assertTrue((self.processed / "accidents_1892.csv").is_file())
        self.assertEqual(screenshot_service.get_draft(screenshot_id, self.database)["status"], "approved")
        first_export = yearly_data_service.sync_year(1892, db_path=self.database, processed_dir=self.processed)
        first_bytes = first_export.read_bytes()
        second_export = yearly_data_service.sync_year(1892, db_path=self.database, processed_dir=self.processed)
        self.assertEqual(first_export, second_export)
        self.assertEqual(first_bytes, second_export.read_bytes())

        repeated = screenshot_service.approve_draft_record(screenshot_record_id, db_path=self.database)
        self.assertTrue(repeated["already_approved"])
        self.assertEqual(len(db.list_accident_records(path=self.database)), 1)

    def test_approved_unknown_date_stays_unassigned(self):
        screenshot_id = self.create_draft(
            "A car crash occurred in Dehradun. Two people were injured.",
            metadata={"published_date": "2024-06-10", "language": "English"},
        )
        record = screenshot_service.extract_draft_records(screenshot_id, self.database)[0]
        result = screenshot_service.approve_draft_record(
            record["screenshot_record_id"], db_path=self.database, processed_dir=self.processed
        )
        central = db.get_accident_record(result["accident_id"], self.database)
        self.assertIsNone(central["accident_date"])
        self.assertIsNone(central["occurrence_year"])
        self.assertEqual(central["review_status"], "verified")
        self.assertEqual(result["unassigned_year"], 1)
        self.assertEqual(list(self.processed.glob("accidents_*.csv")), [])

    def test_csv_sync_failure_is_reported_after_database_commit(self):
        screenshot_id = self.create_draft(
            "A crash occurred in Dehradun on 2024-02-03. One person was injured.",
            metadata={"language": "English"},
        )
        record = screenshot_service.extract_draft_records(screenshot_id, self.database)[0]
        not_a_directory = self.root / "not-a-directory"
        not_a_directory.write_text("occupied", encoding="utf-8")
        with patch("services.yearly_data_service.LOGGER.exception"):
            with self.assertRaises(YearlyCsvSyncError):
                screenshot_service.approve_draft_record(
                    record["screenshot_record_id"], db_path=self.database, processed_dir=not_a_directory
                )
        central = db.list_accident_records(path=self.database)
        self.assertEqual(len(central), 1)
        self.assertEqual(central[0]["review_status"], "verified")
        saved_draft = screenshot_service.get_draft(screenshot_id, self.database)
        self.assertEqual(saved_draft["records"][0]["status"], "approved")

    def test_database_failure_removes_unlinked_original_files(self):
        original_insert = db.insert_screenshot_draft
        try:
            with patch("services.screenshot_service.db.insert_screenshot_draft", side_effect=sqlite3.OperationalError("disk full")):
                with self.assertRaises(sqlite3.OperationalError):
                    self.create_draft("body")
        finally:
            db.insert_screenshot_draft = original_insert
        self.assertFalse(self.images.exists() and any(self.images.iterdir()))

    def test_english_and_hindi_tesseract_fixtures(self):
        fixture_dir = Path(__file__).parent / "fixtures"
        try:
            languages = screenshot_service.tesseract_languages()
        except screenshot_service.OCRUnavailableError as error:
            self.skipTest(str(error))
        if not {"eng", "hin"}.issubset(languages):
            self.skipTest("Tesseract English and Hindi language packs are required for OCR integration tests.")
        english = screenshot_service.ocr_image((fixture_dir / "ocr_english_smoke.png").read_bytes())
        hindi = screenshot_service.ocr_image((fixture_dir / "ocr_hindi_smoke.png").read_bytes())
        self.assertEqual(english["readability_status"], "readable")
        self.assertIn("Dehradun", english["raw_ocr_text"])
        self.assertEqual(hindi["readability_status"], "readable")
        self.assertIn("देहरादून", hindi["raw_ocr_text"])
        sample_upload = (fixture_dir / "ocr_english_smoke.png").read_bytes()
        screenshot_id = screenshot_service.create_draft(
            [("sample-news-screenshot.png", sample_upload)],
            {"headline": "Sample road accident report", "language": "English"},
            image_root=self.images,
            db_path=self.database,
        )
        uploaded_draft = screenshot_service.get_draft(screenshot_id, self.database)
        self.assertIn("Dehradun", uploaded_draft["raw_ocr_text"])
        self.assertIn("Dehradun", uploaded_draft["pages"][0]["raw_ocr_text"])

        english_image = Image.open(fixture_dir / "ocr_english_smoke.png").filter(ImageFilter.GaussianBlur(radius=8))
        blurry_bytes = BytesIO()
        english_image.save(blurry_bytes, format="PNG")
        blurry = screenshot_service.ocr_image(blurry_bytes.getvalue())
        self.assertIn(blurry["readability_status"], {"uncertain", "unreadable"})

    def test_article_schema_migration_preserves_existing_rows_and_adds_draft_tables(self):
        legacy_db = self.root / "legacy.db"
        connection = sqlite3.connect(legacy_db)
        connection.executescript(
            """CREATE TABLE news_articles (
                   article_id INTEGER PRIMARY KEY AUTOINCREMENT,
                   headline TEXT NOT NULL CHECK(length(trim(headline)) > 0),
                   article_text TEXT NOT NULL CHECK(length(trim(article_text)) > 0),
                   source_name TEXT NOT NULL CHECK(length(trim(source_name)) > 0),
                   source_url TEXT NOT NULL CHECK(length(trim(source_url)) > 0),
                   source_url_key TEXT NOT NULL UNIQUE,
                   article_text_hash TEXT NOT NULL UNIQUE,
                   published_date TEXT NOT NULL,
                   language TEXT NOT NULL CHECK(language IN ('English', 'Hindi')),
                   created_at TEXT NOT NULL
               );
               INSERT INTO news_articles VALUES
                 (1, 'Old headline', 'Old article body', 'Old source', 'https://example.com/old',
                  'https://example.com/old', 'old-hash', '2020-01-02', 'English', '2020-01-03');"""
        )
        connection.close()
        db.initialize_database(legacy_db)
        connection = sqlite3.connect(legacy_db)
        self.assertEqual(connection.execute("SELECT article_id, headline FROM news_articles").fetchone(), (1, "Old headline"))
        article_info = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(news_articles)")}
        self.assertEqual(article_info["published_date"], 0)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"screenshot_articles", "screenshot_pages", "screenshot_draft_records"}.issubset(tables))
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()


if __name__ == "__main__":
    unittest.main()
