"""Composer rules: phase/operation/system/resource ordering, numbering, evidence rules."""

from __future__ import annotations

from ghost.agent.schemas import (
    CaseVerdict,
    Intent,
    Membership,
    ResourceVerdict,
    Status,
    Treatment,
)
from ghost.compose import compose


def _r(
    system: str,
    rid: str,
    status: Status,
    treatment: Treatment,
    ev: str = "e1",
    member: Membership = Membership.YES,
) -> ResourceVerdict:
    return ResourceVerdict(
        system_id=system,
        resource_id=rid,
        is_subject=member,
        status=status,
        treatment=treatment,
        evidence_id=ev,
    )


def test_not_deletion_is_not_applicable() -> None:
    result = compose(
        CaseVerdict(
            case_id="c",
            intent=Intent.NOT_DELETION,
            resources=[_r("account_service", "account_1", Status.PRESENT, Treatment.DELETE)],
        )
    )
    assert result.deletion_status == "NOT_APPLICABLE" and result.steps == []


def test_incomplete_case_matches_sample_shape() -> None:
    # The shape of sample case_11: four violations plus a deleted account and a retained record.
    verdict = CaseVerdict(
        case_id="c",
        intent=Intent.DELETION,
        resources=[
            _r("support_platform", "support_864", Status.IDENTIFIABLE, Treatment.REDACT, "e3"),
            _r("search_service", "search_052", Status.PRESENT, Treatment.DELETE, "e3"),
            _r("integration_service", "data_flow_9522", Status.ACTIVE, Treatment.DISABLE, "e3"),
            _r(
                "analytics_platform",
                "analytics_2034",
                Status.IDENTIFIABLE,
                Treatment.ANONYMIZE,
                "e3",
            ),
            _r("security_service", "audit_record_9394", Status.PRESENT, Treatment.RETAIN, "e3"),
            _r("account_service", "account_4248", Status.ABSENT, Treatment.DELETE, "e3"),
            _r(
                "account_service",
                "account_9999",
                Status.ABSENT,
                Treatment.DELETE,
                "e3",
                Membership.NO,
            ),
        ],
    )
    result = compose(verdict)
    assert result.deletion_status == "INCOMPLETE"
    rows = [(s.step_no, s.phase, s.operation, s.resource_id, s.evidence_id) for s in result.steps]
    assert rows == [
        (1, "FINDING", "REPORT_REMAINING_RECORD", "search_052", "e3"),
        (2, "FINDING", "REPORT_EXPOSED_PII", "analytics_2034", "e3"),
        (3, "FINDING", "REPORT_EXPOSED_PII", "support_864", "e3"),
        (4, "FINDING", "REPORT_ACTIVE_DATA_FLOW", "data_flow_9522", "e3"),
        (5, "REMEDIATION", "DISABLE_DATA_FLOW", "data_flow_9522", ""),
        (6, "REMEDIATION", "DELETE_RECORD", "search_052", ""),
        (7, "REMEDIATION", "REDACT_RECORD", "support_864", ""),
        (8, "REMEDIATION", "ANONYMIZE_RECORD", "analytics_2034", ""),
        (9, "VERIFICATION", "VERIFY_RECORD_ABSENT", "account_4248", ""),
        (10, "VERIFICATION", "VERIFY_RECORD_ABSENT", "search_052", ""),
        (11, "VERIFICATION", "VERIFY_RECORD_PRESENT", "audit_record_9394", ""),
        (12, "VERIFICATION", "VERIFY_RECORD_REDACTED", "support_864", ""),
        (13, "VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "analytics_2034", ""),
        (14, "VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "data_flow_9522", ""),
    ]
    assert all(s.state for s in result.steps)


def test_complete_case_cites_evidence_and_drops_unprovable() -> None:
    verdict = CaseVerdict(
        case_id="c",
        intent=Intent.DELETION,
        resources=[
            _r("account_service", "account_1", Status.ABSENT, Treatment.DELETE, "e54"),
            _r("analytics_platform", "analytics_1", Status.ANONYMIZED, Treatment.ANONYMIZE, "e56"),
            _r("backup_service", "backup_1", Status.BLOCKED, Treatment.BLOCK, "e9"),
            _r(
                "search_service", "search_1", Status.UNKNOWN, Treatment.DELETE, ""
            ),  # nothing proves it
        ],
    )
    result = compose(verdict)
    assert result.deletion_status == "COMPLETE"
    assert [(s.operation, s.resource_id, s.evidence_id) for s in result.steps] == [
        ("VERIFY_RECORD_ABSENT", "account_1", "e54"),
        ("VERIFY_RECORD_ANONYMIZED", "analytics_1", "e56"),
        ("VERIFY_RESTORE_BLOCKED", "backup_1", "e9"),
    ]


def test_uncertain_candidates_only_when_asked() -> None:
    verdict = CaseVerdict(
        case_id="c",
        intent=Intent.DELETION,
        resources=[
            _r(
                "account_service",
                "account_1",
                Status.ABSENT,
                Treatment.DELETE,
                "e1",
                Membership.UNCERTAIN,
            ),
        ],
    )
    assert compose(verdict).steps == []
    assert len(compose(verdict, include_uncertain=True).steps) == 1


def test_retained_record_proven_absent_yields_no_step() -> None:
    from ghost.agent.schemas import Membership, ResourceVerdict, Status, Treatment
    from ghost.compose import plan_for

    rv = ResourceVerdict(
        system_id="security_service",
        resource_id="audit_record_1",
        is_subject=Membership.YES,
        status=Status.ABSENT,
        treatment=Treatment.RETAIN,
        evidence_id="evidence_a",
    )
    plan = plan_for(rv)
    assert plan.finding is None and plan.remediation is None and plan.verification is None
