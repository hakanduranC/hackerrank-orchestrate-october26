"""Typed outcomes shared by the baseline, the agent and the guards.

A verdict says, per resource, whether it belongs to the subject, what state it
is in at ``audit_at``, what treatment the policy requires, and which evidence
proves the state. The composer turns verdicts into ordered steps; nothing
downstream reads free text.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """Whether the request authorizes deletion."""

    DELETION = "DELETION"
    NOT_DELETION = "NOT_DELETION"


class Membership(StrEnum):
    """Does a record belong to the subject: yes, no, or uncertain (a batch candidate nothing
    separates).
    """

    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class Status(StrEnum):
    """State of a record at audit_at, from evidence only; UNKNOWN when no file shows it."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    IDENTIFIABLE = "IDENTIFIABLE"
    ANONYMIZED = "ANONYMIZED"
    REDACTED = "REDACTED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    RESTORABLE = "RESTORABLE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class Treatment(StrEnum):
    """What the policy requires for a record: DELETE, REDACT, ANONYMIZE, RETAIN, DISABLE (data flow)
    or BLOCK (restore).
    """

    DELETE = "DELETE"
    REDACT = "REDACT"
    ANONYMIZE = "ANONYMIZE"
    RETAIN = "RETAIN"
    DISABLE = "DISABLE"
    BLOCK = "BLOCK"


class ResourceVerdict(BaseModel):
    """The agent's claim about one record: membership, state, treatment and the file that proves the
    state.
    """

    system_id: str = Field(description="Owning system, e.g. search_service")
    resource_id: str = Field(description="Resource id exactly as it appears in the evidence")
    is_subject: Membership = Field(description="Does this record belong to the deletion subject?")
    status: Status = Field(description="State of the record at audit_at, from evidence only")
    treatment: Treatment = Field(description="Treatment the policy requires for this record")
    evidence_id: str = Field(
        default="",
        description=(
            "file_id (evidence_NN) of the file that shows the status, captured at or before"
            " audit_at; activity / audit rows are claims and are not accepted; empty with"
            " status UNKNOWN when no file shows it"
        ),
    )
    reason: str = Field(default="", description="One sentence; what in the evidence decided this")


class CaseVerdict(BaseModel):
    """The typed outcome of one case: intent and one ResourceVerdict per record considered."""

    case_id: str
    intent: Intent
    reason: str = ""
    resources: list[ResourceVerdict] = Field(default_factory=list)


class Step(BaseModel):
    """One row of ``steps_json``; field order matches the contract."""

    step_no: int
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str


class CaseResult(BaseModel):
    """One output row: case id, deletion status and the ordered steps."""

    case_id: str
    deletion_status: str
    steps: list[Step]
