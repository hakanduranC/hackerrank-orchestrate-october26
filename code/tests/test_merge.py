"""Hardening of an accepted verdict against the deterministic plan."""

from __future__ import annotations

from agent_fixtures import make_context
from ghost.agent.merge import harden
from ghost.agent.schemas import CaseVerdict, Intent, Membership, ResourceVerdict, Status, Treatment


def _rv(
    rid: str, member: Membership, status: Status, treatment: Treatment, ev: str = ""
) -> ResourceVerdict:
    return ResourceVerdict(
        system_id=rid.rsplit("_", 1)[0] + "_service",
        resource_id=rid,
        is_subject=member,
        status=status,
        treatment=treatment,
        evidence_id=ev,
    )


def _harden(*resources: ResourceVerdict) -> tuple[dict[str, ResourceVerdict], list[str]]:
    ctx = make_context()
    verdict = CaseVerdict(case_id="case_t", intent=Intent.DELETION, resources=list(resources))
    out, notes = harden(verdict, ctx.portfolio, ctx.index)
    return {r.resource_id: r for r in out.resources}, notes


def test_candidate_without_record_level_identifier_is_downgraded() -> None:
    by_id, notes = _harden(
        _rv("analytics_2", Membership.YES, Status.ANONYMIZED, Treatment.ANONYMIZE, "evidence_a")
    )
    assert by_id["analytics_2"].is_subject == Membership.UNCERTAIN
    assert any("promoted without identifier evidence" in n for n in notes)


def test_omitted_linked_records_are_filled_from_the_baseline() -> None:
    by_id, notes = _harden(
        _rv("account_1", Membership.YES, Status.PRESENT, Treatment.DELETE, "evidence_a")
    )
    assert {"analytics_1", "audit_record_1"} <= set(by_id)
    assert by_id["audit_record_1"].treatment == Treatment.RETAIN
    assert sum("filled from the baseline" in n for n in notes) == 2


def test_linked_record_cannot_be_demoted_without_contrary_identifiers() -> None:
    by_id, notes = _harden(
        _rv("account_1", Membership.UNCERTAIN, Status.PRESENT, Treatment.DELETE, "evidence_a")
    )
    assert by_id["account_1"].is_subject == Membership.YES
    assert by_id["account_1"].status == Status.PRESENT  # the agent's state is kept
    assert any("restored" in n for n in notes)


def test_treatment_of_a_portfolio_record_is_the_policys() -> None:
    by_id, notes = _harden(
        _rv("account_1", Membership.YES, Status.PRESENT, Treatment.REDACT, "evidence_a")
    )
    assert by_id["account_1"].treatment == Treatment.DELETE
    assert any("reverted to policy DELETE" in n for n in notes)


def test_not_deletion_is_left_alone() -> None:
    ctx = make_context()
    verdict = CaseVerdict(case_id="case_t", intent=Intent.NOT_DELETION, resources=[])
    out, notes = harden(verdict, ctx.portfolio, ctx.index)
    assert out.resources == [] and notes == []


def test_deterministic_non_deletion_overrides_a_model_deletion() -> None:
    from datetime import UTC, datetime

    from ghost.agent.merge import reconcile_intent
    from ghost.portfolio import ActivityEvent

    ctx = make_context()
    account = ctx.portfolio.by_id("account_1")
    assert account is not None
    tag = (
        "Privacy intake classified request for account_1 as non-deletion;"
        " no destructive action was authorized."
    )
    account.timeline.append(
        ActivityEvent(
            "activity_9",
            datetime(2026, 9, 2, tzinfo=UTC),
            "CLASSIFY_REQUEST",
            "privacy_intake",
            "SUCCESS",
            tag,
        )
    )
    verdict = CaseVerdict(
        case_id="case_t",
        intent=Intent.DELETION,
        reason="model said delete",
        resources=[
            _rv("account_1", Membership.YES, Status.PRESENT, Treatment.DELETE, "evidence_a")
        ],
    )
    out, signals = reconcile_intent(verdict, ctx.portfolio)
    assert out.intent == Intent.NOT_DELETION and out.resources == []
    assert signals.startswith("model=DELETION text_rule+intake=NOT_DELETION")
    assert signals.endswith("-> rules kept")


def test_model_non_deletion_is_overridden_when_rules_say_deletion() -> None:
    from ghost.agent.merge import reconcile_intent

    ctx = make_context()  # request text "Please delete my account." -> rule says DELETION
    verdict = CaseVerdict(
        case_id="case_t", intent=Intent.NOT_DELETION, reason="it is an export request", resources=[]
    )
    out, signals = reconcile_intent(verdict, ctx.portfolio)
    assert out.intent == Intent.DELETION and signals.endswith("-> rules kept")
    assert "deterministic signals say deletion" in out.reason


def test_agreement_leaves_no_signal_record() -> None:
    from ghost.agent.merge import reconcile_intent

    ctx = make_context()
    verdict = CaseVerdict(case_id="case_t", intent=Intent.DELETION, resources=[])
    assert reconcile_intent(verdict, ctx.portfolio)[1] == ""


def test_uncited_state_is_completed_or_reverted_from_the_plan() -> None:
    same, notes = _harden(_rv("account_1", Membership.YES, Status.PRESENT, Treatment.DELETE, ""))
    assert same["account_1"].evidence_id == "evidence_a"
    assert any("evidence filled from the plan" in n for n in notes)
    other, notes = _harden(_rv("account_1", Membership.YES, Status.ABSENT, Treatment.DELETE, ""))
    assert (
        other["account_1"].status == Status.PRESENT
        and other["account_1"].evidence_id == "evidence_a"
    )
    assert any("uncited status ABSENT reverted to plan" in n for n in notes)


def test_screen_table_rows_found_by_the_agent_are_not_asserted() -> None:
    from datetime import UTC, datetime

    from ghost.agent.merge import harden
    from ghost.data.observations import Identifiers, Observation, SourceFormat

    ctx = make_context()
    row_only = Observation(
        evidence_id="evidence_a",
        source_format=SourceFormat.IMAGE,
        captured_at=datetime(2026, 9, 5, tzinfo=UTC),
        system="security_service",
        resource_id="audit_record_7",
        identifiers=Identifiers(),
        present=True,
        state={},
    )
    ctx.index.insert(row_only, file_captured_at=row_only.captured_at)
    verdict = CaseVerdict(
        case_id="case_t",
        intent=Intent.DELETION,
        resources=[
            _rv("audit_record_7", Membership.YES, Status.PRESENT, Treatment.RETAIN, "evidence_a")
        ],
    )
    out, notes = harden(verdict, ctx.portfolio, ctx.index)
    by_id = {r.resource_id: r for r in out.resources}
    assert by_id["audit_record_7"].is_subject == Membership.UNCERTAIN
    assert any("context only" in n for n in notes)
