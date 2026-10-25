"""COMPOSE: a case verdict -> ``deletion_status`` + ordered, numbered steps.

Rules (all derived from the problem statement and confirmed on the 32 samples):

* ``NOT_DELETION`` -> ``NOT_APPLICABLE`` with ``[]``.
* Each subject resource is compared with its required treatment. A violation
  yields FINDING + REMEDIATION + VERIFICATION; a compliant record yields the
  VERIFICATION that proves its state.
* A case is INCOMPLETE when it has at least one finding, else COMPLETE.
* COMPLETE cases keep verification steps only, each citing the evidence that
  proves the state. INCOMPLETE cases cite evidence on findings only; remediation
  and verification describe future work and carry an empty ``evidence_id``.
* Order: phase, then the operation order of the statement's table, then
  ``system_id``, then ``resource_id``. Steps are numbered from 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghost.agent.schemas import (
    CaseResult,
    CaseVerdict,
    Intent,
    Membership,
    ResourceVerdict,
    Status,
    Step,
    Treatment,
)

FINDING, REMEDIATION, VERIFICATION = "FINDING", "REMEDIATION", "VERIFICATION"
PHASE_ORDER = {FINDING: 0, REMEDIATION: 1, VERIFICATION: 2}
OPERATION_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}
OPERATION_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}

# Record exists in some form (anything but ABSENT / UNKNOWN).
_EXISTS = {Status.PRESENT, Status.IDENTIFIABLE, Status.ANONYMIZED, Status.REDACTED}


@dataclass(frozen=True)
class Plan:
    """What one resource contributes: an optional finding, remediation and verification op."""

    finding: str | None
    remediation: str | None
    verification: str | None


def plan_for(verdict: ResourceVerdict) -> Plan:
    """Map (treatment, status) to the operations the contract allows."""
    t, s = verdict.treatment, verdict.status
    if t == Treatment.DELETE:
        if s in _EXISTS:
            return Plan("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT")
        return Plan(None, None, "VERIFY_RECORD_ABSENT")
    if t == Treatment.REDACT:
        if s in (Status.IDENTIFIABLE, Status.PRESENT):
            return Plan("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED")
        if s == Status.ABSENT:
            return Plan(None, None, "VERIFY_RECORD_ABSENT")
        return Plan(None, None, "VERIFY_RECORD_REDACTED")
    if t == Treatment.ANONYMIZE:
        if s in (Status.IDENTIFIABLE, Status.PRESENT):
            return Plan("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED")
        if s == Status.ABSENT:
            return Plan(None, None, "VERIFY_RECORD_ABSENT")
        return Plan(None, None, "VERIFY_RECORD_ANONYMIZED")
    if t == Treatment.RETAIN:
        # A retained record proven absent is a policy violation the contract cannot express:
        # no finding operation covers "should exist but does not". It yields no step; the
        # status of the case is then decided by its other records. Never observed on this
        # dataset; recorded as a known limit in the evaluation report.
        return Plan(None, None, "VERIFY_RECORD_PRESENT" if s != Status.ABSENT else None)
    if t == Treatment.DISABLE:
        if s == Status.ACTIVE:
            return Plan("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED")
        return Plan(None, None, "VERIFY_DATA_FLOW_DISABLED")
    if t == Treatment.BLOCK:
        if s == Status.RESTORABLE:
            return Plan("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED")
        return Plan(None, None, "VERIFY_RESTORE_BLOCKED")
    return Plan(None, None, None)


def compose(verdict: CaseVerdict, include_uncertain: bool = False) -> CaseResult:
    """Turn a case verdict into the graded output row."""
    if verdict.intent == Intent.NOT_DELETION:
        return CaseResult(case_id=verdict.case_id, deletion_status="NOT_APPLICABLE", steps=[])
    accepted = {Membership.YES} | ({Membership.UNCERTAIN} if include_uncertain else set())
    subject = [r for r in verdict.resources if r.is_subject in accepted]
    plans = [(r, plan_for(r)) for r in subject]
    incomplete = any(p.finding for _, p in plans)
    raw: list[tuple[str, str, str, str, str]] = []  # phase, op, system, resource, evidence
    for r, p in plans:
        if p.finding:
            raw.append((FINDING, p.finding, r.system_id, r.resource_id, r.evidence_id))
        if p.remediation:
            raw.append((REMEDIATION, p.remediation, r.system_id, r.resource_id, ""))
        if p.verification and (incomplete or r.evidence_id or r.status != Status.UNKNOWN):
            evidence = "" if incomplete else r.evidence_id
            if not incomplete and not evidence:
                continue  # a COMPLETE case may only assert what evidence proves
            raw.append((VERIFICATION, p.verification, r.system_id, r.resource_id, evidence))
    ordered = sorted(
        set(raw),
        key=lambda s: (PHASE_ORDER[s[0]], OPERATION_ORDER[s[1]], s[2], s[3]),
    )
    steps = [
        Step(
            step_no=i,
            phase=phase,
            system_id=system,
            resource_id=resource,
            operation=op,
            state=OPERATION_STATE[op],
            evidence_id=evidence,
        )
        for i, (phase, op, system, resource, evidence) in enumerate(ordered, start=1)
    ]
    status = "INCOMPLETE" if incomplete else "COMPLETE"
    return CaseResult(case_id=verdict.case_id, deletion_status=status, steps=steps)
