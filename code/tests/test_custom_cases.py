"""Constructed cases: they materialise from real subjects and carry checkable expectations."""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.data.loaders import Dataset, DeletionRequest, SampleAnswer
from ghost.evaluation.custom_cases import CUSTOM_CASES, expectation, materialise

T = datetime(2026, 9, 10, tzinfo=UTC)


def _data() -> Dataset:
    base = DeletionRequest("case_27", "subject@example.com", T, "COMPLETE", T, T, "base text")
    other = DeletionRequest("case_14", "y.tanaka32@gmail.com", T, "COMPLETE", T, T, "base text")
    real = DeletionRequest(
        "case_50", "darwish.omar@icloud.com", T, "NOT_STARTED", T, T, "base text"
    )
    return Dataset(
        requests=[real],
        sample_requests=[base, other],
        sample_answers=[
            SampleAnswer("case_27", "COMPLETE", "[]"),
            SampleAnswer("case_14", "INCOMPLETE", "[]"),
        ],
        identities=[],
        audit_log=[],
        activity=[],
        rules=[],
        evidence_files=[],
    )


def test_cases_materialise_from_their_base_request() -> None:
    data = _data()
    for case in CUSTOM_CASES:
        req = materialise(data, case)
        assert req.case_id == case.case_id
        assert req.email  # subject kept unless overridden
        if case.request_text:
            assert req.request_text == case.request_text
        else:
            assert req.request_text == "base text"


def test_every_case_has_a_checkable_expectation() -> None:
    data = _data()
    for case in CUSTOM_CASES:
        has_key = expectation(data, case) is not None
        assert has_key or case.must_not_mention or case.expected_status, case.case_id


def test_at_least_three_cases_cover_distinct_risks() -> None:
    purposes = {c.purpose for c in CUSTOM_CASES}
    assert len(CUSTOM_CASES) >= 3 and len(purposes) == len(CUSTOM_CASES)
