"""CSV export for structured accident records."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from database import db


EXPORT_FIELDS = (
    "accident_id", "article_id", "accident_date", "accident_time", "district", "area",
    "accident_type", "cause", "vehicles_involved", "deaths", "injuries", "weather",
    "extraction_confidence", "review_status", "scope_status", "scope_reason",
    "possible_duplicate", "source_name", "source_url",
)
DEFAULT_EXPORT_PATH = Path(__file__).resolve().parents[1] / "data" / "exports" / "accident_records.csv"


def export_dataframe(*, verified_only: bool = False, db_path=None) -> pd.DataFrame:
    records = db.list_accident_records(verified_only=verified_only, path=db_path)
    return pd.DataFrame(records, columns=EXPORT_FIELDS)


def export_csv(
    output_path: str | Path | None = None,
    *,
    verified_only: bool = False,
    db_path=None,
) -> Path:
    target = Path(output_path or DEFAULT_EXPORT_PATH).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    export_dataframe(verified_only=verified_only, db_path=db_path).to_csv(
        target, index=False, encoding="utf-8-sig", na_rep=""
    )
    return target


def export_csv_bytes(*, verified_only: bool = False, db_path=None) -> bytes:
    return export_dataframe(verified_only=verified_only, db_path=db_path).to_csv(
        index=False, encoding="utf-8-sig", na_rep=""
    ).encode("utf-8-sig")
