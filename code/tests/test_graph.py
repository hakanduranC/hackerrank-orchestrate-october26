"""The judge graph's routing, caps and fallback, driven by a scripted fake model."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent_fixtures import make_context
from ghost.agent.graph import AgentJudge, JudgeState, _JudgeNodes, _with_cache_breakpoint
from ghost.agent.tools import build_tools
from ghost.tracing import CaseTrace
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class ScriptedModel:
    """Returns the next scripted AIMessage on every invoke; records what it was asked."""

    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[object], **_: object) -> AIMessage:
        self.calls.append(list(messages))
        return self.script.pop(0) if self.script else AIMessage(content="done")


def _call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _nodes(model: ScriptedModel, limit: int = 3, allowance: int = 1) -> tuple[_JudgeNodes, Any]:
    ctx = make_context()
    build_tools(ctx)  # binds the case's tools (final_answer records into ctx)
    nodes = _JudgeNodes(
        ctx=ctx,
        trace=CaseTrace(case_id="case_t", model="fake"),
        model=model,  # type: ignore[arg-type]
        final_only=model,  # type: ignore[arg-type]
        limit=limit,
        allowance=allowance,
        base_turns=0,
    )
    return nodes, ctx


def _state(
    messages: list[Any], turns: int = 0, attempts: int = 0, accepted: bool = False
) -> JudgeState:
    return {"messages": messages, "turns": turns, "attempts": attempts, "accepted": accepted}


def test_router_sends_tool_calls_to_tools_and_text_to_nudge_then_end() -> None:
    nodes, _ = _nodes(ScriptedModel([]))
    with_tools = AIMessage(content="", tool_calls=[_call("get_portfolio", {})])
    assert nodes.after_judge(_state([with_tools], turns=1)) == "tools"
    text_only = AIMessage(content="thinking out loud")
    assert nodes.after_judge(_state([text_only], turns=1)) == "nudge"
    assert nodes.after_judge(_state([text_only], turns=3)) == "__end__"  # cap reached -> floor


def test_judge_counts_turns_and_binds_final_answer_only_on_the_last_turn() -> None:
    model = ScriptedModel([AIMessage(content="a"), AIMessage(content="b"), AIMessage(content="c")])
    nodes, _ = _nodes(model, limit=2)
    out1 = nodes.judge(_state([SystemMessage(content="s"), HumanMessage(content="h")], turns=0))
    assert out1["turns"] == 1 and nodes.trace.turns == 1
    out2 = nodes.judge(_state([SystemMessage(content="s"), HumanMessage(content="h")], turns=1))
    assert out2["turns"] == 2
    # on the last turn a deadline message is appended before the model sees the conversation
    last_prompt = model.calls[-1]
    assert any("Investigation budget exhausted" in str(m.content) for m in last_prompt)
    assert isinstance(out2["messages"], list) and len(out2["messages"]) == 2


def test_verify_accepts_a_grounded_verdict_and_refuses_an_ungrounded_one() -> None:
    nodes, ctx = _nodes(ScriptedModel([]), allowance=1)
    tools = {t.name: t for t in build_tools(ctx)}
    good = {
        "intent": "DELETION",
        "reason": "r",
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
    }
    tools["final_answer"].invoke(good)
    assert nodes.verify(_state([]))["accepted"] is True and ctx.accepted
    ctx.accepted = False
    bad = dict(good, resources=[dict(good["resources"][0], evidence_id="evidence_999")])
    tools["final_answer"].invoke(bad)
    out = nodes.verify(_state([], attempts=0))
    assert out["attempts"] == 1 and ctx.verdict is None
    assert "1 attempt(s) left" in str(out["messages"][0].content)
    assert nodes.after_verify(_state([], turns=1, attempts=1)) == "judge"
    assert nodes.after_verify(_state([], turns=1, attempts=2)) == "__end__"  # allowance spent


def test_cache_breakpoint_marks_only_the_newest_message() -> None:
    msgs: list[Any] = [
        SystemMessage(content="s"),
        HumanMessage(content="h"),
        ToolMessage(content="t", tool_call_id="x"),
    ]
    marked = _with_cache_breakpoint(msgs)
    assert marked[-1].content[-1]["cache_control"] == {"type": "ephemeral"}  # type: ignore[index]
    assert msgs[-1].content == "t"  # original untouched


def test_model_failure_falls_back_to_the_floor() -> None:
    from ghost.config import load_settings

    settings = replace(load_settings(), trace_dir=load_settings().trace_dir / "_test")
    ctx = make_context()
    judge = AgentJudge.__new__(AgentJudge)
    judge._settings = settings  # type: ignore[attr-defined]
    judge._index = ctx.index  # type: ignore[attr-defined]
    judge._rules = []  # type: ignore[attr-defined]
    judge._system = SystemMessage(content="s")  # type: ignore[attr-defined]
    judge.traces = []
    import threading

    judge._lock = threading.Lock()  # type: ignore[attr-defined]

    class Boom:
        def bind_tools(self, tools: object) -> Boom:
            return self

        def invoke(self, *_: object, **__: object) -> AIMessage:
            raise RuntimeError("api down")

    judge._model = Boom()  # type: ignore[attr-defined]
    verdict = judge(ctx.portfolio)
    trace = judge.traces[-1]
    assert trace.fallback and "api down" in trace.fallback_reason
    assert verdict.reason == "deterministic baseline"
    assert {r.resource_id for r in verdict.resources} >= {"account_1", "analytics_1"}
