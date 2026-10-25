"""EXECUTE + VERIFY as a LangGraph state machine.

    judge --(tool calls)--> tools --(verdict submitted)--> verify --(ok)--> END
      ^                       |                               |
      |---(no verdict yet)----+          (guard errors, attempts left)
      |------------------------------------------------------+
    any exit without an accepted verdict --> deterministic fallback (baseline)

* ``judge``   one model call with the case's tools bound; counts a turn.
* ``tools``   executes the requested tools; ``final_answer`` records the verdict.
* ``verify``  runs the guards; a refusal goes back to the model as an observation.

Bounded by ``GHOST_JUDGE_TURN_LIMIT`` turns and ``GHOST_RETRY_ALLOWANCE`` refusals.
On a deterministic trigger (``agent/escalation.py``) the same graph runs once more
with ``GHOST_ESCALATION_TURN_LIMIT`` turns and the extra ``sql_probe`` tool.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from ghost.agent.baseline import baseline_verdict
from ghost.agent.escalation import build_sql_probe, escalation_message, escalation_reason
from ghost.agent.guards import check_verdict
from ghost.agent.merge import harden, reconcile_intent
from ghost.agent.prompts import (
    case_message,
    deadline_message,
    guard_message,
    nudge_message,
    system_message,
)
from ghost.agent.schemas import CaseVerdict
from ghost.agent.tools import CaseContext, build_tools
from ghost.config import Settings
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import DeletionRule
from ghost.portfolio import Portfolio
from ghost.tracing import CaseTrace, Usage


class JudgeState(TypedDict):
    """LangGraph state of one investigation: the conversation, turns used, guard refusals answered,
    acceptance.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    turns: int
    attempts: int
    accepted: bool


def _usage_of(message: AIMessage) -> Usage:
    """Token usage of one call, from the raw Anthropic usage block.

    LangChain's normalised ``usage_metadata`` reports ``cache_creation`` as 0 and
    hides the write under ``ephemeral_5m_input_tokens``; the raw block is exact.
    Its ``input_tokens`` counts only uncached input, so the total is rebuilt here.
    """
    raw = cast(dict[str, Any], message.response_metadata.get("usage") or {})
    if raw:
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        cache_write = int(raw.get("cache_creation_input_tokens") or 0)
        return Usage(
            input_tokens=int(raw.get("input_tokens") or 0) + cache_read + cache_write,
            output_tokens=int(raw.get("output_tokens") or 0),
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
            calls=1,
        )
    meta = cast(dict[str, Any], dict(message.usage_metadata or {}))
    details = cast(dict[str, Any], meta.get("input_token_details") or {})
    return Usage(
        input_tokens=int(meta.get("input_tokens") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
        cache_read_tokens=int(details.get("cache_read") or 0),
        cache_creation_tokens=int(details.get("ephemeral_5m_input_tokens") or 0),
        calls=1,
    )


def _with_cache_breakpoint(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Mark the newest message as a cache breakpoint.

    The system prompt is cached once per run; this makes the growing per-case
    conversation cacheable too, so turn N+1 re-reads turn N's prefix from cache
    instead of resending every tool result. The copy is not stored in the state.
    """
    last = messages[-1]
    if not isinstance(last, HumanMessage | ToolMessage):
        return messages
    blocks: list[dict[str, Any]]
    if isinstance(last.content, str):
        blocks = [{"type": "text", "text": last.content or " "}]
    else:
        blocks = [
            dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)}
            for b in last.content
        ]
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    marked = last.model_copy(update={"content": blocks})
    return [*messages[:-1], marked]


class AgentJudge:
    """Callable verdict function: agent loop with guards, baseline as fallback."""

    def __init__(self, settings: Settings, index: EvidenceIndex, rules: list[DeletionRule]) -> None:
        self._settings = settings
        self._index = index
        self._rules = rules
        self._system = system_message(rules)
        self._model = ChatAnthropic(
            model_name=settings.model,
            max_tokens_to_sample=settings.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.effort},
            max_retries=settings.max_retries,
            timeout=settings.model_timeout_s,
            stop=None,
        )
        self.traces: list[CaseTrace] = []
        self._lock = threading.Lock()

    def __call__(self, portfolio: Portfolio) -> CaseVerdict:
        started = time.monotonic()
        trace = CaseTrace(case_id=portfolio.request.case_id, model=self._settings.model)
        ctx = self._investigate(portfolio, trace, self._settings.judge_turn_limit, escalate=None)
        accepted = ctx.verdict if ctx.accepted else None
        reason = escalation_reason(portfolio, self._index, accepted is not None)
        if reason is not None:
            trace.escalated, trace.escalation_reason = True, reason
            first_turns = trace.turns
            ctx2 = self._investigate(
                portfolio, trace, self._settings.escalation_turn_limit, escalate=reason
            )
            trace.escalation_turns = trace.turns - first_turns
            if ctx2.accepted and ctx2.verdict is not None:
                accepted = ctx2.verdict  # the escalated verdict supersedes the first one
            ctx.tool_calls += ctx2.tool_calls
        trace.tool_calls = ctx.tool_calls
        trace.elapsed_s = round(time.monotonic() - started, 2)
        if accepted is None:
            trace.fallback = True
            trace.fallback_reason = trace.fallback_reason or (
                "no guard-passing verdict within the turn / retry allowance"
            )
            verdict = baseline_verdict(portfolio)
        else:
            # Intent first: a verdict flipped to DELETION must then be completed from the plan.
            verdict, trace.intent_signals = reconcile_intent(accepted, portfolio)
            verdict, trace.hardening = harden(verdict, portfolio, self._index)
        trace.verdict_reason = verdict.reason[:300]
        trace.verdict_json = verdict.model_dump_json()
        with self._lock:
            self.traces.append(trace)
        trace.write(self._settings.trace_dir)
        return verdict

    def _investigate(
        self, portfolio: Portfolio, trace: CaseTrace, limit: int, escalate: str | None
    ) -> CaseContext:
        """One bounded investigation; the escalated one gets sql_probe and a wider read window."""
        ctx = CaseContext(portfolio, self._index, self._rules, portfolio.request.audit_at)
        ctx.read_window = self._settings.read_window
        tools = build_tools(ctx)
        messages: list[BaseMessage] = [self._system, case_message(portfolio)]
        if escalate is not None:
            ctx.read_window = self._settings.escalation_read_window
            tools.append(build_sql_probe(ctx, self._settings.dataset_dir))
            messages.append(escalation_message(escalate))
        graph = self._build(ctx, trace, tools, limit)
        try:
            graph.invoke(
                {"messages": messages, "turns": 0, "attempts": 0, "accepted": False},
                config={"recursion_limit": 6 * limit + 10},
            )
        except Exception as exc:  # noqa: BLE001 - any model/transport failure falls back
            trace.fallback_reason = f"{type(exc).__name__}: {exc}"[:300]
        return ctx

    def _build(
        self, ctx: CaseContext, trace: CaseTrace, tools: list[BaseTool], limit: int
    ) -> CompiledStateGraph:  # type: ignore[type-arg]
        nodes = _JudgeNodes(
            ctx=ctx,
            trace=trace,
            model=self._model.bind_tools(tools),
            final_only=self._model.bind_tools([t for t in tools if t.name == "final_answer"]),
            limit=limit,
            allowance=self._settings.retry_allowance,
            base_turns=trace.turns,  # turns already spent by an earlier stage
        )
        graph: StateGraph[JudgeState] = StateGraph(JudgeState)
        graph.add_node("judge", nodes.judge)
        graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
        graph.add_node("verify", nodes.verify)
        graph.add_node("nudge", nodes.nudge)
        graph.add_edge(START, "judge")
        graph.add_conditional_edges("judge", nodes.after_judge)
        graph.add_conditional_edges("tools", nodes.after_tools)
        graph.add_conditional_edges("verify", nodes.after_verify)
        graph.add_edge("nudge", "judge")
        return graph.compile()


@dataclass
class _JudgeNodes:
    """The graph's nodes and routers, sharing one case context and trace."""

    ctx: CaseContext
    trace: CaseTrace
    model: Runnable[Any, BaseMessage]
    final_only: Runnable[Any, BaseMessage]
    limit: int
    allowance: int
    base_turns: int

    def judge(self, state: JudgeState) -> dict[str, object]:
        """One model turn; on the last allowed turn only final_answer is bound."""
        turn = state["turns"] + 1
        self.trace.turns = self.base_turns + turn
        if turn < self.limit:
            response = self.model.invoke(_with_cache_breakpoint(state["messages"]))
            new: list[BaseMessage] = [response]
        else:  # last turn: no more investigation, the verdict is the only tool left
            deadline = deadline_message()
            response = self.final_only.invoke(
                _with_cache_breakpoint([*state["messages"], deadline])
            )
            new = [deadline, response]
        if isinstance(response, AIMessage):
            self.trace.usage.add(_usage_of(response))
        return {"messages": new, "turns": turn}

    def verify(self, state: JudgeState) -> dict[str, object]:
        """Run the guards on the submitted verdict; a refusal becomes an observation."""
        assert self.ctx.verdict is not None
        errors = check_verdict(self.ctx.verdict, self.ctx)
        if not errors:
            self.ctx.accepted = True
            return {"accepted": True}
        self.trace.guard_errors.append(errors)
        self.ctx.verdict = None
        attempt = state["attempts"] + 1
        return {"messages": [guard_message(errors, attempt, self.allowance)], "attempts": attempt}

    def nudge(self, state: JudgeState) -> dict[str, object]:
        """Ask a model that answered in text only to finish through final_answer."""
        return {"messages": [nudge_message()]}

    def after_judge(self, state: JudgeState) -> Literal["tools", "nudge", "__end__"]:
        """Route on the model's last message: tool calls, a nudge, or the end of the budget."""
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "nudge" if state["turns"] < self.limit else "__end__"

    def after_tools(self, state: JudgeState) -> Literal["verify", "judge", "__end__"]:
        """Route after tool execution: verify a submitted verdict or give the model another turn."""
        if self.ctx.verdict is not None:
            return "verify"
        return "judge" if state["turns"] < self.limit else "__end__"

    def after_verify(self, state: JudgeState) -> Literal["judge", "__end__"]:
        """Route after the guards: accept, retry within the allowance, or end (floor)."""
        if state["accepted"]:
            return "__end__"
        if state["attempts"] <= self.allowance and state["turns"] < self.limit:
            return "judge"
        return "__end__"
