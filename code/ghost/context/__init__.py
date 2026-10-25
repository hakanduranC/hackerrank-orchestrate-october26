"""The context layer: curated semantic knowledge the agent reads instead of guessing.

Shape borrowed from data-agent context layers (ktx): Markdown wiki pages hold the
domain — systems, identifiers, policy, evidence formats, time rules, request
intent in several languages, and how to read tool results. Core pages ride in
the cached system prompt; every page is also available on demand through the
``get_context`` tool, so the judge and the escalation stage share one source of
truth. The executable half (schema catalog of every dump) is built by the index.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent / "wiki"

# Pages always present in the system prompt, in reading order.
CORE_PAGES = (
    "systems",
    "evidence_formats",
    "policy",
    "time_rules",
    "intent",
    "reading_tool_results",
)


@lru_cache(maxsize=1)
def pages() -> dict[str, str]:
    """All wiki pages by topic name (file stem), loaded once."""
    return {p.stem: p.read_text(encoding="utf-8").strip() for p in sorted(WIKI_DIR.glob("*.md"))}


def page(topic: str) -> str | None:
    """One context page by topic name, or None."""
    return pages().get(topic.strip().lower().replace("-", "_").replace(" ", "_"))


def topics() -> list[str]:
    """The available topic names."""
    return sorted(pages())


def core_context() -> str:
    """The core pages joined for the cached system prompt."""
    return "\n\n".join(pages()[name] for name in CORE_PAGES if name in pages())
