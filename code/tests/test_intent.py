"""Intent: the text rule and the intake classification signal."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_fixtures import make_context
from ghost.agent.baseline import baseline_verdict, classify_intent
from ghost.agent.prompts import case_message
from ghost.agent.schemas import Intent
from ghost.portfolio import ActivityEvent

TAG = "Privacy intake classified request for account_1 as non-deletion; no destructive action was authorized."


def test_text_rule_catches_english_non_deletion() -> None:
    assert (
        classify_intent("Please send me a copy of my data and keep the account.")
        == Intent.NOT_DELETION
    )
    assert classify_intent("Delete my account and everything you hold about me.") == Intent.DELETION


def test_intake_tag_is_language_independent() -> None:
    spanish_copy = "Quiero una copia de los datos de mi cuenta."  # no negation, no scoping pattern
    assert classify_intent(spanish_copy) == Intent.DELETION  # the text rules cannot tell
    assert classify_intent(spanish_copy, intake=TAG) == Intent.NOT_DELETION


def test_multilingual_negation_and_out_of_scope() -> None:
    assert classify_intent("계정은 삭제하지 말고 그대로 유지해 주세요.") == Intent.NOT_DELETION
    assert (
        classify_intent("Where is my order ORD-1? Tracking has not updated.") == Intent.NOT_DELETION
    )
    assert (
        classify_intent(
            "Please confirm that the profile is gone and remaining records cannot identify me."
        )
        == Intent.DELETION
    )


def test_portfolio_exposes_the_intake_tag_and_the_baseline_uses_it() -> None:
    ctx = make_context()
    account = ctx.portfolio.by_id("account_1")
    assert account is not None
    account.timeline.append(
        ActivityEvent(
            "activity_9",
            datetime(2026, 9, 2, tzinfo=UTC),
            "CLASSIFY_REQUEST",
            "privacy_intake",
            "SUCCESS",
            TAG,
        )
    )
    tag = ctx.portfolio.intake_classification()
    assert tag is not None and tag.activity_id == "activity_9"
    assert baseline_verdict(ctx.portfolio).intent == Intent.NOT_DELETION
    assert "privacy intake classification (claim, activity_9" in str(
        case_message(ctx.portfolio).content
    )
