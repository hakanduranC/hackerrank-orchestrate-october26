"""Run the constructed cases through the real pipeline and check their expectations."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, cast

from ghost.agent.baseline import baseline_verdict
from ghost.agent.graph import AgentJudge
from ghost.config import Settings
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import Dataset
from ghost.evaluation.custom_cases import CUSTOM_CASES, CustomCase, expectation, materialise
from ghost.evaluation.metrics import score_case
from ghost.identity import IdentityResolver
from ghost.logging_setup import get_logger
from ghost.output import steps_json
from ghost.runner import Runner, VerdictFn

log = get_logger()


@dataclass
class CustomResult:
    """Outcome of one constructed case."""

    case_id: str
    purpose: str
    status: str
    steps: str
    passed: bool
    detail: str


def run_custom(
    data: Dataset,
    index: EvidenceIndex,
    resolver: IdentityResolver,
    settings: Settings,
    use_agent: bool,
) -> list[CustomResult]:
    """Run every constructed case through the real pipeline and check its expectation."""
    results: list[CustomResult] = []
    for case in CUSTOM_CASES:
        request = materialise(data, case)
        # The custom request replaces its base in the request set, so the activity-window
        # linker sees exactly one request for that window, as in the real run.
        others = [r for r in data.sample_requests + data.requests if r.case_id != case.base_case_id]
        scoped = replace(data, requests=others, sample_requests=[request])
        verdict_fn: VerdictFn = baseline_verdict
        judge: AgentJudge | None = None
        if use_agent:
            overrides = cast(dict[str, Any], dict(case.settings_override))
            judge = AgentJudge(replace(settings, **overrides), index, data.rules)
            verdict_fn = judge
        runner = Runner(scoped, index, resolver, verdict_fn, include_uncertain=False)
        result = runner.run_case(request)
        produced = steps_json(result)
        passed, detail = _check(data, case, result.deletion_status, produced)
        if judge is not None and judge.traces:
            t = judge.traces[-1]
            detail += (
                f" | turns={t.turns} fallback={t.fallback} escalated={t.escalated}"
                f" refusals={sum(len(e) for e in t.guard_errors)}"
                f"{' intent:' + t.intent_signals if t.intent_signals else ''}"
            )
        results.append(
            CustomResult(
                case.case_id, case.purpose, result.deletion_status, produced, passed, detail
            )
        )
    return results


def _check(data: Dataset, case: CustomCase, status: str, produced: str) -> tuple[bool, str]:
    notes: list[str] = []
    ok = True
    for rid in case.must_not_mention:
        if rid in produced:
            ok = False
            notes.append(f"{rid} appears in the output")
    if case.expected_status is not None and status != case.expected_status:
        ok = False
        notes.append(f"status {status} != {case.expected_status}")
    expected = expectation(data, case)
    if expected is not None:
        score = score_case(case.case_id, expected[0], expected[1], status, produced)
        if not score.sequence_ok:
            ok = False
            notes.append(f"steps differ from the key: tp={score.tp} fp={score.fp} fn={score.fn}")
        else:
            notes.append("exact match with the key")
    if not notes:
        notes.append(f"{len(json.loads(produced))} steps")
    return ok, "; ".join(notes)


def print_custom(results: list[CustomResult]) -> None:
    """Print pass/fail per constructed case."""
    passed = sum(r.passed for r in results)
    log.info(f"== custom cases: {passed}/{len(results)} passed")
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        log.info(f"{flag} {r.case_id:<36} {r.status:<15} {r.detail}")
        log.info(f"     purpose: {r.purpose}")
