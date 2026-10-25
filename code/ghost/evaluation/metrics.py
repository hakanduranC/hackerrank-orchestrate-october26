"""Scoring of predicted rows against expected rows.

Four measures, all per case and aggregated:

* **status accuracy** - ``deletion_status`` matches;
* **step accuracy** - precision / recall / F1 of full step tuples
  (phase, system, resource, operation, state, evidence), order ignored;
* **field accuracy** - for steps matched on (phase, operation, system, resource),
  the share of ``state`` and ``evidence_id`` fields that also match;
* **sequence accuracy** - the ordered step list is exactly the expected one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

StepTuple = tuple[str, str, str, str, str, str]
StepKey = tuple[str, str, str, str]


@dataclass
class CaseScore:
    """Per-case scores and the missing / extra steps."""

    case_id: str
    expected_status: str
    predicted_status: str
    status_ok: bool
    tp: int
    fp: int
    fn: int
    field_ok: int
    field_total: int
    sequence_ok: bool
    missing: list[StepTuple] = field(default_factory=list)
    extra: list[StepTuple] = field(default_factory=list)


@dataclass
class Summary:
    """Aggregate metrics over a set of cases."""

    cases: int
    status_accuracy: float
    step_precision: float
    step_recall: float
    step_f1: float
    field_accuracy: float
    sequence_accuracy: float


def _tuples(steps_json: str) -> list[StepTuple]:
    return [
        (
            s["phase"],
            s["system_id"],
            s["resource_id"],
            s["operation"],
            s["state"],
            s["evidence_id"],
        )
        for s in json.loads(steps_json)
    ]


def _key(t: StepTuple) -> StepKey:
    return (t[0], t[3], t[1], t[2])


def score_case(
    case_id: str,
    expected_status: str,
    expected_json: str,
    predicted_status: str,
    predicted_json: str,
) -> CaseScore:
    """Score one predicted row against the key."""
    exp, pred = _tuples(expected_json), _tuples(predicted_json)
    exp_set, pred_set = set(exp), set(pred)
    tp = len(exp_set & pred_set)
    exp_by_key = {_key(t): t for t in exp}
    field_ok = field_total = 0
    for t in pred:
        match = exp_by_key.get(_key(t))
        if match is None:
            continue
        field_total += 2
        field_ok += int(t[4] == match[4]) + int(t[5] == match[5])
    return CaseScore(
        case_id=case_id,
        expected_status=expected_status,
        predicted_status=predicted_status,
        status_ok=expected_status == predicted_status,
        tp=tp,
        fp=len(pred_set - exp_set),
        fn=len(exp_set - pred_set),
        field_ok=field_ok,
        field_total=field_total,
        sequence_ok=exp == pred and expected_status == predicted_status,
        missing=sorted(exp_set - pred_set),
        extra=sorted(pred_set - exp_set),
    )


def summarize(scores: list[CaseScore]) -> Summary:
    """Aggregate per-case scores."""
    n = len(scores) or 1
    tp = sum(s.tp for s in scores)
    fp = sum(s.fp for s in scores)
    fn = sum(s.fn for s in scores)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    field_total = sum(s.field_total for s in scores) or 1
    return Summary(
        cases=len(scores),
        status_accuracy=sum(s.status_ok for s in scores) / n,
        step_precision=precision,
        step_recall=recall,
        step_f1=f1,
        field_accuracy=sum(s.field_ok for s in scores) / field_total,
        sequence_accuracy=sum(s.sequence_ok for s in scores) / n,
    )
