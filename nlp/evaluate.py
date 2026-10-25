"""Exact-match precision/recall/F1 over a small manually labeled dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline import extract_accidents


FIELDS = (
    "accident_date", "district", "area", "accident_type", "cause",
    "vehicles_involved", "deaths", "injuries",
)
DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "manual_annotated.json"


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {field: {"tp": 0, "fp": 0, "fn": 0} for field in FIELDS}
    for case in cases:
        records = extract_accidents(
            case["article_text"], headline=case.get("headline", ""), language=case.get("language")
        )
        prediction = records[0] if records else {}
        for field in FIELDS:
            expected = case["gold"].get(field)
            actual = prediction.get(field)
            if expected is not None and actual == expected:
                counts[field]["tp"] += 1
            else:
                if actual is not None:
                    counts[field]["fp"] += 1
                if expected is not None:
                    counts[field]["fn"] += 1

    metrics = {}
    for field, values in counts.items():
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics[field] = {
            **values,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }
    return {"sample_count": len(cases), "fields": metrics}


def evaluate_file(path: str | Path = DEFAULT_DATASET) -> dict[str, Any]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    return evaluate_cases(cases)


if __name__ == "__main__":
    print(json.dumps(evaluate_file(), ensure_ascii=False, indent=2))
