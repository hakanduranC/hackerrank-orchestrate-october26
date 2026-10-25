"""VERIFY stage: deterministic checks on a verdict before it is accepted.

A verdict passes only if every claim in it is anchored in what the tools showed:
the resource was in the portfolio or surfaced by a tool, the evidence is a file
that exists, was captured by audit_at and mentions the resource (or proves its
absence), a record the agent found itself is tied to the subject by an identifier
in that file, and the treatment is one the policy allows for that kind of record.
"""

from __future__ import annotations

from ghost.agent.schemas import CaseVerdict, Intent, Membership, Status, Treatment
from ghost.agent.tools import CaseContext
from ghost.policy import RETAINED_CATEGORIES, category_of


def check_verdict(verdict: CaseVerdict, ctx: CaseContext) -> list[str]:
    """Every reason a verdict cannot be accepted, as messages the model can act on; empty means
    accepted.
    """
    errors: list[str] = []
    if verdict.intent == Intent.NOT_DELETION:
        if verdict.resources:
            errors.append("NOT_DELETION verdicts must have an empty resources list")
        return errors
    seen_ids: set[str] = set()
    for r in verdict.resources:
        if r.resource_id in seen_ids:
            errors.append(f"{r.resource_id}: listed twice")
        seen_ids.add(r.resource_id)
        errors.extend(
            _check_resource(
                r.resource_id, r.system_id, r.status, r.treatment, r.is_subject, r.evidence_id, ctx
            )
        )
    return errors


def _check_resource(
    rid: str,
    system: str,
    status: Status,
    treatment: Treatment,
    member: Membership,
    evidence_id: str,
    ctx: CaseContext,
) -> list[str]:
    errors: list[str] = []
    in_portfolio = ctx.portfolio.by_id(rid) is not None
    if not in_portfolio and rid not in ctx.seen_resources:
        errors.append(
            f"{rid}: never returned by any tool; only records the tools showed may be asserted"
        )
        return errors
    view = ctx.portfolio.by_id(rid)
    category = (
        view.category if view else category_of(rid, ctx.index.history(rid, before=ctx.audit_at))
    )
    if category in RETAINED_CATEGORIES and treatment == Treatment.DELETE:
        errors.append(f"{rid}: {category} is a retained category; DELETE is not allowed")
    if status not in ALLOWED_STATES[treatment]:
        allowed = ", ".join(sorted(s.value for s in ALLOWED_STATES[treatment]))
        errors.append(
            f"{rid}: status {status.value} does not describe a {treatment.value} record;"
            f" use one of {allowed}"
        )
    if view is not None and view.system != system:
        errors.append(f"{rid}: system_id should be {view.system}")
    if member == Membership.YES and status != Status.UNKNOWN and not evidence_id:
        if view is None or not (view.proving_evidence or view.absence_proofs):
            errors.append(f"{rid}: a status of {status} needs an evidence_id that proves it")
    if evidence_id:
        errors.extend(_check_evidence(rid, evidence_id, ctx))
    return errors


def _check_evidence(rid: str, evidence_id: str, ctx: CaseContext) -> list[str]:
    if evidence_id.startswith("evidence_"):
        entry = ctx.index.file_entry(evidence_id)
        if entry is None:
            return [f"{rid}: {evidence_id} is not in the evidence manifest"]
        if entry.captured_at > ctx.audit_at:
            return [f"{rid}: {evidence_id} was captured after audit_at and cannot be cited"]
        if evidence_id not in ctx.seen_evidence and evidence_id not in _plan_evidence(rid, ctx):
            return [
                f"{rid}: {evidence_id} was not shown by any tool in this case; cite only files"
                " you saw in a tool result"
            ]
        if not _file_mentions_resource(rid, evidence_id, ctx):
            return [f"{rid}: {evidence_id} does not mention {rid} nor prove its absence"]
        if ctx.portfolio.by_id(rid) is None and not _source_names_subject(rid, evidence_id, ctx):
            return [
                f"{rid}: {evidence_id} ties {rid} to no identifier of the subject (email, phone,"
                " hash or user_id); a record you found yourself must be linked by an identifier"
            ]
        return []
    if evidence_id.startswith(("activity_", "audit_")):
        return [
            f"{rid}: {evidence_id} is a workflow / audit-log row, which is a claim, not state;"
            " cite the evidence file that shows the record's state, or leave evidence_id empty"
            " and set status UNKNOWN"
        ]
    return [f"{rid}: {evidence_id} is not a file_id"]


def _plan_evidence(rid: str, ctx: CaseContext) -> set[str]:
    """Files the plan itself holds for a record (its observations and absence proofs)."""
    view = ctx.portfolio.by_id(rid)
    if view is None:
        return set()
    return {o.evidence_id for o in view.observations} | {p.evidence_id for p in view.absence_proofs}


# Which states can describe a record under each treatment; anything else is a category error
# (a profile is not ACTIVE, a connector is not ANONYMIZED).
_RECORD_STATES = {
    Status.PRESENT,
    Status.ABSENT,
    Status.IDENTIFIABLE,
    Status.ANONYMIZED,
    Status.REDACTED,
    Status.UNKNOWN,
}
ALLOWED_STATES: dict[Treatment, frozenset[Status]] = {
    Treatment.DELETE: frozenset(_RECORD_STATES),
    Treatment.REDACT: frozenset(_RECORD_STATES),
    Treatment.ANONYMIZE: frozenset(_RECORD_STATES),
    Treatment.RETAIN: frozenset(_RECORD_STATES),
    Treatment.DISABLE: frozenset({Status.ACTIVE, Status.DISABLED, Status.UNKNOWN}),
    Treatment.BLOCK: frozenset({Status.RESTORABLE, Status.BLOCKED, Status.UNKNOWN}),
}


def _source_names_subject(rid: str, evidence_id: str, ctx: CaseContext) -> bool:
    """The cited source links the record to the subject: the record's own observation in that
    file carries a subject identifier, or the file names one of the subject's identifiers."""
    subject = ctx.portfolio.subject.identifiers
    if any(
        o.evidence_id == evidence_id and o.identifiers.intersects(subject)
        for o in ctx.index.history(rid, before=ctx.audit_at)
    ):
        return True
    if ctx.index.file_mentions_subject(evidence_id, subject):
        return True
    terms = {ctx.portfolio.subject.user_id, *subject.emails, *subject.phones, *subject.email_hashes}
    return any(term and ctx.index.file_mentions_text(evidence_id, term) for term in terms)


def _file_mentions_resource(rid: str, evidence_id: str, ctx: CaseContext) -> bool:
    if any(o.evidence_id == evidence_id for o in ctx.index.history(rid)):
        return True
    view = ctx.portfolio.by_id(rid)
    if view and any(p.evidence_id == evidence_id for p in view.absence_proofs):
        return True
    return ctx.index.file_mentions_text(evidence_id, rid)


# Refusal kinds, for measurement: what the guards catch, counted per kind in the usage report.
_REFUSAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("is not in the evidence manifest", "hallucinated_file"),
    ("captured after audit_at", "post_audit_evidence"),
    ("claim, not state", "activity_as_state"),
    ("was not shown by any tool in this case", "unseen_file"),
    ("does not describe a", "status_treatment_mismatch"),
    ("never returned by any tool", "unseen_resource"),
    ("ties", "no_subject_identifier"),
    ("does not mention", "file_lacks_record"),
    ("needs an evidence_id", "uncited_status"),
    ("retained category", "delete_on_retained"),
    ("listed twice", "duplicate_resource"),
    ("system_id should be", "wrong_system"),
    ("NOT_DELETION verdicts", "not_deletion_with_resources"),
    ("is not a file_id", "bad_evidence_id"),
)


def refusal_kind(error: str) -> str:
    """Map a guard message to its kind (stable names for traces and reports)."""
    for needle, kind in _REFUSAL_PATTERNS:
        if needle in error:
            return kind
    return "other"
