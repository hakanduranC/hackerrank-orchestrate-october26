"""Portfolio rules: status derivation, direct-PII detection, recreation flag."""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.data.observations import Observation, SourceFormat
from ghost.portfolio import (
    ABSENT,
    ACTIVE,
    ANONYMIZED,
    BLOCKED,
    IDENTIFIABLE,
    PRESENT,
    REDACTED,
    RESTORABLE,
    ActivityEvent,
    _recreated,
    derive_status,
    direct_pii,
)

T = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _obs(present: bool = True, **state: object) -> Observation:
    return Observation(
        evidence_id="e1",
        source_format=SourceFormat.JSON,
        captured_at=T,
        system="x",
        resource_id="search_1",
        present=present,
        state=dict(state),  # type: ignore[arg-type]
    )


def test_direct_pii_ignores_lookup_keys() -> None:
    obs = _obs(lookup_key="a@x.com", correlation_key="+44 20 7946 0958", requester_email="b@x.com")
    assert direct_pii(obs) == ["requester_email=b@x.com"]
    assert direct_pii(_obs(email=None, phone="null")) == []


def test_status_rules() -> None:
    assert derive_status("USER_PROFILE", None, []) == "UNKNOWN"
    assert derive_status("USER_PROFILE", _obs(present=False, http_status="404"), []) == ABSENT
    assert derive_status("SEARCH_INDEX_ENTRY", _obs(result="FOUND"), []) == PRESENT
    assert derive_status("BACKUP_ARCHIVE", _obs(result="ACTIVE"), []) == RESTORABLE
    assert (
        derive_status("BACKUP_ARCHIVE", _obs(eligible="true", deletion_block="false"), [])
        == RESTORABLE
    )
    assert derive_status("BACKUP_ARCHIVE", _obs(restore_allowed=0), []) == BLOCKED
    assert derive_status("DATA_FLOW", _obs(enabled="true"), []) == ACTIVE
    assert derive_status("ANALYTICS_PROFILE", _obs(email=None, phone=None), []) == ANONYMIZED
    assert (
        derive_status("ANALYTICS_PROFILE", _obs(email="a@x.com"), ["email=a@x.com"]) == IDENTIFIABLE
    )
    assert derive_status("SUPPORT_MESSAGE", _obs(requester_email=None), []) == REDACTED
    # A record that exists but whose observation shows no identifier columns is simply present.
    assert derive_status("SUPPORT_MESSAGE", _obs(rows="1", marker_found="false"), []) == PRESENT
    assert derive_status("SUPPORT_ATTACHMENT", _obs(file_name="id.pdf"), []) == PRESENT


def _event(when: datetime, event_type: str, result: str, details: str) -> ActivityEvent:
    return ActivityEvent("a1", when, event_type, "deletion_worker", result, details)


def test_recreated_after_deletion_or_repopulation() -> None:
    earlier = datetime(2026, 9, 1, tzinfo=UTC)
    latest = _obs()  # present at T
    deleted = [_event(earlier, "DELETE_RECORD", "SUCCESS", "Target resource: search_1.")]
    assert _recreated(deleted, latest) is True
    failed = [_event(earlier, "DELETE_RECORD", "FAILED", "final result for search_1")]
    assert _recreated(failed, latest) is False
    synced = [
        _event(earlier, "SYNC_RECORD", "SUCCESS", "repopulated downstream resource search_1.")
    ]
    assert _recreated(synced, latest) is True
    assert _recreated(deleted, _obs(present=False)) is False
