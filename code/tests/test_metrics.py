"""Metric tests on a known difference, plus the baseline intent rule on real phrasings."""

from __future__ import annotations

import json

from ghost.agent.baseline import classify_intent
from ghost.agent.schemas import Intent
from ghost.evaluation.metrics import score_case, summarize


def _steps(*rows: tuple[str, str, str, str, str, str]) -> str:
    return json.dumps(
        [
            {
                "step_no": i,
                "phase": p,
                "system_id": s,
                "resource_id": r,
                "operation": o,
                "state": st,
                "evidence_id": e,
            }
            for i, (p, s, r, o, st, e) in enumerate(rows, start=1)
        ]
    )


def test_score_counts_matches_fields_and_sequence() -> None:
    expected = _steps(
        ("FINDING", "search_service", "search_1", "REPORT_REMAINING_RECORD", "PRESENT", "e1"),
        ("VERIFICATION", "account_service", "account_1", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    )
    predicted = _steps(
        (
            "FINDING",
            "search_service",
            "search_1",
            "REPORT_REMAINING_RECORD",
            "PRESENT",
            "e9",
        ),  # wrong ev
        ("VERIFICATION", "account_service", "account_1", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        (
            "VERIFICATION",
            "backup_service",
            "backup_1",
            "VERIFY_RESTORE_BLOCKED",
            "BLOCKED",
            "",
        ),  # extra
    )
    score = score_case("c", "INCOMPLETE", expected, "INCOMPLETE", predicted)
    assert (score.tp, score.fp, score.fn) == (1, 2, 1)
    assert (score.field_ok, score.field_total) == (3, 4)  # state ok twice, evidence ok once
    assert score.status_ok and not score.sequence_ok
    summary = summarize([score, score_case("d", "COMPLETE", "[]", "COMPLETE", "[]")])
    assert summary.status_accuracy == 1.0 and summary.sequence_accuracy == 0.5
    assert round(summary.step_precision, 3) == round(1 / 3, 3)


def test_intent_rule_on_real_phrasings() -> None:
    deletion = [
        "Please permanently delete the account associated with this email address.",
        "Je demande la suppression définitive du compte associé à cette adresse e-mail.",
        "Solicito a exclusão permanente da conta associada a este e-mail.",
        "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.",
        "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
        "I no longer use this account and would like it permanently deleted.",
        "Hi, I haven't used this account in years and would like it closed.",
    ]
    not_deletion = [
        "Please remove me from marketing lists only. I still want receipts for this account.",
        "Please stop the promotional emails. I still need the account and my order history.",
        "Could you send me a copy of the information held on my account? I still use it.",
        "Please change the email address used for this account.",
        "I can't access my profile and need help resetting the login.",
    ]
    assert all(classify_intent(t) == Intent.DELETION for t in deletion)
    assert all(classify_intent(t) == Intent.NOT_DELETION for t in not_deletion)
