"""Evaluation entry point behind ``code/evaluation/main.py``.

Scores a verdict function against ``sample_output.csv`` (and later the custom
cases) and prints the four metrics plus a per-case diff.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from ghost.agent.baseline import baseline_verdict
from ghost.agent.graph import AgentJudge
from ghost.config import PARSER_FORMATS, load_settings, require_api_key
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import Dataset, load_dataset
from ghost.evaluation.custom import print_custom, run_custom
from ghost.evaluation.metrics import CaseScore, Summary, score_case, summarize
from ghost.identity import IdentityResolver
from ghost.logging_setup import configure_logging, get_logger
from ghost.output import steps_json
from ghost.runner import Runner, VerdictFn
from ghost.tracing import CaseTrace, aggregate, intent_disagreements, refusal_counts

log = get_logger()


def evaluate(
    data: Dataset,
    index: EvidenceIndex,
    resolver: IdentityResolver,
    verdict_fn: VerdictFn,
    include_uncertain: bool = False,
    workers: int = 1,
) -> tuple[Summary, list[CaseScore]]:
    """Score a verdict function against the sample key; returns the summary and per-case scores."""
    runner = Runner(data, index, resolver, verdict_fn, include_uncertain=include_uncertain)
    answers = {a.case_id: a for a in data.sample_answers}
    scores: list[CaseScore] = []
    results = runner.run(data.sample_requests, workers=workers)
    for req, result in zip(data.sample_requests, results, strict=True):
        answer = answers[req.case_id]
        scores.append(
            score_case(
                req.case_id,
                answer.deletion_status,
                answer.steps_json,
                result.deletion_status,
                steps_json(result),
            )
        )
    return summarize(scores), scores


def print_report(label: str, summary: Summary, scores: list[CaseScore], verbose: bool) -> None:
    """Print the four metrics and, with ``verbose``, every differing case."""
    log.info(f"== {label} on {summary.cases} sample cases")
    log.info(f"status accuracy    {summary.status_accuracy:6.1%}")
    log.info(
        f"step P/R/F1        {summary.step_precision:6.1%} / {summary.step_recall:6.1%}"
        f" / {summary.step_f1:6.1%}"
    )
    log.info(
        f"field accuracy     {summary.field_accuracy:6.1%}   (state + evidence on matched steps)"
    )
    log.info(f"sequence accuracy  {summary.sequence_accuracy:6.1%}   (exact ordered match)")
    if not verbose:
        return
    for s in scores:
        if s.sequence_ok:
            continue
        flag = "" if s.status_ok else f"  STATUS {s.predicted_status} != {s.expected_status}"
        log.info(f"-- {s.case_id}: tp={s.tp} fp={s.fp} fn={s.fn}{flag}")
        for t in s.missing:
            log.info(f"   missing  {t[0][:5]} {t[3]:<26} {t[1]:<20} {t[2]:<18} ev={t[5]}")
        for t in s.extra:
            log.info(f"   extra    {t[0][:5]} {t[3]:<26} {t[1]:<20} {t[2]:<18} ev={t[5]}")


def main(argv: list[str] | None = None) -> int:
    """Evaluation entry point: baseline, agent, custom cases or a format ablation."""
    parser = argparse.ArgumentParser(prog="ghost-eval")
    parser.add_argument("--baseline", action="store_true", help="score the deterministic baseline")
    parser.add_argument("--agent", action="store_true", help="score the agent (needs an API key)")
    parser.add_argument("--cases", help="comma-separated sample case ids to restrict the run")
    parser.add_argument(
        "--custom",
        action="store_true",
        help="run the constructed cases (with --agent: via the agent)",
    )
    parser.add_argument(
        "--ablate",
        metavar="FORMAT",
        help="format ablation: switch off one parser (sql,json,log,text,pdf,image) and score the"
        " floor (and the agent with --agent) on the samples; raw lines stay searchable",
    )
    parser.add_argument("--verbose", action="store_true", help="print per-case differences")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    settings = load_settings()
    if args.ablate:
        fmt = args.ablate.strip().lower()
        if fmt not in PARSER_FORMATS:
            parser.error(f"--ablate must be one of {sorted(PARSER_FORMATS)}")
        # A separate cache and trace directory keep the ablation apart from production runs.
        settings = replace(
            settings,
            disabled_parsers=frozenset({fmt}),
            cache_dir=settings.cache_dir / f"ablation_{fmt}",
            trace_dir=settings.trace_dir.parent / f"{settings.trace_dir.name}_ablation_{fmt}",
        )
        log.info(f"== format ablation: {fmt} parser off (raw lines and catalog kept)")
    data = load_dataset(settings.dataset_dir)
    index = EvidenceIndex.open_or_build(
        settings.dataset_dir,
        data.evidence_files,
        settings.cache_dir / "index.db",
        vision_cache_dir=settings.cache_dir / "vision",
        disabled_parsers=settings.disabled_parsers,
    )
    resolver = IdentityResolver(data)
    if args.cases:
        wanted = set(args.cases.split(","))
        data = replace(
            data, sample_requests=[r for r in data.sample_requests if r.case_id in wanted]
        )
    if args.custom:
        if args.agent:
            require_api_key()
        print_custom(run_custom(data, index, resolver, settings, use_agent=args.agent))
        return 0
    if args.baseline or not args.agent:
        summary, scores = evaluate(data, index, resolver, baseline_verdict)
        print_report("baseline", summary, scores, args.verbose)
    if args.agent:
        require_api_key()
        judge = AgentJudge(settings, index, data.rules)
        summary, scores = evaluate(
            data, index, resolver, judge, include_uncertain=False, workers=settings.concurrency
        )
        print_report(f"agent {settings.model}", summary, scores, args.verbose)
        print_usage(judge.traces)
    return 0


def print_usage(traces: list[CaseTrace]) -> None:
    """Print token usage, refusals by kind and intent disagreements of an agent run."""
    agg = aggregate(traces)
    log.info(f"guard refusals by kind: {refusal_counts(traces) or 'none'}")
    for case_id, signals in intent_disagreements(traces):
        log.info(f"intent disagreement {case_id}: {signals}")
    log.info(
        f"usage: {agg['calls']} calls, in={agg['input_tokens']} out={agg['output_tokens']}"
        f" cache_read={agg['cache_read_tokens']} cache_write={agg['cache_creation_tokens']}"
        f" avg_tokens/case={agg['avg_tokens_per_case']:.0f} avg_turns={agg['avg_turns']:.1f}"
        f" fallbacks={agg['fallbacks']} escalations={agg['escalations']}"
    )
