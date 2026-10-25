"""PLAN stage: the deterministic per-case portfolio.

For one request the portfolio is everything the agent is allowed to reason
about: the subject, every resource linked to them, each resource's latest
state at ``audit_at``, the policy treatment that applies, and the activity
timeline that explains how it got there. Nothing here calls a model.

Linking is resource-centric: once a resource is tied to the subject by any
link (identifier, related resource, activity window), *all* of its observations
up to ``audit_at`` are used, including the identifier-less ones (a 404, an
anonymized profile) that record the state after deletion.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ghost.data.ids import EMAIL_RE as _EMAIL_RE
from ghost.data.ids import RESOURCE_ID_RE as _RESOURCE_RE
from ghost.data.index import AbsenceProof, EvidenceIndex
from ghost.data.loaders import Activity, Dataset, DeletionRequest
from ghost.data.observations import Observation
from ghost.data.parsers.common import PREFIX_SYSTEM
from ghost.identity import IdentityResolver, Subject
from ghost.linking import WINDOW_SLACK, WindowLink, WindowLinker
from ghost.policy import Policy, Treatment, category_of

_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PHONE_RE = re.compile(r"^\+?[\d\s()-]{7,}$")
# State fields that hold the *lookup* used to reach a record, not data stored in it.
_LOOKUP_FIELDS = {"lookup_key", "query", "correlation_key", "subject_key", "match_key", "scan_key"}
# Fields that, when present (even as null), show the record exposes identifier columns.
_IDENTIFIER_FIELDS = {
    "email",
    "phone",
    "email_hash",
    "name",
    "full_name",
    "requester_email",
    "requester_phone",
    "requester_name",
    "customer_email",
    "owner_email",
    "owner_phone",
    "subject_selector",
    "uploaded_by",
    "matched_value",
    "message_text",
    "display_name",
}

# Normalised status vocabulary shared by the agent prompt, the baseline and the guards.
PRESENT, ABSENT, UNKNOWN = "PRESENT", "ABSENT", "UNKNOWN"
IDENTIFIABLE, ANONYMIZED, REDACTED = "IDENTIFIABLE", "ANONYMIZED", "REDACTED"
ACTIVE, DISABLED, RESTORABLE, BLOCKED = "ACTIVE", "DISABLED", "RESTORABLE", "BLOCKED"

_READBACK_STATUS = {
    "FOUND": PRESENT,
    "NOT_FOUND": ABSENT,
    "IDENTIFIABLE": IDENTIFIABLE,
    "ANONYMIZED": ANONYMIZED,
    "REDACTED": REDACTED,
    "ACTIVE": ACTIVE,
    "DISABLED": DISABLED,
    "BLOCKED": BLOCKED,
}
_TRUTHY = {"1", "true", "yes", 1}
_PII_CATEGORIES = {
    "ANALYTICS_PROFILE",
    "SUPPORT_MESSAGE",
    "ACCOUNT_TOMBSTONE",
    "EMAIL_DELIVERY_LOG",
}

# The deletion worker's attempted action is the policy engine's recorded treatment decision.
WORKFLOW_ACTION: dict[str, str] = {
    "DELETE_RECORD": "DELETE",
    "REDACT_RECORD": "REDACT",
    "ANONYMIZE_RECORD": "ANONYMIZE",
    "CHECK_DATA_FLOW": "DISABLE",
    "DISABLE_DATA_FLOW": "DISABLE",
    "CHECK_RESTORE_ACCESS": "BLOCK",
    "BLOCK_RESTORE": "BLOCK",
    "RETAIN_RECORD": "RETAIN",
}


@dataclass
class ActivityEvent:
    """One activity row on a record's timeline."""

    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    reported_result: str
    details: str


@dataclass
class ResourceView:
    """One linked record as the plan sees it: category, treatment, link kind, observations, absence
    proofs, timeline and derived status.
    """

    resource_id: str
    system: str
    category: str
    treatment: Treatment
    workflow_action: str | None
    link_kind: str  # identifier | related | window
    observations: list[Observation]
    absence_proofs: list[AbsenceProof]
    timeline: list[ActivityEvent]
    status: str
    direct_pii: list[str]
    recreated: bool
    ambiguous: bool = False
    alternatives: tuple[str, ...] = ()

    @property
    def latest(self) -> Observation | None:
        """The newest observation at or before audit_at."""
        return self.observations[0] if self.observations else None

    @property
    def proving_evidence(self) -> str:
        """Newest evidence id that shows the current status (observation or absence proof)."""
        if self.latest is not None:
            if self.absence_proofs and self.absence_proofs[0].captured_at > self.latest.captured_at:
                return self.absence_proofs[0].evidence_id
            return self.latest.evidence_id
        return self.absence_proofs[0].evidence_id if self.absence_proofs else ""


@dataclass
class Portfolio:
    """The plan for one case: subject, linked records, and what could not be linked."""

    request: DeletionRequest
    subject: Subject
    resources: list[ResourceView]
    notes: list[str] = field(default_factory=list)

    def by_id(self, resource_id: str) -> ResourceView | None:
        """A linked record by id, or None."""
        return next((r for r in self.resources if r.resource_id == resource_id), None)

    def intake_classification(self) -> ActivityEvent | None:
        """The privacy intake's own classification of this request, if it recorded one.

        ``CLASSIFY_REQUEST`` rows sit on the subject's account ("classified request for
        account_x as non-deletion; no destructive action was authorized"). They are a claim,
        but a language-independent one, so they cross-check the text rule.
        """
        for view in self.resources:
            if view.link_kind != "identifier":
                continue
            for event in view.timeline:
                if event.event_type == "CLASSIFY_REQUEST":
                    return event
        return None


def direct_pii(obs: Observation) -> list[str]:
    """Direct identifiers stored *in* the record (not the key used to look it up)."""
    found: list[str] = []
    for key, value in obs.state.items():
        if key in _LOOKUP_FIELDS or not isinstance(value, str) or value in ("", "null"):
            continue
        if (
            _EMAIL_RE.search(value)
            or _HASH_RE.match(value)
            or (value.startswith("+") and _PHONE_RE.match(value))
        ):
            found.append(f"{key}={value}")
    return found


def _flag(obs: Observation, key: str) -> bool | None:
    value = obs.state.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in _TRUTHY
    return value in _TRUTHY


def _has_identifier_fields(obs: Observation) -> bool:
    return any(key in _IDENTIFIER_FIELDS for key in obs.state)


def derive_status(category: str, obs: Observation | None, pii: list[str]) -> str:
    """Normalise the latest observation into the shared status vocabulary."""
    if obs is None:
        return UNKNOWN
    readback = obs.state.get("result")
    if isinstance(readback, str) and readback in _READBACK_STATUS:
        status = _READBACK_STATUS[readback]
        return RESTORABLE if (category == "BACKUP_ARCHIVE" and status == ACTIVE) else status
    if not obs.present:
        return ABSENT
    if category == "DATA_FLOW":
        enabled = _flag(obs, "enabled")
        if enabled is None:  # the record exists but the source shows no on/off state
            return UNKNOWN
        return ACTIVE if enabled else DISABLED
    if category == "BACKUP_ARCHIVE":
        decision = str(obs.state.get("decision") or "").lower()  # restore_authorization logs
        if _flag(obs, "deletion_block") or decision in ("deny", "denied", "blocked"):
            return BLOCKED
        if decision in ("allow", "allowed", "approve", "approved"):
            return RESTORABLE
        allowed = [
            f for f in (_flag(obs, "restore_allowed"), _flag(obs, "eligible")) if f is not None
        ]
        if not allowed:  # an archive that exists without any block can be restored
            return RESTORABLE
        return RESTORABLE if any(allowed) else BLOCKED
    if category in _PII_CATEGORIES:
        if pii:
            return IDENTIFIABLE
        if not _has_identifier_fields(obs):
            return PRESENT  # the record exists; the observation shows no identifier columns
        return ANONYMIZED if category == "ANALYTICS_PROFILE" else REDACTED
    return PRESENT


class PortfolioBuilder:
    """Builds portfolios for a request set; window links are solved once for the whole set."""

    def __init__(
        self,
        dataset: Dataset,
        index: EvidenceIndex,
        resolver: IdentityResolver,
        requests: list[DeletionRequest],
    ) -> None:
        self._index = index
        self._resolver = resolver
        self._policy = Policy(dataset.rules)
        # Rows about a resource: keyed by resource_id, plus rows whose details mention it
        # (a sync row is keyed by the connector but names the search entry it repopulated).
        self._activity_by_resource: dict[str, list[Activity]] = {}
        for row in dataset.activity:
            mentioned = set(_RESOURCE_RE.findall(row.details)) | {row.resource_id}
            for rid in mentioned:
                self._activity_by_resource.setdefault(rid, []).append(row)
        self._linkers: dict[str, WindowLinker] = {
            "account": WindowLinker(
                requests, dataset.activity, "account", self._identifier_linked("account")
            ),
            "analytics": WindowLinker(
                requests,
                dataset.activity,
                "analytics",
                self._identifier_linked("analytics"),
                event_types=frozenset({"ANONYMIZE_RECORD", "DELETE_RECORD"}),
                use_status=False,
            ),
        }

    def _identifier_linked(self, prefix: str) -> Callable[[DeletionRequest], str | None]:
        def resolve(req: DeletionRequest) -> str | None:
            subject = self._resolver.resolve(req.email)
            if subject is None:
                return None
            linked = self._index.find_by_identifiers(subject.identifiers)
            resources = {o.resource_id for o in linked} | {
                rid for o in linked for rid in o.related_resources
            }
            matches = {rid for rid in resources if rid.startswith(prefix + "_")}
            return next(iter(matches)) if len(matches) == 1 else None

        return resolve

    def build(self, req: DeletionRequest) -> Portfolio | None:
        """The portfolio for one request, or None when its email resolves to no identity."""
        subject = self._resolver.resolve(req.email)
        if subject is None:
            return None
        notes: list[str] = []
        # Membership may be learned from any file (a later snapshot can reveal that a retained
        # record belongs to the subject); state and citations are still cut at audit_at.
        linked = self._index.find_by_identifiers(subject.identifiers)
        link_kind: dict[str, str] = {}
        for obs in linked:
            if _is_resource(obs.resource_id):
                link_kind.setdefault(obs.resource_id, "identifier")
        for obs in linked:
            for rid in obs.related_resources:
                if _is_resource(rid):
                    link_kind.setdefault(rid, "related")
        self._dedupe_accounts(req, link_kind, notes)
        window_links = self._window_links(req, subject, link_kind, notes)
        resources = [
            self._view(req, subject, rid, kind, window_links.get(rid))
            for rid, kind in link_kind.items()
        ]
        resources.sort(key=lambda r: (r.system, r.resource_id))
        return Portfolio(request=req, subject=subject, resources=resources, notes=notes)

    def _window_links(
        self, req: DeletionRequest, subject: Subject, link_kind: dict[str, str], notes: list[str]
    ) -> dict[str, WindowLink]:
        """Link identifier-less records (deleted accounts, anonymized analytics) by the
        deletion workflow's activity window; alternatives travel along as candidates."""
        window_links: dict[str, WindowLink] = {}
        for prefix, linker in self._linkers.items():
            if any(rid.startswith(prefix + "_") for rid in link_kind):
                continue
            link = linker.link(req.case_id)
            if link is None:
                notes.append(f"no {prefix} record could be linked by identifier or activity window")
                continue
            if link.resource_id and (
                prefix == "account" or self._index.history(link.resource_id, before=req.audit_at)
            ):
                link_kind[link.resource_id] = "window"
                window_links[link.resource_id] = link
                if link.ambiguous:
                    notes.append(
                        f"{link.resource_id} chosen by activity window ({link.activity_id}); "
                        f"alternatives: {', '.join(link.alternatives)}"
                    )
            elif link.resource_id:
                notes.append(
                    f"{link.resource_id} is in the activity window but has no evidence by audit_at"
                )
            # The alternatives travel with the portfolio as candidates: the agent, not the
            # linker, makes the final call when two requests share one deletion batch.
            for alt in link.alternatives:
                if alt not in link_kind and self._has_evidence(alt, req.audit_at, subject):
                    link_kind[alt] = "window-alternative"
                    window_links[alt] = link
        return window_links

    def _has_evidence(self, rid: str, before: datetime, subject: Subject) -> bool:
        """An observation or an absence proof by the cutoff: something the agent can cite."""
        if self._index.history(rid, before=before):
            return True
        return bool(
            self._index.absence_proofs(
                rid, _system_from_prefix(rid), before=before, subject=subject.identifiers
            )
        )

    def _dedupe_accounts(
        self, req: DeletionRequest, link_kind: dict[str, str], notes: list[str]
    ) -> None:
        """One subject has one account; an old 404 under a former email is not it."""
        accounts = [rid for rid in link_kind if rid.startswith("account_")]
        if len(accounts) < 2:
            return
        in_window = [
            rid
            for rid in accounts
            if any(
                a.source_system == "deletion_worker"
                and req.requested_at <= a.event_time <= req.reported_at + WINDOW_SLACK
                for a in self._activity_by_resource.get(rid, [])
            )
        ]
        keep = (
            in_window
            if len(in_window) == 1
            else [
                max(
                    accounts,
                    key=lambda rid: (
                        self._index.history(rid, before=req.audit_at)[0].captured_at
                        if self._index.history(rid, before=req.audit_at)
                        else req.requested_at
                    ),
                )
            ]
        )
        for rid in accounts:
            if rid not in keep:
                link_kind.pop(rid)
                notes.append(f"{rid} also matched the subject but is not the account under audit")

    def _view(
        self,
        req: DeletionRequest,
        subject: Subject,
        rid: str,
        kind: str,
        link: WindowLink | None,
    ) -> ResourceView:
        observations = self._index.history(rid, before=req.audit_at)  # full history, newest first
        category = category_of(rid, observations)
        system = observations[0].system if observations else _system_from_prefix(rid)
        # The newest evidence at or before audit_at decides the state, even when it predates
        # the request: a record seen in June and again in September existed in July too, and
        # the sample key never cites a file captured after audit_at but does rely on the
        # latest snapshot available. (A request-time cutoff was measured and rejected.)
        proofs = self._index.absence_proofs(
            rid, system, before=req.audit_at, subject=subject.identifiers
        )
        latest = observations[0] if observations else None
        absent_by_table = bool(proofs) and (
            latest is None or proofs[0].captured_at > latest.captured_at
        )
        pii = direct_pii(latest) if latest else []
        status = ABSENT if absent_by_table else derive_status(category, latest, pii)
        timeline = self._timeline(rid, req.audit_at)
        workflow_action = _workflow_action(timeline)
        treatment = self._policy.treatment(category)
        if workflow_action and workflow_action != treatment.action:
            treatment = Treatment(
                category,
                workflow_action,
                treatment.rule_id,
                f"{treatment.condition} [workflow attempted {workflow_action}]",
            )
        return ResourceView(
            resource_id=rid,
            system=system,
            category=category,
            treatment=treatment,
            workflow_action=workflow_action,
            link_kind=kind,
            observations=observations,
            absence_proofs=proofs,
            timeline=timeline,
            status=status,
            direct_pii=pii,
            recreated=_recreated(timeline, latest),
            ambiguous=bool(link and link.ambiguous),
            alternatives=link.alternatives if link else (),
        )

    def _timeline(self, rid: str, before: datetime) -> list[ActivityEvent]:
        return sorted(
            (
                ActivityEvent(
                    a.activity_id,
                    a.event_time,
                    a.event_type,
                    a.source_system,
                    a.reported_result,
                    a.details,
                )
                for a in self._activity_by_resource.get(rid, [])
                if a.event_time <= before
            ),
            key=lambda e: e.event_time,
        )


def _workflow_action(timeline: list[ActivityEvent]) -> str | None:
    """Treatment the deletion worker attempted for this resource, if any."""
    actions = [
        WORKFLOW_ACTION[e.event_type]
        for e in timeline
        if e.source_system == "deletion_worker" and e.event_type in WORKFLOW_ACTION
    ]
    return actions[-1] if actions else None


def _is_resource(resource_id: str) -> bool:
    """Real system records only: document-level PDF facts and snapshot markers are context."""
    return bool(resource_id) and not resource_id.startswith("document:")


def _system_from_prefix(resource_id: str) -> str:
    return PREFIX_SYSTEM.get(resource_id.rsplit("_", 1)[0], "unknown")


def _recreated(timeline: list[ActivityEvent], latest: Observation | None) -> bool:
    """A present record observed after a successful deletion, or a sync/restore repopulation."""
    if latest is None or not latest.present:
        return False
    deleted_before = any(
        e.event_type == "DELETE_RECORD"
        and e.reported_result == "SUCCESS"
        and e.event_time < latest.captured_at
        and latest.resource_id in e.details
        for e in timeline
    )
    repopulated = any(
        "repopulated" in e.details and latest.resource_id in e.details for e in timeline
    )
    return deleted_before or repopulated
