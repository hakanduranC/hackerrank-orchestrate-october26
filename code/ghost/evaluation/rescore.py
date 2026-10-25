"""Re-score stored agent verdicts offline, without calling the model again.

Each trace under ``traces/`` carries the accepted verdict as JSON. This module
composes those verdicts with different policies (for example keeping or dropping
``uncertain`` candidates) and scores them against ``sample_output.csv``, so a
strategy comparison costs no tokens.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghost.agent.merge import harden
from ghost.agent.schemas import CaseVerdict
from ghost.compose import compose
from ghost.config import load_settings
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import load_dataset
from ghost.evaluation.cli import print_report
from ghost.evaluation.metrics import CaseScore, score_case, summarize
from ghost.identity import IdentityResolver
from ghost.logging_setup import configure_logging, get_logger
from ghost.output import steps_json
from ghost.portfolio import PortfolioBuilder

log = get_logger()


def load_verdicts(trace_dir: Path) -> dict[str, CaseVerdict]:
    """The stored verdict of every trace in a directory."""
    verdicts: dict[str, CaseVerdict] = {}
    for path in sorted(trace_dir.glob("*.json")):
        trace = json.loads(path.read_text(encoding="utf-8"))
        if trace.get("verdict_json"):
            verdicts[trace["case_id"]] = CaseVerdict.model_validate_json(trace["verdict_json"])
    return verdicts


def main(argv: list[str] | None = None) -> int:
    """Re-score stored verdicts offline, with and without ``uncertain`` records and optionally
    hardened.
    """
    parser = argparse.ArgumentParser(prog="ghost-rescore")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--harden", action="store_true", help="apply merge.harden to each verdict")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    settings = load_settings()
    data = load_dataset(settings.dataset_dir)
    verdicts = load_verdicts(settings.trace_dir)
    answers = {a.case_id: a for a in data.sample_answers}
    if args.harden:
        index = EvidenceIndex.open_or_build(
            settings.dataset_dir,
            data.evidence_files,
            settings.cache_dir / "index.db",
            vision_cache_dir=settings.cache_dir / "vision",
            disabled_parsers=settings.disabled_parsers,
        )
        builder = PortfolioBuilder(
            data, index, IdentityResolver(data), data.requests + data.sample_requests
        )
        notes = 0
        for req in data.sample_requests:
            if req.case_id in verdicts and (portfolio := builder.build(req)) is not None:
                verdicts[req.case_id], changed = harden(verdicts[req.case_id], portfolio, index)
                notes += len(changed)
        log.info(f"hardening changed {notes} resource entries")
    for include_uncertain in (True, False):
        scores: list[CaseScore] = []
        for case_id, answer in answers.items():
            if case_id not in verdicts:
                continue
            result = compose(verdicts[case_id], include_uncertain=include_uncertain)
            scores.append(
                score_case(
                    case_id,
                    answer.deletion_status,
                    answer.steps_json,
                    result.deletion_status,
                    steps_json(result),
                )
            )
        label = "uncertain kept" if include_uncertain else "uncertain dropped"
        print_report(label, summarize(scores), scores, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
