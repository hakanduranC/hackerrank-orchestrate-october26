"""Hardening of an accepted agent verdict against the deterministic plan.

Two rules, both measured on the samples before they were adopted:

* A window-alternative candidate (a record touched by the same deletion batch,
  carrying no identifier) can be promoted to ``yes`` only when the record's own
  observations hold one of the subject's identifiers. A mention elsewhere in the
  same file is not enough; without a record-level link the claim is a guess and
  is downgraded to ``uncertain`` (the deterministic tie-break then decides).
* A portfolio record that was linked by identifier, relation or window and that
  the agent left out of its verdict is filled in from the baseline, so the agent
  can add and correct records but never silently drop one.
* Such a record can be demoted to ``no`` / ``uncertain`` only when its own
  observations carry identifiers that are *not* the subject's (it is someone
  else's record). "No file shows it" is a statement about state, not membership,
  so membership is restored and the agent's status is kept.
* Treatment for a portfolio record is the policy's and the workflow's decision,
  not the agent's; an override is reverted. The agent owns membership and state.
* A state without a citation is completed from the plan when it agrees with the
  plan, and reverted to the plan's state when it does not (found in the full run:
  two correct findings were emitted without their evidence id).
* A record the agent found only as a table row on a screenshot (not the record the
  screen displays in detail) is context, as the image parser treats it; it is not
  asserted (final run: five filler audit rows next to the case's own record).

Measured on the samples and on the first 100 evaluation requests: every
deviation these rules revert was a loss against the baseline or the answer key.
"""

from __future__ import annotations

from ghost.agent.baseline import classify_intent, verdict_for_view
from ghost.agent.schemas import (
    CaseVerdict,
    Intent,
    Membership,
    ResourceVerdict,
    Status,
    Treatment,
)
from ghost.data.index import EvidenceIndex
from ghost.data.observations import SourceFormat
from ghost.portfolio import Portfolio, ResourceView

_GROUNDED_LINKS = {"identifier", "related", "window"}


def harden(
    verdict: CaseVerdict, portfolio: Portfolio, index: EvidenceIndex
) -> tuple[CaseVerdict, list[str]]:
    """Apply the four rules to an accepted verdict; returns the verdict and what changed."""
    if verdict.intent == Intent.NOT_DELETION:
        return verdict, []
    notes: list[str] = []
    resources: list[ResourceVerdict] = []
    for r in verdict.resources:
        view = portfolio.by_id(r.resource_id)
        if view is None:
            resources.append(_screen_context_only(r, portfolio, index, notes))
            continue
        r = _downgrade_ungrounded_candidate(r, view, portfolio, index, notes)
        r = _restore_linked_membership(r, view, portfolio, index, notes)
        r = _complete_citation(r, view, notes)
        r = _revert_treatment(r, view, notes)
        resources.append(r)
    mentioned = {r.resource_id for r in resources}
    for view in portfolio.resources:
        if view.link_kind in _GROUNDED_LINKS and view.resource_id not in mentioned:
            notes.append(f"{view.resource_id}: omitted by the agent, filled from the baseline")
            resources.append(verdict_for_view(view))
    return verdict.model_copy(update={"resources": resources}), notes


def _downgrade_ungrounded_candidate(
    r: ResourceVerdict,
    view: ResourceView,
    portfolio: Portfolio,
    index: EvidenceIndex,
    notes: list[str],
) -> ResourceVerdict:
    """Rule 1: a batch candidate is the subject's only with a record-level identifier."""
    if (
        view.link_kind == "window-alternative"
        and r.is_subject == Membership.YES
        and not _identifier_grounded(r, portfolio, index)
    ):
        notes.append(f"{r.resource_id}: candidate promoted without identifier evidence")
        return r.model_copy(update={"is_subject": Membership.UNCERTAIN})
    return r


def _restore_linked_membership(
    r: ResourceVerdict,
    view: ResourceView,
    portfolio: Portfolio,
    index: EvidenceIndex,
    notes: list[str],
) -> ResourceVerdict:
    """Rule 2: a linked record can be demoted only when it carries someone else's identifiers."""
    if view.link_kind not in _GROUNDED_LINKS or r.is_subject == Membership.YES:
        return r
    if _foreign_record(r, portfolio, index):
        return r
    notes.append(f"{r.resource_id}: demoted without contrary identifiers; restored")
    base = verdict_for_view(view)
    status = r.status if r.status != Status.UNKNOWN else base.status
    evidence = r.evidence_id if r.status != Status.UNKNOWN else base.evidence_id
    return r.model_copy(
        update={"is_subject": Membership.YES, "status": status, "evidence_id": evidence}
    )


def _complete_citation(r: ResourceVerdict, view: ResourceView, notes: list[str]) -> ResourceVerdict:
    """Rule 3: a state without a citation is completed from the plan, or reverted to it."""
    if view.link_kind not in _GROUNDED_LINKS or r.evidence_id or r.status == Status.UNKNOWN:
        return r
    base = verdict_for_view(view)
    if r.status == base.status and base.evidence_id:
        notes.append(f"{r.resource_id}: evidence filled from the plan ({base.evidence_id})")
        return r.model_copy(update={"evidence_id": base.evidence_id})
    if r.status != base.status:
        notes.append(f"{r.resource_id}: uncited status {r.status.value} reverted to plan")
        return r.model_copy(update={"status": base.status, "evidence_id": base.evidence_id})
    return r


def _revert_treatment(r: ResourceVerdict, view: ResourceView, notes: list[str]) -> ResourceVerdict:
    """Rule 4: the treatment of a plan record is the policy's, not the agent's."""
    if view.link_kind not in _GROUNDED_LINKS or r.treatment.value == view.treatment.action:
        return r
    notes.append(
        f"{r.resource_id}: treatment {r.treatment.value} reverted to policy {view.treatment.action}"
    )
    return r.model_copy(update={"treatment": Treatment(view.treatment.action)})


def _screen_context_only(
    r: ResourceVerdict, portfolio: Portfolio, index: EvidenceIndex, notes: list[str]
) -> ResourceVerdict:
    """Rule 5 (agent-found records): a record a screenshot lists in a table but does not
    display in detail is context, not an attested record (the same rule the image parser
    applies); if no other evidence shows it, it stays uncertain."""
    if r.is_subject != Membership.YES:
        return r
    obs = index.history(r.resource_id, before=portfolio.request.audit_at)
    if not obs or any(o.source_format != SourceFormat.IMAGE for o in obs):
        return r
    if any(not o.identifiers.is_empty() for o in obs):
        return r  # the screen attests it in detail (identifiers attached)
    notes.append(f"{r.resource_id}: listed on a screen as context only; not asserted")
    return r.model_copy(update={"is_subject": Membership.UNCERTAIN})


def _foreign_record(r: ResourceVerdict, portfolio: Portfolio, index: EvidenceIndex) -> bool:
    """The record carries identifiers and none of them is the subject's."""
    subject = portfolio.subject.identifiers
    seen = [
        o.identifiers
        for o in index.history(r.resource_id, before=portfolio.request.audit_at)
        if not o.identifiers.is_empty()
    ]
    return bool(seen) and not any(ids.intersects(subject) for ids in seen)


def _identifier_grounded(r: ResourceVerdict, portfolio: Portfolio, index: EvidenceIndex) -> bool:
    """The record itself (not merely the file around it) carries a subject identifier."""
    subject = portfolio.subject.identifiers
    return any(
        o.identifiers.intersects(subject)
        for o in index.history(r.resource_id, before=portfolio.request.audit_at)
    )


def reconcile_intent(verdict: CaseVerdict, portfolio: Portfolio) -> tuple[CaseVerdict, str]:
    """Three intent signals: the model, the text rule and the privacy-intake tag.

    The deterministic signals decide; the model's intent is advisory and every disagreement
    is recorded. Measured reasons: on all 550 request texts the rules (scoping, negation in
    six languages, deletion vocabulary, intake tag) reproduce the 10 non-deletion requests
    exactly, while a model asked to answer in one turn without tools returned NOT_DELETION
    for a plain deletion request (custom_07). Returns the verdict and a one-line record of
    the signals when they disagreed, empty when they agreed.
    """
    intake = portfolio.intake_classification()
    rule = classify_intent(portfolio.request.request_text, intake.details if intake else None)
    model = verdict.intent
    if rule == model:
        return verdict, ""
    signals = (
        f"model={model.value} text_rule+intake={rule.value}"
        f"{' (intake tag ' + intake.activity_id + ')' if intake else ''} -> rules kept"
    )
    if rule == Intent.NOT_DELETION:
        verdict = verdict.model_copy(
            update={
                "intent": Intent.NOT_DELETION,
                "resources": [],
                "reason": f"{verdict.reason} [deterministic signals say non-deletion]".strip(),
            }
        )
    else:
        verdict = verdict.model_copy(
            update={
                "intent": Intent.DELETION,
                "reason": f"{verdict.reason} [deterministic signals say deletion]".strip(),
            }
        )
    return verdict, signals
