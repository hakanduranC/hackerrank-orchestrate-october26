"""Per-case trace: what the agent did, what it cost, whether the fallback ran.

Traces are written as JSON under ``traces/`` and aggregated for the usage report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Usage:
    """Token and call counters."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    calls: int = 0

    def add(self, other: Usage) -> None:
        """Accumulate another usage."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.calls += other.calls


@dataclass
class CaseTrace:
    """Everything recorded about one case's investigation."""

    case_id: str
    model: str
    turns: int = 0
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    guard_errors: list[list[str]] = field(default_factory=list)
    fallback: bool = False
    fallback_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    elapsed_s: float = 0.0
    verdict_reason: str = ""
    verdict_json: str = ""
    hardening: list[str] = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str = ""
    escalation_turns: int = 0
    intent_signals: str = ""

    def write(self, trace_dir: Path) -> None:
        """Write the trace as JSON."""
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / f"{self.case_id}.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def refusal_counts(traces: list[CaseTrace]) -> dict[str, int]:
    """How often each guard kind refused a claim across the run (hallucinations caught)."""
    from ghost.agent.guards import refusal_kind  # local import: tracing stays dependency-light

    counts: dict[str, int] = {}
    for t in traces:
        for errors in t.guard_errors:
            for e in errors:
                kind = refusal_kind(e)
                counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def intent_disagreements(traces: list[CaseTrace]) -> list[tuple[str, str]]:
    """Cases where the model's intent disagreed with the deterministic signals."""
    return [(t.case_id, t.intent_signals) for t in traces if t.intent_signals]


def aggregate(traces: list[CaseTrace]) -> dict[str, float]:
    """Totals and averages over traces."""
    total = Usage()
    for t in traces:
        total.add(t.usage)
    n = max(len(traces), 1)
    return {
        "cases": len(traces),
        "calls": total.calls,
        "input_tokens": total.input_tokens,
        "output_tokens": total.output_tokens,
        "cache_read_tokens": total.cache_read_tokens,
        "cache_creation_tokens": total.cache_creation_tokens,
        "avg_tokens_per_case": (total.input_tokens + total.output_tokens) / n,
        "fallbacks": sum(1 for t in traces if t.fallback),
        "escalations": sum(1 for t in traces if t.escalated),
        "avg_turns": sum(t.turns for t in traces) / n,
    }
