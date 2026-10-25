"""Contract validation of ``output.csv`` against the problem statement.

Run as the last step of every full run and on demand with ``--validate``:
exactly one row per request, sorted by case id, allowed statuses, valid
``steps_json`` with the allowed phases / operations / states in the required
order, consecutive numbering, empty steps for NOT_APPLICABLE, finding evidence
from the manifest and captured at or before the case's ``audit_at``, and empty
evidence on remediation / future verification steps.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ghost.compose import OPERATION_ORDER, OPERATION_STATE, PHASE_ORDER
from ghost.data.loaders import Dataset, EvidenceFile

STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


@dataclass
class Report:
    """Validation outcome: row count and violations."""

    rows: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there are no violations."""
        return not self.errors


def _case_number(case_id: str) -> int:
    try:
        return int(case_id.rsplit("_", 1)[-1])
    except ValueError:
        return 0


def validate_output(path: Path, data: Dataset) -> Report:
    """Check an output file against the contract; returns every violation."""
    report = Report()
    requests = {r.case_id: r for r in data.requests}
    files = {f.file_id: f for f in data.evidence_files}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            report.errors.append(f"header is {reader.fieldnames}")
            return report
        rows = list(reader)
    report.rows = len(rows)
    ids = [r["case_id"] for r in rows]
    if set(ids) != set(requests):
        missing, extra = set(requests) - set(ids), set(ids) - set(requests)
        report.errors.append(f"case ids differ from deletion_requests.csv: -{missing} +{extra}")
    if ids != sorted(ids, key=_case_number):
        report.errors.append("rows are not sorted by case_id")
    if len(set(ids)) != len(ids):
        report.errors.append("duplicate case ids")
    for row in rows:
        req = requests.get(row["case_id"])
        audit_at = req.audit_at if req else None
        report.errors.extend(f"{row['case_id']}: {e}" for e in _check_row(row, audit_at, files))
    return report


def _check_row(
    row: dict[str, str], audit_at: datetime | None, files: dict[str, EvidenceFile]
) -> list[str]:
    errors: list[str] = []
    status = row["deletion_status"]
    if status not in STATUSES:
        errors.append(f"status {status!r} not allowed")
    try:
        steps = json.loads(row["steps_json"])
    except json.JSONDecodeError as exc:
        return [*errors, f"steps_json is not valid JSON ({exc})"]
    if not isinstance(steps, list):
        return [*errors, "steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE" and steps:
        errors.append("NOT_APPLICABLE must have []")
    if status == "COMPLETE" and any(s.get("phase") != "VERIFICATION" for s in steps):
        errors.append("COMPLETE may contain verification steps only")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
        errors.append("INCOMPLETE needs at least one finding")
    errors.extend(_check_steps(steps, audit_at, files))
    return errors


def _check_steps(
    steps: list[dict[str, object]], audit_at: datetime | None, files: dict[str, EvidenceFile]
) -> list[str]:
    errors: list[str] = []
    last_key: tuple[int, int, str, str] | None = None
    for i, step in enumerate(steps, start=1):
        if set(step) != STEP_FIELDS:
            errors.append(f"step {i}: fields {sorted(step)}")
            continue
        if step["step_no"] != i:
            errors.append(f"step {i}: step_no is {step['step_no']}")
        phase, op, state = str(step["phase"]), str(step["operation"]), str(step["state"])
        if phase not in PHASE_ORDER or op not in OPERATION_ORDER:
            errors.append(f"step {i}: unknown phase/operation {phase}/{op}")
            continue
        if OPERATION_STATE.get(op) != state:
            errors.append(f"step {i}: {op} must have state {OPERATION_STATE.get(op)}, not {state}")
        key = (
            PHASE_ORDER[phase],
            OPERATION_ORDER[op],
            str(step["system_id"]),
            str(step["resource_id"]),
        )
        if last_key is not None and key < last_key:
            errors.append(f"step {i}: out of order")
        last_key = key
        errors.extend(_check_evidence(i, phase, str(step["evidence_id"]), audit_at, files))
    return errors


def _check_evidence(
    i: int, phase: str, evidence: str, audit_at: datetime | None, files: dict[str, EvidenceFile]
) -> list[str]:
    if phase == "REMEDIATION" and evidence:
        return [f"step {i}: remediation steps carry no evidence"]
    if not evidence:
        return [] if phase != "FINDING" else [f"step {i}: a finding must cite evidence"]
    if evidence.startswith("evidence_"):
        entry = files.get(evidence)
        if entry is None:
            return [f"step {i}: {evidence} is not in evidence_files.csv"]
        if audit_at is not None and entry.captured_at > audit_at:
            return [f"step {i}: {evidence} captured after audit_at"]
        return []
    if evidence.startswith(("activity_", "audit_")):
        # The output contract allows activity / audit ids as references; this program never
        # emits them for state (the guards refuse them), but the validator checks the
        # contract, not our stricter policy.
        return []
    return [f"step {i}: evidence {evidence!r} is not a file_id, activity_id or audit_id"]
