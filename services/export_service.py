"""CSV export for structured accident records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

from database import db


EXPORT_FIELDS = (
    "accident_id", "record_key", "occurrence_year", "article_id", "headline", "published_date",
    "accident_date", "accident_time", "district", "area", "accident_type", "cause",
    "vehicles_involved", "deaths", "injuries", "weather", "extraction_confidence",
    "review_status", "scope_status", "scope_reason", "possible_duplicate", "source_name",
    "source_url", "language",
)
DEFAULT_EXPORT_PATH = Path(__file__).resolve().parents[1] / "data" / "exports" / "accident_records.csv"


def export_dataframe(
    *,
    verified_only: bool = False,
    year: int | None = None,
    year_basis: str = "accident",
    db_path=None,
) -> pd.DataFrame:
    records = db.list_accident_records(
        verified_only=verified_only, year=year, year_basis=year_basis, path=db_path
    )
    return pd.DataFrame(records, columns=EXPORT_FIELDS)


def write_dataframe_atomically(dataframe: pd.DataFrame, output_path: str | Path) -> Path:
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", suffix=".tmp",
            prefix=f".{target.stem}-", dir=target.parent, delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            dataframe.to_csv(temporary_file, index=False, na_rep="")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
        return target
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def export_csv(
    output_path: str | Path | None = None,
    *,
    verified_only: bool = False,
    db_path=None,
) -> Path:
    target = Path(output_path or DEFAULT_EXPORT_PATH).expanduser().resolve()
    return write_dataframe_atomically(
        export_dataframe(verified_only=verified_only, db_path=db_path), target
    )


def export_csv_bytes(*, verified_only: bool = False, db_path=None) -> bytes:
    return export_dataframe(verified_only=verified_only, db_path=db_path).to_csv(
        index=False, encoding="utf-8-sig", na_rep=""
    ).encode("utf-8-sig")
