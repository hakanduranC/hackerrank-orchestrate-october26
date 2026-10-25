"""Command-line entry point behind ``code/main.py``.

Commands: ``--check`` (dataset counts), ``--build-index`` (parse all
text evidence into the SQLite index) and ``--coverage`` (does the index find
every resource the sample answers cite, under the right subject, in time?).
"""

from __future__ import annotations

import argparse
import os
from collections import Counter

from ghost.agent.baseline import baseline_verdict
from ghost.agent.graph import AgentJudge
from ghost.config import Settings, load_settings, require_api_key
from ghost.data.ids import IMAGE_SUFFIXES
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import Dataset, load_dataset
from ghost.data.vision import extract_images, find_cached
from ghost.evaluation import diagnostics
from ghost.identity import IdentityResolver
from ghost.logging_setup import configure_logging, get_logger
from ghost.output import write_output
from ghost.runner import Runner
from ghost.tracing import aggregate, intent_disagreements, refusal_counts
from ghost.validate import validate_output

log = get_logger()


def build_parser() -> argparse.ArgumentParser:
    """Command-line options of the production entry point."""
    parser = argparse.ArgumentParser(prog="ghost", description="Ghost Data deletion audit")
    parser.add_argument("--check", action="store_true", help="load the dataset and print counts")
    parser.add_argument("--build-index", action="store_true", help="parse evidence into the index")
    parser.add_argument("--force", action="store_true", help="rebuild the index even if cached")
    parser.add_argument("--coverage", action="store_true", help="measure index coverage on samples")
    parser.add_argument(
        "--portfolio", action="store_true", help="measure portfolio recall on samples"
    )
    parser.add_argument("--case", help="print the portfolio of one sample case")
    parser.add_argument(
        "--run", action="store_true", help="audit every request and write output.csv"
    )
    parser.add_argument("--limit", type=int, help="with --run: only the first N requests")
    parser.add_argument("--cases", help="with --run: comma-separated case ids to process")
    parser.add_argument(
        "--validate", action="store_true", help="check output.csv against the contract"
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="with the default run: skip the agent even if an API key is configured",
    )
    parser.add_argument("--agent", action="store_true", help="with --run: use the agent judge")
    parser.add_argument(
        "--extract-images",
        action="store_true",
        help="read every image evidence file once with the vision model (cached by content hash)",
    )
    return parser


def report_counts(settings: Settings, data: Dataset) -> None:
    """Print dataset row counts (the --check command)."""
    rows = {
        "deletion_requests": len(data.requests),
        "sample_requests": len(data.sample_requests),
        "sample_answers": len(data.sample_answers),
        "user_identities": len(data.identities),
        "user_audit_log": len(data.audit_log),
        "system_activity": len(data.activity),
        "deletion_rules": len(data.rules),
        "evidence_files": len(data.evidence_files),
    }
    for name, count in rows.items():
        log.info(f"{name:<20} {count:>5}")
    log.info(f"{'dataset_dir':<20} {settings.dataset_dir}")
    log.info(f"{'model':<20} {settings.model} (effort={settings.effort})")


def open_index(settings: Settings, data: Dataset, force: bool) -> EvidenceIndex:
    """Open the cached evidence index or build it, reporting observations per format and any files
    not fully indexed.
    """
    index = EvidenceIndex.open_or_build(
        settings.dataset_dir,
        data.evidence_files,
        settings.cache_dir / "index.db",
        force=force,
        vision_cache_dir=settings.cache_dir / "vision",
        disabled_parsers=settings.disabled_parsers,
    )
    for problem in index.build_problems():
        log.warning(
            f"warning: {problem.evidence_id} ({problem.name}) not fully indexed: {problem.columns}"
        )
    for fmt, n in sorted(index.counts_by_format().items()):
        log.info(f"{'observations.' + fmt:<24} {n:>6}")
    return index


def report_identity(data: Dataset) -> IdentityResolver:
    """Build the identity resolver and print how many requests resolve by current and historical
    email.
    """
    resolver = IdentityResolver(data)
    via: Counter[str] = Counter()
    for req in data.requests:
        subject = resolver.resolve(req.email)
        via[subject.resolved_via if subject else "UNRESOLVED"] += 1
    log.info(f"{'identity.resolved':<24} {dict(via)}")
    return resolver


def run_all(
    settings: Settings,
    data: Dataset,
    index: EvidenceIndex,
    resolver: IdentityResolver,
    limit: int | None,
    agent: bool = False,
    cases: str | None = None,
) -> int:
    """Produce ``output.csv`` for every request (baseline, or the agent judge with ``--agent``).

    Returns the number of cases that fell back to the deterministic floor (0 for a clean run).
    """
    requests = data.requests[:limit] if limit else data.requests
    if cases:
        wanted = set(cases.split(","))
        requests = [r for r in requests if r.case_id in wanted]
    if agent:
        require_api_key()
        judge = AgentJudge(settings, index, data.rules)
        runner = Runner(data, index, resolver, judge, include_uncertain=False)
        results = runner.run(requests, workers=settings.concurrency)
    else:
        runner = Runner(data, index, resolver, baseline_verdict)
        results = runner.run(requests)
    write_output(results, settings.output_path)
    statuses = Counter(r.deletion_status for r in results)
    log.info(f"{'rows written':<24} {len(results):>6}  -> {settings.output_path}")
    log.info(f"{'statuses':<24} {dict(statuses)}")
    log.info(f"{'ambiguous cases':<24} {runner.stats.ambiguous:>6}")
    log.info(f"{'unresolved subjects':<24} {runner.stats.unresolved:>6}")
    if agent:
        agg = aggregate(judge.traces)
        refusals = refusal_counts(judge.traces) or "none"
        log.info(
            f"{'agent':<24} {int(agg['calls'])} calls, {int(agg['fallbacks'])} fallback(s),"
            f" {int(agg['escalations'])} escalation(s), refusals {refusals},"
            f" {len(intent_disagreements(judge.traces))} intent disagreement(s)"
        )
        if agg["fallbacks"]:
            log.info(
                "warning: some cases fell back to the deterministic floor; see traces/*.json"
                " (fallback_reason) - a bad API key or sustained API errors look like this"
            )
    if not limit and not cases:
        validate(settings, data)
    return int(agg["fallbacks"]) if agent else 0


def validate(settings: Settings, data: Dataset) -> bool:
    """Check the written output.csv against the contract; print every violation."""
    if not settings.output_path.exists():
        log.info(
            f"validation: {settings.output_path} does not exist; run `python3 code/main.py` first"
        )
        return False
    report = validate_output(settings.output_path, data)
    log.info(f"{'validation':<24} {report.rows:>6} rows, {len(report.errors)} violation(s)")
    for err in report.errors[:40]:
        log.info(f"  ! {err}")
    return report.ok


def warn_missing_vision_cache(settings: Settings, data: Dataset) -> None:
    """Images without a cached extraction parse to nothing; say so instead of staying silent."""
    missing = [
        e.file_id
        for e in data.evidence_files
        if (settings.dataset_dir / e.file_path).suffix.lower() in IMAGE_SUFFIXES
        and find_cached(settings.dataset_dir / e.file_path, settings.cache_dir / "vision") is None
    ]
    if missing:
        log.warning(
            f"warning: {len(missing)} image evidence file(s) have no vision extraction yet"
            f" ({', '.join(missing[:5])}{'...' if len(missing) > 5 else ''});"
            " run `python code/main.py --extract-images` (needs ANTHROPIC_API_KEY)"
        )


def main(argv: list[str] | None = None) -> int:
    """Entry point: dispatch the command, or run the full pipeline when no command is given."""
    args = build_parser().parse_args(argv)
    configure_logging(False)
    settings = load_settings()
    data = load_dataset(settings.dataset_dir)
    if args.check:
        report_counts(settings, data)
        return 0
    if args.validate:
        return 0 if validate(settings, data) else 1
    if not any(
        (args.build_index, args.coverage, args.portfolio, args.case, args.run, args.extract_images)
    ):
        return default_run(settings, data, args.baseline_only)
    if args.extract_images:
        require_api_key()
        for res in extract_images(settings, data.evidence_files):
            ex = res.extraction
            log.info(
                f"{res.file_id:<12} {'cached' if res.cached else 'extracted':<9}"
                f" kind={ex.document_kind:<22} ids={len(ex.resource_ids):>2}"
                f" emails={len(ex.identifiers.emails)} phones={len(ex.identifiers.phones)}"
                f" hashes={len(ex.identifiers.email_hashes)} users={len(ex.identifiers.user_ids)}"
                f" lines={len(ex.lines):>3} tokens={res.input_tokens}+{res.output_tokens}"
            )
        return 0
    if args.build_index or args.coverage or args.portfolio or args.case or args.run:
        warn_missing_vision_cache(settings, data)
        index = open_index(settings, data, args.force)
        resolver = report_identity(data)
        if args.coverage:
            diagnostics.report_coverage(data, index, resolver)
        if args.portfolio:
            diagnostics.report_portfolio(data, index, resolver)
        if args.case:
            diagnostics.print_case(data, index, resolver, args.case)
        if args.run:
            run_all(settings, data, index, resolver, args.limit, args.agent, args.cases)
        return 0
    return 0


def default_run(settings: Settings, data: Dataset, baseline_only: bool) -> int:
    """`python code/main.py` with no arguments: the full pipeline for all 518 requests.

    The agent judge runs when an API key is configured; otherwise the deterministic
    baseline produces the output and says so. Images are read from the vision cache.
    """
    use_agent = bool(os.environ.get("ANTHROPIC_API_KEY")) and not baseline_only
    log.info(
        f"mode: {'agent judge + baseline floor' if use_agent else 'deterministic baseline'}"
        f" ({'ANTHROPIC_API_KEY set' if use_agent else 'no API key or --baseline-only'})"
    )
    warn_missing_vision_cache(settings, data)
    index = open_index(settings, data, force=False)
    resolver = report_identity(data)
    fallbacks = run_all(settings, data, index, resolver, None, use_agent)
    return 0 if validate(settings, data) and not fallbacks else 1
