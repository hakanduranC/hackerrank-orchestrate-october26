"""Typed loaders for every CSV under ``dataset/``.

Each loader returns immutable row objects. Nothing here interprets the data;
interpretation happens in ``identity``, ``policy`` and ``portfolio``.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp with a trailing ``Z`` into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class DeletionRequest:
    """One row of deletion_requests.csv / sample_deletion_requests.csv."""

    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True)
class Identity:
    """One row of user_identities.csv: the current identifiers of a user."""

    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    """One row of user_audit_log.csv: a historical identity change."""

    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class Activity:
    """One row of system_activity.csv: a workflow, sync, export, backup, restore or audit event (a
    claim).
    """

    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class DeletionRule:
    """One row of deletion_rules.csv."""

    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True)
class EvidenceFile:
    """One row of evidence_files.csv: the manifest entry of an evidence file."""

    file_id: str
    file_path: str
    origin: str
    captured_at: datetime


@dataclass(frozen=True)
class SampleAnswer:
    """One row of sample_output.csv."""

    case_id: str
    deletion_status: str
    steps_json: str


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def _load[T](path: Path, build: Callable[[dict[str, str]], T]) -> list[T]:
    return [build(row) for row in _rows(path)]


def load_requests(path: Path) -> list[DeletionRequest]:
    """Load a requests CSV."""
    return _load(
        path,
        lambda r: DeletionRequest(
            case_id=r["case_id"],
            email=r["email"],
            requested_at=parse_ts(r["requested_at"]),
            reported_status=r["reported_status"],
            reported_at=parse_ts(r["reported_at"]),
            audit_at=parse_ts(r["audit_at"]),
            request_text=r["request_text"],
        ),
    )


def load_identities(path: Path) -> list[Identity]:
    """Load user_identities.csv."""
    return _load(
        path,
        lambda r: Identity(
            user_id=r["user_id"],
            name=r["name"],
            email=r["email"],
            phone=r["phone"],
            email_hash=r["email_hash"],
            updated_at=parse_ts(r["updated_at"]),
        ),
    )


def load_audit_log(path: Path) -> list[AuditEvent]:
    """Load user_audit_log.csv."""
    return _load(
        path,
        lambda r: AuditEvent(
            audit_id=r["audit_id"],
            user_id=r["user_id"],
            event_time=parse_ts(r["event_time"]),
            field_name=r["field_name"],
            old_value=r["old_value"],
            new_value=r["new_value"],
        ),
    )


def load_activity(path: Path) -> list[Activity]:
    """Load system_activity.csv."""
    return _load(
        path,
        lambda r: Activity(
            activity_id=r["activity_id"],
            event_time=parse_ts(r["event_time"]),
            event_type=r["event_type"],
            source_system=r["source_system"],
            target_system=r["target_system"],
            resource_id=r["resource_id"],
            reported_result=r["reported_result"],
            details=r["details"],
        ),
    )


def load_rules(path: Path) -> list[DeletionRule]:
    """Load deletion_rules.csv."""
    return _load(
        path,
        lambda r: DeletionRule(
            rule_id=r["rule_id"],
            data_category=r["data_category"],
            condition=r["condition"],
            required_action=r["required_action"],
        ),
    )


def load_evidence_manifest(path: Path) -> list[EvidenceFile]:
    """Load evidence_files.csv."""
    return _load(
        path,
        lambda r: EvidenceFile(
            file_id=r["file_id"],
            file_path=r["file_path"],
            origin=r["origin"],
            captured_at=parse_ts(r["captured_at"]),
        ),
    )


def load_sample_answers(path: Path) -> list[SampleAnswer]:
    """Load sample_output.csv."""
    return _load(
        path,
        lambda r: SampleAnswer(
            case_id=r["case_id"],
            deletion_status=r["deletion_status"],
            steps_json=r["steps_json"],
        ),
    )


@dataclass(frozen=True)
class Dataset:
    """All participant-facing CSVs, loaded once."""

    requests: list[DeletionRequest]
    sample_requests: list[DeletionRequest]
    sample_answers: list[SampleAnswer]
    identities: list[Identity]
    audit_log: list[AuditEvent]
    activity: list[Activity]
    rules: list[DeletionRule]
    evidence_files: list[EvidenceFile]


REQUIRED_FILES = (
    "deletion_requests.csv",
    "sample_deletion_requests.csv",
    "sample_output.csv",
    "user_identities.csv",
    "user_audit_log.csv",
    "system_activity.csv",
    "deletion_rules.csv",
    "evidence_files.csv",
)


def load_dataset(dataset_dir: Path) -> Dataset:
    """Load every participant-facing CSV; name what is missing instead of failing mid-way."""
    missing = [name for name in REQUIRED_FILES if not (dataset_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"dataset directory {dataset_dir} lacks {', '.join(missing)}; set GHOST_DATASET_DIR"
            " to the folder that holds the challenge's dataset/ files"
        )
    return Dataset(
        requests=load_requests(dataset_dir / "deletion_requests.csv"),
        sample_requests=load_requests(dataset_dir / "sample_deletion_requests.csv"),
        sample_answers=load_sample_answers(dataset_dir / "sample_output.csv"),
        identities=load_identities(dataset_dir / "user_identities.csv"),
        audit_log=load_audit_log(dataset_dir / "user_audit_log.csv"),
        activity=load_activity(dataset_dir / "system_activity.csv"),
        rules=load_rules(dataset_dir / "deletion_rules.csv"),
        evidence_files=load_evidence_manifest(dataset_dir / "evidence_files.csv"),
    )
