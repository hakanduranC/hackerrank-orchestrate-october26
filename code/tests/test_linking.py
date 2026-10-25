"""Window linker: status consistency, uniqueness propagation, one row per request."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ghost.data.loaders import Activity, DeletionRequest
from ghost.linking import WindowLinker

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _req(case_id: str, status: str, start_h: int, end_h: int) -> DeletionRequest:
    return DeletionRequest(
        case_id,
        f"{case_id}@x.com",
        T0 + timedelta(hours=start_h),
        status,
        T0 + timedelta(hours=end_h),
        T0 + timedelta(days=10),
        "delete me",
    )


def _row(activity_id: str, hours: float, resource: str, result: str) -> Activity:
    return Activity(
        activity_id,
        T0 + timedelta(hours=hours),
        "DELETE_RECORD",
        "deletion_worker",
        "account_service",
        resource,
        result,
        f"Deletion worker submitted {resource}.",
    )


def test_status_filter_propagation_and_unique_assignment() -> None:
    rows = [
        _row("a1", 1, "account_1", "SUCCESS"),
        _row("a2", 2, "account_2", "FAILED"),
        _row("a3", 3, "account_3", "SUCCESS"),
        _row("a4", 3.5, "account_4", "SUCCESS"),
    ]
    requests = [
        _req("c1", "COMPLETE", 0, 4),  # window holds 1, 3, 4 (SUCCESS); ambiguous
        _req("c2", "NOT_STARTED", 0, 4),  # only FAILED row 2 fits -> unique
        _req("c3", "COMPLETE", 2, 4),  # holds 3, 4 -> ambiguous, tighter than c1
    ]
    linker = WindowLinker(
        requests, rows, "account", fixed=lambda r: "account_1" if r.case_id == "c1" else None
    )
    c1, c2, c3 = (linker.link(c) for c in ("c1", "c2", "c3"))
    assert c1 and c1.resource_id == "account_1" and c1.source == "identifier"
    assert c2 and c2.resource_id == "account_2" and not c2.ambiguous  # unique after status filter
    assert c3 and c3.resource_id == "account_4" and c3.ambiguous  # closest to reported_at
    assert c3.alternatives == ("account_3",)
    assert c2.alternatives == ()  # the only status-consistent row in c2's window


def test_rows_are_never_shared_between_requests() -> None:
    rows = [_row("a1", 1, "account_1", "SUCCESS"), _row("a2", 1.5, "account_2", "SUCCESS")]
    requests = [_req("c1", "COMPLETE", 0, 2), _req("c2", "COMPLETE", 0, 2)]
    linker = WindowLinker(requests, rows, "account", fixed=lambda r: None)
    chosen = {linker.link(c).resource_id for c in ("c1", "c2")}  # type: ignore[union-attr]
    assert chosen == {"account_1", "account_2"}
