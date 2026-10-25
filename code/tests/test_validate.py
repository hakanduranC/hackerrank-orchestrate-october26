"""Output contract validator."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from ghost.data.loaders import Dataset, DeletionRequest, EvidenceFile
from ghost.validate import validate_output

T = datetime(2026, 9, 10, tzinfo=UTC)
LATE = datetime(2026, 9, 20, tzinfo=UTC)


def _dataset() -> Dataset:
    req = DeletionRequest("case_1", "a@x.com", T, "COMPLETE", T, T, "delete me")
    files = [
        EvidenceFile("evidence_ok", "a", "o", T),
        EvidenceFile("evidence_late", "b", "o", LATE),
    ]
    return Dataset(
        requests=[req],
        sample_requests=[],
        sample_answers=[],
        identities=[],
        audit_log=[],
        activity=[],
        rules=[],
        evidence_files=files,
    )


def _write(path: Path, status: str, steps: list[dict[str, object]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        w.writerow(["case_1", status, json.dumps(steps)])
    return path


def _step(no: int, phase: str, op: str, state: str, ev: str) -> dict[str, object]:
    return {
        "step_no": no,
        "phase": phase,
        "system_id": "account_service",
        "resource_id": "account_1",
        "operation": op,
        "state": state,
        "evidence_id": ev,
    }


def test_valid_incomplete_row_passes(tmp_path: Path) -> None:
    steps = [
        _step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_ok"),
        _step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT", ""),
        _step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    ]
    report = validate_output(_write(tmp_path / "o.csv", "INCOMPLETE", steps), _dataset())
    assert report.ok and report.rows == 1


def test_violations_are_named(tmp_path: Path) -> None:
    steps = [
        _step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_late"),
        _step(3, "FINDING", "REPORT_REMAINING_RECORD", "ABSENT", ""),
    ]
    report = validate_output(_write(tmp_path / "o.csv", "COMPLETE", steps), _dataset())
    text = "\n".join(report.errors)
    assert "captured after audit_at" in text
    assert "step_no is 3" in text and "out of order" in text
    assert "must have state PRESENT" in text and "a finding must cite evidence" in text
    assert "COMPLETE may contain verification steps only" in text


def test_not_applicable_must_be_empty(tmp_path: Path) -> None:
    steps = [_step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_ok")]
    report = validate_output(_write(tmp_path / "o.csv", "NOT_APPLICABLE", steps), _dataset())
    assert any("NOT_APPLICABLE must have []" in e for e in report.errors)
