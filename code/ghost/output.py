"""Serialisation of case results to the ``output.csv`` contract."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ghost.agent.schemas import CaseResult

COLUMNS = ["case_id", "deletion_status", "steps_json"]


def steps_json(result: CaseResult) -> str:
    """Compact JSON array; key order follows the contract, no whitespace."""
    return json.dumps(
        [step.model_dump() for step in result.steps], ensure_ascii=False, separators=(",", ":")
    )


def _sort_key(case_id: str) -> tuple[str, int]:
    prefix, _, number = case_id.rpartition("_")
    return (prefix, int(number) if number.isdigit() else 0)


def write_output(results: list[CaseResult], path: Path) -> None:
    """Write results as output.csv, sorted by case number."""
    rows = sorted(results, key=lambda r: _sort_key(r.case_id))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for result in rows:
            writer.writerow([result.case_id, result.deletion_status, steps_json(result)])
