"""VERIFY guards: every claim must be anchored in what the tools showed."""

from __future__ import annotations

from agent_fixtures import make_context
from ghost.agent.guards import check_verdict, refusal_kind
from ghost.agent.schemas import CaseVerdict, Intent, Membership, ResourceVerdict, Status, Treatment


def _rv(rid: str, evidence: str, **kw: object) -> ResourceVerdict:
    base: dict[str, object] = {
        "system_id": rid.rsplit("_", 1)[0] + "_service",
        "resource_id": rid,
        "is_subject": Membership.YES,
        "status": Status.PRESENT,
        "treatment": Treatment.DELETE,
        "evidence_id": evidence,
    }
    base.update(kw)
    return ResourceVerdict(**base)  # type: ignore[arg-type]


def _verdict(*resources: ResourceVerdict, intent: Intent = Intent.DELETION) -> CaseVerdict:
    return CaseVerdict(case_id="case_t", intent=intent, resources=list(resources))


def test_grounded_claim_passes() -> None:
    ctx = make_context()
    assert check_verdict(_verdict(_rv("account_1", "evidence_a")), ctx) == []


def test_evidence_after_audit_at_is_rejected() -> None:
    ctx = make_context()
    errors = check_verdict(_verdict(_rv("account_1", "evidence_late")), ctx)
    assert any("after audit_at" in e for e in errors)


def test_activity_rows_are_claims_not_state() -> None:
    ctx = make_context()
    errors = check_verdict(_verdict(_rv("account_1", "activity_42")), ctx)
    assert any("claim, not state" in e for e in errors)


def test_resource_never_shown_by_a_tool_is_rejected() -> None:
    ctx = make_context()
    errors = check_verdict(_verdict(_rv("search_777", "evidence_a")), ctx)
    assert any("never returned by any tool" in e for e in errors)


def test_agent_found_record_needs_a_subject_identifier_in_the_source() -> None:
    ctx = make_context()
    ctx.note("search_evidence", "other", "evidence_a:2 account_9 other@y.com user_9")
    errors = check_verdict(_verdict(_rv("account_9", "evidence_a")), ctx)
    # evidence_a names the subject elsewhere, so the file-level check passes; the record
    # itself carries another person's identifiers and is accepted only on file mention.
    assert errors == []


def test_agent_found_record_in_a_file_without_the_subject_is_rejected() -> None:
    ctx = make_context()
    ctx.index.file_mentions_subject = lambda *_: False  # type: ignore[method-assign]
    ctx.index.file_mentions_text = (  # type: ignore[method-assign]
        lambda fid, term: term == "account_9"
    )
    ctx.note("search_evidence", "x", "evidence_a:2 account_9")
    errors = check_verdict(_verdict(_rv("account_9", "evidence_a")), ctx)
    assert any("ties account_9 to no identifier of the subject" in e for e in errors)


def test_delete_on_retained_category_is_rejected() -> None:
    ctx = make_context()
    errors = check_verdict(
        _verdict(_rv("audit_record_1", "", status=Status.UNKNOWN, treatment=Treatment.DELETE)),
        ctx,
    )
    assert any("retained category" in e for e in errors)


def test_not_deletion_must_have_no_resources() -> None:
    ctx = make_context()
    errors = check_verdict(
        _verdict(_rv("account_1", "evidence_a"), intent=Intent.NOT_DELETION), ctx
    )
    assert errors == ["NOT_DELETION verdicts must have an empty resources list"]


def test_duplicates_are_rejected() -> None:
    ctx = make_context()
    errors = check_verdict(
        _verdict(_rv("account_1", "evidence_a"), _rv("account_1", "evidence_a")), ctx
    )
    assert any("listed twice" in e for e in errors)


def test_wrong_system_is_corrected() -> None:
    ctx = make_context()
    errors = check_verdict(
        _verdict(_rv("account_1", "evidence_a", system_id="search_service")), ctx
    )
    assert any("system_id should be account_service" in e for e in errors)


def test_citation_of_a_file_that_does_not_exist_is_refused_and_classified() -> None:
    ctx = make_context()
    errors = check_verdict(_verdict(_rv("account_1", "evidence_999")), ctx)
    assert errors and refusal_kind(errors[0]) == "hallucinated_file"
    assert (
        refusal_kind("x: evidence_a was captured after audit_at and cannot be cited")
        == "post_audit_evidence"
    )
    assert (
        refusal_kind(
            "x: activity_1 is a workflow / audit-log row, which is a claim, not state; cite..."
        )
        == "activity_as_state"
    )


def test_zero_hit_search_does_not_whitelist_the_term() -> None:
    ctx = make_context()
    ctx.note("search_evidence", "account_999", "0 hit(s) for 'account_999'\nno match", "")
    assert "account_999" not in ctx.seen_resources
    ctx.note(
        "search_evidence",
        "account_9",
        "1 hit(s)\nevidence_a:2 account_9 other@y.com",
        "evidence_a:2 account_9 other@y.com",
    )
    assert "account_9" in ctx.seen_resources and "evidence_a" in ctx.seen_evidence


def test_cited_file_must_have_been_shown_by_a_tool() -> None:
    ctx = make_context()
    # evidence_zz is a valid in-window file that mentions the record, but no tool showed it
    # and it is not among the plan's files for account_1.
    ctx.index._conn.execute(
        "INSERT INTO files VALUES (?,?,?,?)",
        ("evidence_zz", "evidence/zz.json", "test", "2026-09-05T12:00:00+00:00"),
    )
    ctx.index._insert_raw_lines("evidence_zz", "account_1 a@x.com")
    errors = check_verdict(_verdict(_rv("account_1", "evidence_zz")), ctx)
    assert errors and refusal_kind(errors[0]) == "unseen_file"
    ctx.note(
        "read_evidence",
        "evidence_zz:1",
        "evidence/zz.json\n1: account_1 a@x.com",
        "evidence_zz\n1: account_1 a@x.com",
    )
    assert check_verdict(_verdict(_rv("account_1", "evidence_zz")), ctx) == []


def test_status_must_fit_the_treatment() -> None:
    ctx = make_context()
    errors = check_verdict(_verdict(_rv("account_1", "evidence_a", status=Status.ACTIVE)), ctx)
    assert any(refusal_kind(e) == "status_treatment_mismatch" for e in errors)
