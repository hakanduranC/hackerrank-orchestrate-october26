"""Escalation trigger and the read-only sql_probe tool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_fixtures import EARLY, LATE, make_context
from ghost.agent.escalation import build_sql_probe, escalation_reason
from ghost.agent.tools import CaseContext
from ghost.portfolio import Portfolio
from langchain_core.tools import BaseTool

DUMP = """
CREATE TABLE customer_profiles (resource_id TEXT, email TEXT, status TEXT);
INSERT INTO customer_profiles VALUES ('account_1', 'a@x.com', 'ACTIVE');
INSERT INTO customer_profiles VALUES ('account_9', 'other@y.com', 'ACTIVE');
"""


def _probe(tmp_path: Path, captured: datetime) -> tuple[CaseContext, BaseTool]:
    ctx = make_context()
    (tmp_path / "dump.sql").write_text(DUMP, encoding="utf-8")
    ctx.index._conn.execute(
        "INSERT INTO files VALUES (?,?,?,?)",
        ("evidence_sql", "dump.sql", "test", captured.isoformat()),
    )
    return ctx, build_sql_probe(ctx, tmp_path)


def test_sql_probe_runs_a_select(tmp_path: Path) -> None:
    ctx, tool = _probe(tmp_path, EARLY)
    out = tool.invoke(
        {
            "evidence_id": "evidence_sql",
            "sql": "SELECT resource_id, email FROM customer_profiles WHERE email = 'a@x.com'",
        }
    )
    assert "account_1 | a@x.com" in out and "columns: resource_id | email" in out
    assert "account_1" in ctx.seen_resources  # what the probe showed can be asserted later


def test_sql_probe_rejects_writes_and_multiple_statements(tmp_path: Path) -> None:
    _, tool = _probe(tmp_path, EARLY)
    for sql in (
        "DELETE FROM customer_profiles",
        "SELECT 1; DROP TABLE customer_profiles",
        "PRAGMA table_info(x)",
    ):
        assert "refused" in tool.invoke({"evidence_id": "evidence_sql", "sql": sql})


def test_sql_probe_refuses_files_after_audit_at(tmp_path: Path) -> None:
    _, tool = _probe(tmp_path, LATE)
    assert "after audit_at" in tool.invoke({"evidence_id": "evidence_sql", "sql": "SELECT 1"})


def test_trigger_t1_when_no_verdict_was_accepted() -> None:
    ctx = make_context()
    assert str(escalation_reason(ctx.portfolio, ctx.index, accepted=False)).startswith("T1")


def test_trigger_t2_when_nothing_is_grounded() -> None:
    ctx = make_context()
    only_candidates = Portfolio(
        request=ctx.portfolio.request,
        subject=ctx.portfolio.subject,
        resources=[v for v in ctx.portfolio.resources if v.link_kind == "window-alternative"],
        notes=[],
    )
    assert str(escalation_reason(only_candidates, ctx.index, accepted=True)).startswith("T2")


def test_no_trigger_when_unknown_records_are_mentioned_nowhere() -> None:
    ctx = make_context()  # audit_record_1 is UNKNOWN but retained; analytics_1 has a state
    assert escalation_reason(ctx.portfolio, ctx.index, accepted=True) is None


def test_trigger_t3_needs_a_word_boundary_mention() -> None:
    ctx = make_context()
    view = next(v for v in ctx.portfolio.resources if v.resource_id == "analytics_1")
    object.__setattr__(view, "status", "UNKNOWN")  # mentioned in evidence_a -> escalate
    assert str(escalation_reason(ctx.portfolio, ctx.index, accepted=True)).startswith("T3")


def test_substring_fallback_respects_id_boundaries() -> None:
    ctx = make_context()
    ctx.index._insert_raw_lines("evidence_a", "INSERT INTO t VALUES ('account_19', 'x')")
    hits = ctx.index.search_raw("account_1", before=None)
    assert hits and all("account_19" not in h.text for h in hits)


def test_sql_probe_allows_keywords_inside_string_literals(tmp_path: Path) -> None:
    _, tool = _probe(tmp_path, EARLY)
    out = tool.invoke(
        {
            "evidence_id": "evidence_sql",
            "sql": "SELECT resource_id FROM customer_profiles WHERE status = 'update pending'",
        }
    )
    assert "refused" not in out


def test_escalated_loop_runs_after_a_failed_first_stage() -> None:
    """Stage 1 never calls a tool -> no verdict -> trigger T1 -> stage 2 submits one."""
    import threading
    from dataclasses import replace

    from ghost.agent.graph import AgentJudge
    from ghost.config import load_settings
    from langchain_core.messages import AIMessage, SystemMessage

    ctx = make_context()
    settings = replace(
        load_settings(), judge_turn_limit=2, escalation_turn_limit=3, retry_allowance=1
    )
    verdict_call = {
        "name": "final_answer",
        "id": "c9",
        "type": "tool_call",
        "args": {
            "intent": "DELETION",
            "reason": "escalated",
            "resources": [
                {
                    "system_id": "account_service",
                    "resource_id": "account_1",
                    "is_subject": "yes",
                    "status": "PRESENT",
                    "treatment": "DELETE",
                    "evidence_id": "evidence_a",
                }
            ],
        },
    }

    class Scripted:
        def __init__(self) -> None:
            self.n = 0

        def bind_tools(self, tools: object) -> Scripted:
            return self

        def invoke(self, messages: object, **_: object) -> AIMessage:
            self.n += 1
            # stage 1: two text-only turns (limit 2) -> ends without a verdict
            if self.n <= 2:
                return AIMessage(content="still thinking")
            return AIMessage(content="", tool_calls=[verdict_call])

    judge = AgentJudge.__new__(AgentJudge)
    judge._settings = settings  # type: ignore[attr-defined]
    judge._index = ctx.index  # type: ignore[attr-defined]
    judge._rules = []  # type: ignore[attr-defined]
    judge._system = SystemMessage(content="s")  # type: ignore[attr-defined]
    judge._model = Scripted()  # type: ignore[attr-defined]
    judge._lock = threading.Lock()  # type: ignore[attr-defined]
    judge.traces = []
    judge._settings = replace(settings, trace_dir=settings.trace_dir / "_test")  # type: ignore[attr-defined]
    verdict = judge(ctx.portfolio)
    trace = judge.traces[-1]
    assert trace.escalated and trace.escalation_reason.startswith("T1")
    assert trace.escalation_turns >= 1 and not trace.fallback
    assert verdict.reason.startswith("escalated")
    assert any(c["tool"] == "final_answer" for c in trace.tool_calls)
