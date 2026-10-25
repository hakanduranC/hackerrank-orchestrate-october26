"""The context layer: pages load, core pages reach the prompt, tools expose them."""

from __future__ import annotations

from agent_fixtures import make_context
from ghost.agent.prompts import system_message
from ghost.agent.tools import describe_evidence_tool, get_context_tool
from ghost.context import CORE_PAGES, core_context, page, pages, topics


def test_all_pages_present_and_titled() -> None:
    expected = {
        "systems",
        "identifiers",
        "policy",
        "evidence_formats",
        "time_rules",
        "intent",
        "reading_tool_results",
    }
    assert expected <= set(topics())
    for name, text in pages().items():
        assert text.startswith("# "), name
        assert len(text) > 400, name


def test_core_pages_ride_in_the_system_prompt() -> None:
    text = system_message([]).content[0]["text"]  # type: ignore[index]
    for name in CORE_PAGES:
        first_line = pages()[name].splitlines()[0]
        assert first_line in text, name
    assert "내 계정과 개인정보를 완전히 삭제해 주세요" in text  # multilingual intent examples
    assert core_context() in text


def test_get_context_tool_returns_pages_and_lists_topics() -> None:
    ctx = make_context()
    tool = get_context_tool(ctx)
    assert tool.invoke({"topic": "time_rules"}) == page("time_rules")
    assert "available:" in tool.invoke({"topic": "nonsense"})
    assert ctx.tool_calls[-1]["tool"] == "get_context"


def test_describe_evidence_respects_the_time_cut() -> None:
    ctx = make_context()
    tool = describe_evidence_tool(ctx)
    assert "refused" in tool.invoke({"evidence_id": "evidence_late"})
    assert "no structure recorded" in tool.invoke({"evidence_id": "evidence_a"})
