"""Window-based linking for records that carry no identifiers.

A deleted account is a 404 and an anonymized analytics profile is all nulls, so
after deletion these records cannot be matched by identifier. What remains is
the activity log: the deletion worker processes a request between its
``requested_at`` and ``reported_at``. Each activity row belongs to exactly one
request, so the assignment is solved jointly over all requests:

1. requests whose record is identifier-linked are fixed first;
2. a request whose window holds exactly one unassigned candidate row takes it
   (repeated until nothing changes);
3. what remains is ambiguous: the closest row to ``reported_at`` is chosen and
   the alternatives are kept so the trace and the agent can see them.

For the account row the worker's result also has to agree with the request's
``reported_status``; policy rows for downstream records (anonymize, redact)
succeed or fail independently of the account, so that filter is optional.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from ghost.data.loaders import Activity, DeletionRequest

STATUS_RESULTS: dict[str, frozenset[str]] = {
    "COMPLETE": frozenset({"SUCCESS"}),
    "PARTIALLY_COMPLETE": frozenset({"PARTIAL", "SUCCESS"}),
    "NOT_STARTED": frozenset({"FAILED"}),
}
WINDOW_SLACK = timedelta(minutes=15)


@dataclass(frozen=True)
class WindowLink:
    """A record tied to a request by the deletion workflow's time window, with the alternatives it
    could not be separated from.
    """

    resource_id: str
    activity_id: str
    ambiguous: bool
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    source: str = "window"


def _in_window(req: DeletionRequest, row: Activity) -> bool:
    return req.requested_at <= row.event_time <= req.reported_at + WINDOW_SLACK


def _status_ok(req: DeletionRequest, row: Activity) -> bool:
    return row.reported_result in STATUS_RESULTS.get(req.reported_status, frozenset())


class WindowLinker:
    """Joint assignment of one deletion-worker row per request for one resource prefix."""

    def __init__(
        self,
        requests: list[DeletionRequest],
        activity: list[Activity],
        prefix: str,
        fixed: Callable[[DeletionRequest], str | None],
        event_types: frozenset[str] = frozenset({"DELETE_RECORD"}),
        use_status: bool = True,
    ) -> None:
        self._use_status = use_status
        self._rows = [
            a
            for a in activity
            if a.source_system == "deletion_worker"
            and a.event_type in event_types
            and a.resource_id.startswith(prefix + "_")
        ]
        self._links: dict[str, WindowLink] = {}
        self._assign(requests, fixed)

    def _candidates(self, req: DeletionRequest, taken: set[str]) -> list[Activity]:
        return [
            a
            for a in self._rows
            if _in_window(req, a)
            and (not self._use_status or _status_ok(req, a))
            and a.resource_id not in taken
        ]

    def _assign(
        self, requests: list[DeletionRequest], fixed: Callable[[DeletionRequest], str | None]
    ) -> None:
        taken: set[str] = set()
        pending: list[DeletionRequest] = []
        for req in requests:
            resource = fixed(req)
            if resource is not None:
                taken.add(resource)
                row = next((a for a in self._rows if a.resource_id == resource), None)
                self._links[req.case_id] = WindowLink(
                    resource, row.activity_id if row else "", False, source="identifier"
                )
            else:
                pending.append(req)
        # Tightest windows first throughout: a request with little slack has the strongest claim.
        pending.sort(key=lambda r: r.reported_at - r.requested_at)
        changed = True
        while changed:
            changed = False
            for req in list(pending):
                cands = self._candidates(req, taken)
                if len(cands) == 1:
                    self._take(req, cands[0], ambiguous=False)
                    taken.add(cands[0].resource_id)
                    pending.remove(req)
                    changed = True
        for req in pending:
            cands = self._candidates(req, taken)
            if not cands:
                # Every row in the window went to another request; still show them as candidates.
                self._links[req.case_id] = WindowLink("", "", True, self._all_candidates(req))
                continue
            best = min(cands, key=lambda a: abs((req.reported_at - a.event_time).total_seconds()))
            self._take(req, best, ambiguous=len(cands) > 1)
            taken.add(best.resource_id)

    def _all_candidates(self, req: DeletionRequest, exclude: str = "") -> tuple[str, ...]:
        """Every status-consistent row in the window, whoever it was assigned to."""
        return tuple(
            a.resource_id for a in self._candidates(req, set()) if a.resource_id != exclude
        )

    def _take(self, req: DeletionRequest, row: Activity, ambiguous: bool) -> None:
        self._links[req.case_id] = WindowLink(
            row.resource_id,
            row.activity_id,
            ambiguous,
            self._all_candidates(req, exclude=row.resource_id),
        )

    def link(self, case_id: str) -> WindowLink | None:
        """The window link assigned to a case, or None."""
        return self._links.get(case_id)
