"""The agent's tools. All of them are read-only views over the index and the portfolio.

Every tool is bound to one case through :class:`CaseContext`, so a tool can never
reach data of another case, and every tool result is data: the model reads it,
the guards later check that what the model asserts was actually returned here.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ghost.agent.schemas import CaseVerdict, Intent, ResourceVerdict
from ghost.context import page, topics
from ghost.data.ids import RESOURCE_ID_RE as _RESOURCE_RE
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import DeletionRule
from ghost.data.observations import Observation
from ghost.portfolio import Portfolio, ResourceView

_EVIDENCE_RE = re.compile(r"\b(?:evidence|activity|audit)_[A-Za-z0-9]+\b")


@dataclass
class CaseContext:
    """Per-case state shared by the tools, the graph and the guards."""

    portfolio: Portfolio
    index: EvidenceIndex
    rules: list[DeletionRule]
    audit_at: datetime
    verdict: CaseVerdict | None = None
    accepted: bool = False
    read_window: int = 25
    seen_resources: set[str] = field(default_factory=set)
    seen_evidence: set[str] = field(default_factory=set)
    tool_calls: list[dict[str, str]] = field(default_factory=list)

    def note(self, name: str, args: str, output: str, harvest: str | None = None) -> str:
        """Record a call and what it *exposed*, then return the output unchanged.

        Ids are harvested from ``harvest`` (the result body) rather than the whole output, so
        a header that echoes the model's own search term never whitelists that term: a
        zero-hit search for ``account_999`` must not make ``account_999`` citable.
        """
        self.tool_calls.append({"tool": name, "args": args[:200], "chars": str(len(output))})
        source = output if harvest is None else harvest
        self.seen_resources.update(_RESOURCE_RE.findall(source))
        self.seen_evidence.update(_EVIDENCE_RE.findall(source))
        return output

    def fail(self, name: str, args: str, exc: Exception) -> str:
        """Record a tool failure; the model sees it as data and can try something else."""
        message = f"tool error: {type(exc).__name__}: {exc}"[:300]
        self.tool_calls.append(
            {"tool": name, "args": args[:200], "chars": "ERROR", "error": message}
        )
        return message


def _view_dict(v: ResourceView) -> dict[str, object]:
    latest = v.latest
    return {
        "system": v.system,
        "resource_id": v.resource_id,
        "category": v.category,
        "status_at_audit": v.status,
        "required_treatment": v.treatment.action,
        "treatment_basis": v.treatment.condition,
        "evidence_id": v.proving_evidence,
        "link": v.link_kind,
        "ambiguous": v.ambiguous,
        "alternatives": list(v.alternatives),
        "direct_pii": v.direct_pii,
        "recreated_after_deletion": v.recreated,
        "latest_observation": (
            {
                "evidence_id": latest.evidence_id,
                "captured_at": latest.captured_at.isoformat(),
                "location": latest.location,
                "excerpt": latest.excerpt[:200],
            }
            if latest
            else None
        ),
        "absence_proofs": [f"{p.evidence_id}@{p.captured_at.date()}" for p in v.absence_proofs[:3]],
        "activity": [
            f"{e.activity_id} {e.event_time.isoformat()} {e.event_type} {e.reported_result}"
            for e in v.timeline[-6:]
        ],
    }


class FinalAnswer(BaseModel):
    """Arguments of ``final_answer``: the typed case verdict."""

    intent: Intent = Field(
        description=(
            "DELETION if the text asks for the account / personal data to be removed; "
            "NOT_DELETION for access, copy / export, correction, login help, marketing opt-out, "
            "an explicit 'do not delete', or an unrelated request"
        )
    )
    reason: str = Field(description="One or two sentences on the deciding evidence")
    resources: list[ResourceVerdict] = Field(
        default_factory=list, description="One entry per record considered, candidates included"
    )


def _guarded(ctx: CaseContext, fn: Callable[..., str]) -> Callable[..., str]:
    """Tool failures become data the model can read instead of exceptions that end the case."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> str:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as data
            return ctx.fail(fn.__name__, json.dumps(kwargs, default=str), exc)

    return wrapper


def get_portfolio_tool(ctx: CaseContext) -> BaseTool:
    """Tool: the deterministic plan for the case (the draft verdict the model confirms, corrects or
    extends).
    """

    def get_portfolio() -> str:
        """Input: none. Returns JSON {subject, notes, resources[]}: every record the deterministic
        plan linked to this subject, each with status_at_audit, required_treatment, the evidence_id
        proving the status, how it was linked (link) and its last activity rows. Field meanings
        are in the system prompt section 'Reading tool results'. Use it first; it is the draft
        verdict you confirm, correct or extend. Next: check notes and every resource with
        link=window-alternative, ambiguous=true, status UNKNOWN or an empty evidence_id."""
        p = ctx.portfolio
        payload = {
            "format": "portfolio; see 'Reading tool results' for field meanings",
            "resource_count": len(p.resources),
            "subject": {
                "user_id": p.subject.user_id,
                "current_email": p.subject.current_email,
                "all_emails": sorted(p.subject.identifiers.emails),
                "phones": sorted(p.subject.identifiers.phones),
                "email_hashes": sorted(p.subject.identifiers.email_hashes),
                "resolved_via": p.subject.resolved_via,
            },
            "notes": p.notes,
            "resources": [_view_dict(v) for v in p.resources],
        }
        return ctx.note("get_portfolio", "", json.dumps(payload, ensure_ascii=False))

    return StructuredTool.from_function(_guarded(ctx, get_portfolio))


def _observation_dict(o: Observation) -> dict[str, object]:
    return {
        "evidence_id": o.evidence_id,
        "captured_at": o.captured_at.isoformat(),
        "system": o.system,
        "present": o.present,
        "location": o.location,
        "state": o.state,
        "identifiers": {
            k: sorted(v) for k, v in o.identifiers.model_dump(exclude_defaults=True).items()
        },
    }


def get_resource_history_tool(ctx: CaseContext) -> BaseTool:
    """Tool: every structured observation and activity row of one record up to audit_at."""

    def get_resource_history(resource_id: str) -> str:
        """Input: a resource id such as account_123. Returns JSON {observations[], activity[]}:
        every structured observation of that record in files captured by audit_at, newest first
        (evidence_id, captured_at, present, state fields, identifiers it still holds), then its
        activity rows (activity_id time event result: details). Use it when the portfolio status
        or evidence_id looks doubtful, or for a record search_evidence surfaced that the portfolio
        lacks. Next: the newest observation gives the status; its evidence_id is the citation."""
        obs = ctx.index.history(resource_id, before=ctx.audit_at)
        payload: dict[str, object] = {
            "format": "observations newest first; present=false means the file proves absence",
            "resource_id": resource_id,
            "observation_count": len(obs),
            "observations": [_observation_dict(o) for o in obs[:12]],
        }
        view = ctx.portfolio.by_id(resource_id)
        if view is not None:
            payload["activity"] = [
                f"{e.activity_id} {e.event_time.isoformat()} {e.event_type}"
                f" {e.reported_result}: {e.details}"
                for e in view.timeline
            ]
        shown = json.dumps(
            {"observations": payload["observations"], "activity": payload.get("activity", [])},
            ensure_ascii=False,
        )
        return ctx.note(
            "get_resource_history", resource_id, json.dumps(payload, ensure_ascii=False), shown
        )

    return StructuredTool.from_function(_guarded(ctx, get_resource_history))


def get_policy_rules_tool(ctx: CaseContext) -> BaseTool:
    """Tool: the deletion policy table."""

    def get_policy_rules() -> str:
        """Input: none. Returns JSON rows {rule_id, category, condition, action} from
        deletion_rules.csv. Use it only when a record's category or treatment is unclear; the
        same table is already in the system prompt."""
        rows = [
            {
                "rule_id": r.rule_id,
                "category": r.data_category,
                "condition": r.condition,
                "action": r.required_action,
            }
            for r in ctx.rules
        ]
        return ctx.note("get_policy_rules", "", json.dumps(rows, ensure_ascii=False), "")

    return StructuredTool.from_function(_guarded(ctx, get_policy_rules))


def list_evidence_tool(ctx: CaseContext) -> BaseTool:
    """Tool: evidence files captured by audit_at, optionally filtered by origin or path."""

    def list_evidence(origin_contains: str = "") -> str:
        """Input: optional substring of a file's origin or path ('backup', 'support', 'replica',
        'search'); empty lists all. Returns one line per evidence file captured at or before
        audit_at: evidence_id captured_at origin path, newest first. Files captured later are
        never listed and can never be cited. Use it to see which systems have a snapshot in the
        time window at all. Next: search_evidence for identifiers, or read_evidence on a file."""
        files = ctx.index.list_files(origin_contains or None, before=ctx.audit_at)
        rows = [
            f"{f.evidence_id} {f.captured_at.isoformat()} {f.origin} {f.file_path}" for f in files
        ]
        header = (
            f"{len(rows)} files captured <= audit_at; format: evidence_id captured_at origin path"
        )
        body = "\n".join(rows) or "none: no file matching that filter was captured by audit_at"
        return ctx.note("list_evidence", origin_contains, f"{header}\n{body}", body)

    return StructuredTool.from_function(_guarded(ctx, list_evidence))


def search_evidence_tool(ctx: CaseContext) -> BaseTool:
    """Tool: exact keyword search over the raw lines of every in-window file."""

    def search_evidence(term: str) -> str:
        """Input: one exact term - an email, phone, email hash, user_id, resource id, name or
        word (no wildcards; search variants separately). Returns up to 15 raw lines containing
        it from files captured at or before audit_at, newest file first, as
        evidence_id:line_no (file_captured_at) line. It reads raw text, so it finds records the
        structured index missed. 'no match' means the term is absent from every file in the
        window - that is evidence of absence only for a full snapshot, not for a log. Next:
        read_evidence(evidence_id, line_no) on a hit to see its surrounding record."""
        hits = ctx.index.search_raw(term, before=ctx.audit_at, limit=15)
        rows = [
            f"{h.evidence_id}:{h.line_no} ({h.file_captured_at.date()}) {h.text[:220]}"
            for h in hits
        ]
        header = (
            f"{len(rows)} hit(s) for {term!r} in files captured <= audit_at"
            f"{' (showing first 15)' if len(rows) == 15 else ''};"
            " format: evidence_id:line_no (file_captured_at) line"
        )
        body = "\n".join(rows) or "no match: the term appears in no file captured by audit_at"
        return ctx.note("search_evidence", term, f"{header}\n{body}", "\n".join(rows))

    return StructuredTool.from_function(_guarded(ctx, search_evidence))


def read_evidence_tool(ctx: CaseContext) -> BaseTool:
    """Tool: raw lines of one in-window file around a line number."""

    def read_evidence(evidence_id: str, line_no: int = 1, around: int = 10) -> str:
        """Input: an evidence_id, a line number (from search_evidence) and how many lines of
        context on each side (capped). Returns the file path, its captured_at, then
        'line_no: text' lines. Use it to read the record around a hit: which table or
        collection it sits in, which fields are filled, whether it is a 404 / NOT_FOUND readback.
        A file captured after audit_at is refused. Next: cite this evidence_id for the status
        you read here."""
        entry = ctx.index.file_entry(evidence_id)
        if entry is None:
            return ctx.note("read_evidence", evidence_id, "unknown evidence id", "")
        if entry.captured_at > ctx.audit_at:
            return ctx.note(
                "read_evidence", evidence_id, "refused: file captured after audit_at", ""
            )
        lines = ctx.index.read_raw(evidence_id, line_no, around=min(around, ctx.read_window))
        body = "\n".join(f"{n}: {t[:300]}" for n, t in lines) or "no text at that position"
        header = (
            f"{entry.file_path} captured_at={entry.captured_at.isoformat()} ({len(lines)} lines)"
        )
        return ctx.note(
            "read_evidence",
            f"{evidence_id}:{line_no}",
            f"{header}\n{body}",
            f"{evidence_id}\n{body}",
        )

    return StructuredTool.from_function(_guarded(ctx, read_evidence))


def final_answer_tool(ctx: CaseContext) -> BaseTool:
    """Tool: submit the typed verdict; the only way a case ends."""

    def final_answer(intent: Intent, reason: str, resources: list[ResourceVerdict]) -> str:
        """Submit the verdict; the case ends only through this tool. Input: intent, a short
        reason, and one entry per record considered (portfolio records, candidates and records
        you found yourself) with system_id, resource_id, is_subject yes/no/uncertain, status at
        audit_at, required treatment and the evidence_id (a file_id) proving the status.
        Records with is_subject=no are dropped by code; uncertain ones are not emitted. For
        NOT_DELETION send resources=[]. A validator checks every claim against what the tools
        returned and replies with what to fix."""
        ctx.verdict = CaseVerdict(
            case_id=ctx.portfolio.request.case_id, intent=intent, reason=reason, resources=resources
        )
        return ctx.note("final_answer", intent.value, "verdict recorded", "")

    return StructuredTool.from_function(_guarded(ctx, final_answer), args_schema=FinalAnswer)


def get_context_tool(ctx: CaseContext) -> BaseTool:
    """Tool: one page of the context layer in full."""

    def get_context(topic: str) -> str:
        """Input: a context page name - systems, identifiers, policy, evidence_formats,
        time_rules, intent, reading_tool_results. Returns the full page (Markdown): curated
        domain knowledge about where each record type lives, how identifiers are matched, how
        each rule is applied, how every evidence format states a fact, the time rules and how
        to read intent in any language. Use it when you are unsure how to interpret a file,
        a field or a request; it costs no investigation budget beyond the turn."""
        text = page(topic)
        if text is None:
            return ctx.note(
                "get_context", topic, f"unknown topic; available: {', '.join(topics())}"
            )
        return ctx.note("get_context", topic, text)

    return StructuredTool.from_function(_guarded(ctx, get_context))


def describe_evidence_tool(ctx: CaseContext) -> BaseTool:
    """Tool: the structure of one file from the schema catalog (tables, collections, columns,
    counts).
    """

    def describe_evidence(evidence_id: str) -> str:
        """Input: an evidence_id. Returns the file's path, origin and capture time, then its
        structure from the schema catalog: for SQL dumps every table with its columns and row
        count (plus the snapshot scope), for JSON files every collection with its record count
        and keys, for logs and text the line count. Use it before sql_probe so queries name
        real tables and columns, and to judge whether a table is a full snapshot (absence
        proof) or a partial extract. Files captured after audit_at are refused."""
        entry = ctx.index.file_entry(evidence_id)
        if entry is None:
            return ctx.note("describe_evidence", evidence_id, "unknown evidence id", "")
        if entry.captured_at > ctx.audit_at:
            return ctx.note(
                "describe_evidence", evidence_id, "refused: captured after audit_at", ""
            )
        rows = ctx.index.catalog(evidence_id)
        body = "\n".join(f"{r.kind} {r.name}: rows={r.rows} columns={r.columns}" for r in rows)
        header = (
            f"{entry.file_path} origin={entry.origin} captured_at={entry.captured_at.isoformat()};"
            f" {len(rows)} structure entr(ies); format: kind name: rows columns"
        )
        return ctx.note(
            "describe_evidence", evidence_id, f"{header}\n{body or 'no structure recorded'}"
        )

    return StructuredTool.from_function(_guarded(ctx, describe_evidence))


TOOL_FACTORIES: tuple[Callable[[CaseContext], BaseTool], ...] = (
    get_portfolio_tool,
    get_resource_history_tool,
    get_policy_rules_tool,
    list_evidence_tool,
    search_evidence_tool,
    read_evidence_tool,
    get_context_tool,
    describe_evidence_tool,
    final_answer_tool,
)


def build_tools(ctx: CaseContext) -> list[BaseTool]:
    """The nine case-bound tools, in the order the prompt introduces them."""
    return [factory(ctx) for factory in TOOL_FACTORIES]
