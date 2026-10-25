"""Development diagnostics against the 32 sample cases.

Coverage of the evidence index, the portfolio's recall against the sample key, and a
readable dump of one case. Used while building the plan; not part of the production run.
"""

from __future__ import annotations

import json
from collections import Counter

from ghost.data.index import EvidenceIndex
from ghost.data.loaders import Dataset
from ghost.identity import IdentityResolver
from ghost.logging_setup import get_logger
from ghost.portfolio import PortfolioBuilder

log = get_logger()


def report_coverage(data: Dataset, index: EvidenceIndex, resolver: IdentityResolver) -> None:
    """For each sample step: is its resource observed for this subject at or before audit_at?"""
    requests = {r.case_id: r for r in data.sample_requests}
    totals: Counter[str] = Counter()
    misses: list[str] = []
    for answer in data.sample_answers:
        req = requests[answer.case_id]
        subject = resolver.resolve(req.email)
        if subject is None:
            misses.append(f"{answer.case_id}: subject unresolved")
            continue
        found = index.find_by_identifiers(subject.identifiers, before=req.audit_at)
        by_resource: dict[str, set[str]] = {}
        via_link: set[str] = set()
        for obs in found:
            by_resource.setdefault(obs.resource_id, set()).add(obs.evidence_id)
            via_link.update(obs.related_resources)
        for step in json.loads(answer.steps_json):
            rid, eid = step["resource_id"], step["evidence_id"]
            totals["steps"] += 1
            if rid in by_resource:
                totals["by_identifier"] += 1
            elif rid in via_link:
                totals["by_link"] += 1
            else:
                totals["unlinked"] += 1
                misses.append(f"{answer.case_id}: {rid} not linked to {subject.user_id}")
            if eid:
                totals["cited"] += 1
                if eid in by_resource.get(rid, set()):
                    totals["citation_found"] += 1
                elif any(
                    p.evidence_id == eid
                    for p in index.absence_proofs(rid, step["system_id"], req.audit_at)
                ):
                    totals["citation_by_absence"] += 1
                else:
                    misses.append(f"{answer.case_id}: {rid} not seen in {eid}")
    n = totals["steps"]
    log.info(
        f"resources   by identifier {totals['by_identifier']}/{n}  by link {totals['by_link']}/{n}"
        f"  unlinked {totals['unlinked']}/{n}"
    )
    log.info(
        f"citations   direct {totals['citation_found']}/{totals['cited']}"
        f"  by absence proof {totals['citation_by_absence']}/{totals['cited']}"
    )
    for miss in misses:
        log.info("  MISS", miss)


# Expected status implied by a sample step, used only to measure the portfolio.
_OP_STATUS = {
    "REPORT_REMAINING_RECORD": {"PRESENT", "IDENTIFIABLE"},
    "REPORT_EXPOSED_PII": {"IDENTIFIABLE"},
    "REPORT_ACTIVE_DATA_FLOW": {"ACTIVE"},
    "REPORT_RESTORE_RISK": {"RESTORABLE"},
    "VERIFY_RECORD_ABSENT": {"ABSENT"},
    "VERIFY_RECORD_PRESENT": {"PRESENT"},
    "VERIFY_RECORD_REDACTED": {"REDACTED"},
    "VERIFY_RECORD_ANONYMIZED": {"ANONYMIZED"},
    "VERIFY_DATA_FLOW_DISABLED": {"DISABLED"},
    "VERIFY_RESTORE_BLOCKED": {"BLOCKED"},
}


def report_portfolio(data: Dataset, index: EvidenceIndex, resolver: IdentityResolver) -> None:
    """Recall of expected resources, extras, and status agreement on the sample cases."""
    builder = PortfolioBuilder(data, index, resolver, data.requests + data.sample_requests)
    requests = {r.case_id: r for r in data.sample_requests}
    totals: Counter[str] = Counter()
    lines: list[str] = []
    for answer in data.sample_answers:
        portfolio = builder.build(requests[answer.case_id])
        steps = json.loads(answer.steps_json)
        expected = {s["resource_id"] for s in steps}
        views = portfolio.resources if portfolio else []
        picks = {r.resource_id for r in views if r.link_kind != "window-alternative"}
        candidates = {r.resource_id for r in views if r.link_kind == "window-alternative"}
        totals["expected"] += len(expected)
        totals["found"] += len(expected & picks)
        totals["found_via_candidate"] += len((expected - picks) & candidates)
        totals["extras"] += len(picks - expected)
        totals["candidates_offered"] += len(candidates)
        for step in steps:
            view = portfolio.by_id(step["resource_id"]) if portfolio else None
            if view is None:
                lines.append(f"  {answer.case_id}: missing {step['resource_id']}")
                continue
            if view.link_kind == "window-alternative":
                lines.append(
                    f"  {answer.case_id}: {step['resource_id']} offered only as a candidate"
                )
            if step["phase"] == "FINDING" or answer.deletion_status == "COMPLETE":
                totals["status_checked"] += 1
                if view.status in _OP_STATUS[step["operation"]]:
                    totals["status_ok"] += 1
                else:
                    lines.append(
                        f"  {answer.case_id}: {step['resource_id']} expected {step['operation']}"
                        f" but status={view.status} ({view.link_kind}, ev={view.proving_evidence})"
                    )
    log.info(
        f"sample resources   recall {totals['found']}/{totals['expected']}"
        f"   +{totals['found_via_candidate']} via candidates"
        f"   extras {totals['extras']}   candidates offered {totals['candidates_offered']}"
    )
    log.info(f"status       agree  {totals['status_ok']}/{totals['status_checked']}")
    for line in sorted(set(lines)):
        log.info(line)


def print_case(
    data: Dataset, index: EvidenceIndex, resolver: IdentityResolver, case_id: str
) -> None:
    """Dump one case's portfolio: subject, notes and every linked record."""
    builder = PortfolioBuilder(data, index, resolver, data.requests + data.sample_requests)
    req = next((r for r in data.sample_requests + data.requests if r.case_id == case_id), None)
    if req is None:
        log.info(f"unknown case id {case_id!r} (sample ids are case_01..case_32)")
        return
    portfolio = builder.build(req)
    if portfolio is None:
        log.info("subject unresolved")
        return
    log.info(
        f"{case_id} {req.email} -> {portfolio.subject.user_id} audit_at={req.audit_at.isoformat()}"
    )
    for note in portfolio.notes:
        log.info(f"  note: {note}")
    for r in portfolio.resources:
        log.info(
            f"  {r.system:<20} {r.resource_id:<18} {r.category:<20} {r.status:<12}"
            f" {r.treatment.action:<9} link={r.link_kind:<10} ev={r.proving_evidence:<12}"
            f" pii={len(r.direct_pii)} recreated={r.recreated} events={len(r.timeline)}"
        )
