"""Escalation: a second, larger-budget investigation for cases the plan cannot settle.

Entered only on a deterministic trigger, so the extra budget is spent where
there is something to find:

* ``T1`` the judge loop ended without a guard-passing verdict;
* ``T2`` the portfolio has no grounded record (identifier / related / window);
* ``T3`` a grounded, non-retained record has no in-window evidence of its state,
  yet some in-window file mentions it (word-boundary match), so a parser gap is
  possible.

The escalated loop gets one extra tool, ``sql_probe``: read-only SQL over one
evidence dump captured by ``audit_at``. Everything else (typed verdict, guards,
hardening, baseline floor) is unchanged.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool

from ghost.agent.tools import CaseContext, _guarded
from ghost.data.index import EvidenceIndex
from ghost.portfolio import Portfolio

GROUNDED_LINKS = frozenset({"identifier", "related", "window"})
_SELECT_RE = re.compile(r"^\s*(select|with)\b", re.I)
_FORBIDDEN_RE = re.compile(
    r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace|vacuum|reindex)\b",
    re.I,
)
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")  # keywords inside quotes are data
ROW_CAP = 50
STEP_BUDGET = 2_000  # progress-handler ticks of 10k VM steps each (~20M steps)


def escalation_reason(portfolio: Portfolio, index: EvidenceIndex, accepted: bool) -> str | None:
    """Why this case needs the escalated loop, or ``None`` if it does not."""
    if not accepted:
        return "T1: the first investigation ended without a guard-passing verdict"
    grounded = [v for v in portfolio.resources if v.link_kind in GROUNDED_LINKS]
    if not grounded:
        return "T2: no record could be linked to the subject by identifier, relation or window"
    mentioned = [
        v.resource_id
        for v in grounded
        if v.status == "UNKNOWN"
        and v.treatment.action != "RETAIN"
        and index.search_raw(v.resource_id, before=portfolio.request.audit_at, limit=1)
    ]
    if mentioned:
        return (
            "T3: no structured evidence shows the state of "
            + ", ".join(mentioned[:6])
            + " although an in-window file mentions them"
        )
    return None


def escalation_message(reason: str) -> HumanMessage:
    """The observation that opens the escalated loop: why it was entered and what the extra budget
    is for.
    """
    return HumanMessage(
        content=(
            "Escalated investigation. Reason: "
            f"{reason}. You have a larger budget and one extra tool, sql_probe (read-only SQL"
            " over one evidence dump). Use search_evidence on every identifier and resource id,"
            " read the files that mention them, query SQL dumps for the subject's identifiers,"
            " and then call final_answer. Keep the portfolio's status for records you cannot"
            " check; do not guess."
        )
    )


def build_sql_probe(ctx: CaseContext, dataset_dir: Path) -> BaseTool:
    """The escalated loop's extra tool: one read-only statement over one evidence dump, time-
    filtered and capped.
    """

    def sql_probe(evidence_id: str, sql: str) -> str:
        """Input: the evidence_id of a .sql evidence file and ONE read-only SELECT (or WITH)
        statement. The dump is loaded into a private in-memory SQLite and the query runs
        against it; returns the column names and up to 50 rows. Use it to ask a dump directly
        which tables hold an email, a phone, a hash or a resource id (e.g. SELECT * FROM
        customer_profiles WHERE email LIKE '%name%'). Files captured after audit_at are
        refused. Call describe_evidence(evidence_id) first for the real table and column names."""
        entry = ctx.index.file_entry(evidence_id)
        if entry is None:
            return ctx.note("sql_probe", evidence_id, "unknown evidence id", "")
        if entry.captured_at > ctx.audit_at:
            return ctx.note("sql_probe", evidence_id, "refused: file captured after audit_at", "")
        if not entry.file_path.lower().endswith(".sql"):
            return ctx.note("sql_probe", evidence_id, "refused: not a SQL evidence file", "")
        if (
            not _SELECT_RE.match(sql)
            or ";" in sql.rstrip().rstrip(";")
            or _FORBIDDEN_RE.search(_STRING_LITERAL_RE.sub("''", sql))
        ):
            return ctx.note(
                "sql_probe", evidence_id, "refused: one read-only SELECT / WITH statement only", ""
            )
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript((dataset_dir / entry.file_path).read_text(encoding="utf-8"))
            conn.execute("PRAGMA query_only = 1")
            budget = [STEP_BUDGET]

            def _tick() -> int:  # abort runaway queries (cross joins) after the step budget
                budget[0] -= 1
                return 1 if budget[0] < 0 else 0

            conn.set_progress_handler(_tick, 10_000)
            cur = conn.execute(sql.rstrip().rstrip(";"))
            columns = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(ROW_CAP + 1)
        except sqlite3.Error as exc:
            return ctx.note("sql_probe", evidence_id, f"sql error: {exc}", "")
        finally:
            conn.close()
        more = len(rows) > ROW_CAP
        body = "\n".join(
            " | ".join("" if v is None else str(v)[:120] for v in r) for r in rows[:ROW_CAP]
        )
        header = (
            f"{entry.file_path} captured_at={entry.captured_at.isoformat()};"
            f" {min(len(rows), ROW_CAP)} row(s){' (capped at 50)' if more else ''};"
            f" columns: {' | '.join(columns)}"
        )
        return ctx.note(
            "sql_probe", f"{evidence_id}: {sql[:120]}", f"{header}\n{body or 'no rows'}"
        )

    return StructuredTool.from_function(_guarded(ctx, sql_probe))
