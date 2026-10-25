"""Tests for dynamic occurrence-year storage and CSV synchronization."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from database import db
from services import yearly_data_service as years
from services.export_service import EXPORT_FIELDS


class YearlyDataManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "central.db"
        self.processed = self.root / "processed"
        db.initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, year: int, **updates):
        result = {
            "headline": f"Road accident in {year}",
            "accident_date": f"{year:04d}-03-14",
            "published_date": f"{year:04d}-03-15",
            "district": "Dehradun",
            "area": "Doiwala",
            "accident_type": "collision",
            "cause": None,
            "deaths": None,
            "injuries": 2,
            "source_name": "Local News",
            "source_url": f"https://example.test/{year}",
            "language": "English",
        }
        result.update(updates)
        return result

    def add(self, record, **kwargs):
        return years.add_record(record, db_path=self.database, processed_dir=self.processed, **kwargs)

    def test_dynamic_year_paths_and_empty_exports_have_consistent_headers(self):
        output = years.sync_year(2042, db_path=self.database, processed_dir=self.processed)
        self.assertEqual(output.name, "accidents_2042.csv")
        self.assertTrue(output.exists())
        with output.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file)
            self.assertEqual(next(reader), list(EXPORT_FIELDS))
            self.assertEqual(list(reader), [])
        with self.assertRaises(ValueError):
            years.validate_year("20x5")
        with self.assertRaises(ValueError):
            years.validate_year(0)
        self.assertEqual(years.validate_year("0001"), 1)
        self.assertEqual(years.yearly_csv_path(1, self.processed).name, "accidents_0001.csv")

    def test_records_from_multiple_years_share_database_but_have_separate_csvs(self):
        first = self.add(self.record(2023), selected_year=2023)
        second = self.add(self.record(2026), selected_year=2026)
        self.assertEqual(first["saved"], 1)
        self.assertEqual(second["saved"], 1)
        self.assertEqual(years.available_years(db_path=self.database), [2023, 2026])
        self.assertEqual(len(years.list_records(2023, db_path=self.database)), 1)
        self.assertEqual(len(years.list_records(2026, db_path=self.database)), 1)
        self.assertTrue((self.processed / "accidents_2023.csv").exists())
        self.assertTrue((self.processed / "accidents_2026.csv").exists())
        self.assertFalse((self.processed / "accidents_2024.csv").exists())
        combined = years.export_all_years(output_path=self.root / "all_years.csv", db_path=self.database)
        with combined.open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 2)

    def test_historical_and_future_years_are_not_hardcoded(self):
        historical = self.record(2023, accident_date="0001-03-14", published_date="0001-03-15", source_url="https://example.test/0001")
        future = self.record(2023, accident_date="9999-03-14", published_date="9999-03-15", source_url="https://example.test/9999")
        self.assertEqual(self.add(historical, selected_year=1)["saved"], 1)
        self.assertEqual(self.add(future, selected_year=9999)["saved"], 1)
        self.assertTrue((self.processed / "accidents_0001.csv").exists())
        self.assertTrue((self.processed / "accidents_9999.csv").exists())

    def test_duplicate_submission_is_skipped_and_year_can_be_rebuilt(self):
        self.assertEqual(self.add(self.record(2024))["saved"], 1)
        duplicate = self.record(2024, source_url="https://EXAMPLE.test/2024/#top")
        result = self.add(duplicate)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["duplicates_skipped"], 1)
        self.assertEqual(len(db.list_accident_records(path=self.database)), 1)
        recovered = years.sync_year(2024, db_path=self.database, processed_dir=self.processed)
        self.assertTrue(recovered.exists())

    def test_unknown_accident_date_does_not_inherit_publication_year(self):
        result = self.add(
            self.record(2025, accident_date="", occurrence_year="", published_date="2025-07-01"),
            selected_year=2025,
            year_basis="accident",
        )
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["unassigned_year"], 1)
        record = db.list_accident_records(path=self.database)[0]
        self.assertIsNone(record["occurrence_year"])
        self.assertEqual(years.available_years(db_path=self.database), [])
        self.assertEqual(len(years.list_records(2025, year_basis="publication", db_path=self.database)), 1)
        self.assertFalse((self.processed / "accidents_2025.csv").exists())

    def test_selected_year_checks_the_chosen_date_basis(self):
        result = self.add(self.record(2023), selected_year=2024, year_basis="accident")
        self.assertEqual(result["saved"], 0)
        self.assertIn("not selected occurrence year", result["validation_errors"][0])
        publication_check = self.add(
            self.record(2023, published_date="2024-01-02"),
            selected_year=2024,
            year_basis="publication",
        )
        self.assertEqual(publication_check["saved"], 1)
        self.assertEqual(len(years.list_records(2024, year_basis="publication", db_path=self.database)), 1)
        self.assertEqual(years.available_years(db_path=self.database), [2023])

    def test_update_moving_year_rewrites_old_and_new_csvs(self):
        record_id = self.add(self.record(2023))["inserted_ids"][0]
        result = years.update_record(
            record_id,
            {"accident_date": "2024-03-14"},
            db_path=self.database,
            processed_dir=self.processed,
        )
        self.assertEqual((result["old_year"], result["new_year"]), (2023, 2024))
        with (self.processed / "accidents_2023.csv").open(encoding="utf-8-sig") as file:
            old_rows = list(csv.DictReader(file))
        with (self.processed / "accidents_2024.csv").open(encoding="utf-8-sig") as file:
            new_rows = list(csv.DictReader(file))
        self.assertEqual(old_rows, [])
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0]["occurrence_year"], "2024")

    def test_delete_updates_database_and_keeps_empty_year_csv(self):
        record_id = self.add(self.record(2023))["inserted_ids"][0]
        result = years.delete_record(record_id, db_path=self.database, processed_dir=self.processed)
        self.assertEqual(result["deleted_year"], 2023)
        self.assertEqual(db.list_accident_records(path=self.database), [])
        path = self.processed / "accidents_2023.csv"
        self.assertTrue(path.exists())
        with path.open(encoding="utf-8-sig") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 0)

    def test_csv_sync_failure_is_reported_and_recoverable_from_database(self):
        with self.assertLogs(years.LOGGER, level="ERROR"):
            with patch.object(years, "write_dataframe_atomically", side_effect=OSError("disk is full")):
                with self.assertRaises(years.YearlyCsvSyncError) as error:
                    self.add(self.record(2027))
        self.assertIn(2027, error.exception.years)
        self.assertEqual(len(years.list_records(2027, db_path=self.database)), 1)
        repaired = years.sync_year(2027, db_path=self.database, processed_dir=self.processed)
        self.assertTrue(repaired.exists())
        with repaired.open(encoding="utf-8-sig") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 1)

    def test_failed_atomic_replace_keeps_previous_csv_until_recovery(self):
        record_id = self.add(self.record(2023))["inserted_ids"][0]
        old_path = self.processed / "accidents_2023.csv"
        old_bytes = old_path.read_bytes()
        with self.assertLogs(years.LOGGER, level="ERROR"):
            with patch("services.export_service.os.replace", side_effect=PermissionError("file is open")):
                with self.assertRaises(years.YearlyCsvSyncError):
                    years.update_record(
                        record_id,
                        {"accident_date": "2024-03-14"},
                        db_path=self.database,
                        processed_dir=self.processed,
                    )
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(db.get_accident_record(record_id, self.database)["occurrence_year"], 2024)
        years.sync_years([2023, 2024], db_path=self.database, processed_dir=self.processed)
        self.assertEqual(old_path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_csv_import_reports_validation_errors_and_saves_valid_rows(self):
        source = self.root / "import.csv"
        source.write_text(
            "headline,accident_date,published_date,district,area,source_url\n"
            "Valid,2022-05-01,2022-05-02,Dehradun,Raipur,https://example.test/import-1\n"
            ",2022-05-03,2022-05-04,Dehradun,Raipur,https://example.test/import-2\n",
            encoding="utf-8",
        )
        result = years.import_csv(
            source, selected_year=2022, db_path=self.database, processed_dir=self.processed
        )
        self.assertEqual(result["saved"], 1)
        self.assertEqual(len(result["validation_errors"]), 1)
        self.assertEqual(len(years.list_records(2022, db_path=self.database)), 1)

    def test_database_batch_rolls_back_if_a_database_constraint_fails(self):
        records = [
            {"headline": "Valid", "accident_date": "2020-01-01", "deaths": 0},
            {"headline": "Invalid", "accident_date": "2020-01-02", "deaths": -1},
        ]
        with self.assertRaises(sqlite3.IntegrityError):
            db.insert_accident_records(records, self.database)
        self.assertEqual(db.list_accident_records(path=self.database), [])

    def test_migrates_existing_linked_records_without_losing_them(self):
        path = self.root / "legacy.db"
        db.initialize_database(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TABLE accident_records")
            connection.execute(
                """CREATE TABLE accident_records (
                    accident_id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL,
                    accident_date TEXT, accident_time TEXT, district TEXT, area TEXT,
                    accident_type TEXT, cause TEXT, cause_status TEXT, vehicles_involved TEXT,
                    deaths INTEGER, injuries INTEGER, weather TEXT, extraction_confidence REAL,
                    review_status TEXT NOT NULL DEFAULT 'pending', accident_date_status TEXT,
                    scope_status TEXT NOT NULL DEFAULT 'unknown', scope_reason TEXT NOT NULL DEFAULT '',
                    possible_duplicate INTEGER NOT NULL DEFAULT 0, extracted_fields_json TEXT NOT NULL DEFAULT '{}',
                    field_sources_json TEXT NOT NULL DEFAULT '{}', conflict_notes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT INTO news_articles
                   (headline, article_text, source_name, source_url, source_url_key,
                    article_text_hash, published_date, language, created_at)
                   VALUES ('Legacy story', 'Raw article', 'Source', 'https://legacy.test/story',
                           'https://legacy.test/story', 'hash', '2023-01-02', 'English', 'now')"""
            )
            connection.execute(
                """INSERT INTO accident_records
                   (accident_id, article_id, accident_date, district, area, injuries, deaths,
                    created_at, updated_at)
                   VALUES (77, 1, '2023-01-01', 'Dehradun', 'Mussoorie', 2, NULL, 'now', 'now')"""
            )
        db.initialize_database(path)
        migrated = db.get_accident_record(77, path)
        self.assertEqual(migrated["occurrence_year"], 2023)
        self.assertEqual(migrated["headline"], "Legacy story")
        self.assertEqual(migrated["article_text"], "Raw article")


if __name__ == "__main__":
    unittest.main()
