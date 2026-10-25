"""Runs a set of requests through PLAN -> verdict -> COMPOSE.

The verdict function is pluggable: the deterministic baseline today, the agent
loop (with the baseline as fallback) once it exists. Everything else is shared,
so agent and baseline are compared on identical portfolios and identical
composition rules.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ghost.agent.baseline import baseline_verdict
from ghost.agent.schemas import CaseResult, CaseVerdict
from ghost.compose import compose
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import Dataset, DeletionRequest
from ghost.identity import IdentityResolver
from ghost.portfolio import Portfolio, PortfolioBuilder

VerdictFn = Callable[[Portfolio], CaseVerdict]


@dataclass
class RunStats:
    """Counters of one run."""

    cases: int = 0
    unresolved: int = 0
    ambiguous: int = 0


class Runner:
    """Runs cases through a verdict function and composes their rows."""

    def __init__(
        self,
        dataset: Dataset,
        index: EvidenceIndex,
        resolver: IdentityResolver,
        verdict_fn: VerdictFn = baseline_verdict,
        include_uncertain: bool = False,
    ) -> None:
        self._builder = PortfolioBuilder(
            dataset, index, resolver, dataset.requests + dataset.sample_requests
        )
        self._verdict_fn = verdict_fn
        self._include_uncertain = include_uncertain
        self.stats = RunStats()
        self._lock = threading.Lock()  # stats are updated from worker threads

    def run_case(self, req: DeletionRequest) -> CaseResult:
        """The output row of one request."""
        portfolio = self._builder.build(req)
        if portfolio is None:
            # No identity matches the request email: nothing can be linked, so there is nothing
            # to find or verify. The contract has no status for "unknown requester"; COMPLETE
            # with no steps is the neutral row. Never hit on this dataset (518/518 resolve).
            with self._lock:
                self.stats.cases += 1
                self.stats.unresolved += 1
            return CaseResult(case_id=req.case_id, deletion_status="COMPLETE", steps=[])
        verdict = self._verdict_fn(portfolio)
        with self._lock:
            self.stats.cases += 1
            if any(v.ambiguous for v in portfolio.resources):
                self.stats.ambiguous += 1
        return compose(verdict, include_uncertain=self._include_uncertain)

    def run(self, requests: list[DeletionRequest], workers: int = 1) -> list[CaseResult]:
        """Run every request; with ``workers > 1`` cases run concurrently, order is preserved."""
        if workers <= 1:
            return [self.run_case(req) for req in requests]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.run_case, requests))
