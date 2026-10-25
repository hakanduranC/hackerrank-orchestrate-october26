"""Small hand-built case for guard and hardening tests.

Subject user_1 (a@x.com) has a profile ``account_1`` linked by identifier, a
window-picked ``analytics_1`` and a batch candidate ``analytics_2``. Two files
exist: ``evidence_a`` (captured before audit_at) and ``evidence_late`` (after).
``account_9`` belongs to someone else and appears only in ``evidence_a``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.agent.tools import CaseContext
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import DeletionRequest, EvidenceFile
from ghost.data.observations import Identifiers, Observation, SourceFormat
from ghost.identity import Subject
from ghost.policy import Treatment
from ghost.portfolio import Portfolio, ResourceView

AUDIT_AT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
EARLY = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
LATE = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

REQUEST = DeletionRequest(
    case_id="case_t",
    email="a@x.com",
    requested_at=datetime(2026, 9, 1, tzinfo=UTC),
    reported_status="COMPLETE",
    reported_at=datetime(2026, 9, 3, tzinfo=UTC),
    audit_at=AUDIT_AT,
    request_text="Please delete my account.",
)
SUBJECT = Subject(
    user_id="user_1",
    name="A",
    current_email="a@x.com",
    identifiers=Identifiers(user_ids={"user_1"}, emails={"a@x.com"}),
)
FILES = [
    EvidenceFile("evidence_a", "evidence/a.json", "test", EARLY),
    EvidenceFile("evidence_late", "evidence/late.json", "test", LATE),
]
TEXTS = {
    "evidence_a": "account_1 a@x.com user_1\naccount_9 other@y.com user_9\nanalytics_1\n",
    "evidence_late": "account_1 a@x.com\n",
}


def _obs(rid: str, evidence_id: str, when: datetime, **ids: set[str]) -> Observation:
    return Observation(
        evidence_id=evidence_id,
        source_format=SourceFormat.JSON,
        captured_at=when,
        system=rid.rsplit("_", 1)[0] + "_service",
        resource_id=rid,
        identifiers=Identifiers(**ids),
        present=True,
        state={},
    )


OBSERVATIONS = [
    _obs("account_1", "evidence_a", EARLY, user_ids={"user_1"}, emails={"a@x.com"}),
    _obs("account_1", "evidence_late", LATE, emails={"a@x.com"}),
    _obs("account_9", "evidence_a", EARLY, user_ids={"user_9"}, emails={"other@y.com"}),
    _obs("analytics_1", "evidence_a", EARLY),
]


def _view(rid: str, category: str, action: str, link: str, status: str) -> ResourceView:
    return ResourceView(
        resource_id=rid,
        system=rid.rsplit("_", 1)[0] + "_service",
        category=category,
        treatment=Treatment(category, action, "rule_t", "Always"),
        workflow_action=None,
        link_kind=link,
        observations=[o for o in OBSERVATIONS if o.resource_id == rid],
        absence_proofs=[],
        timeline=[],
        status=status,
        direct_pii=[],
        recreated=False,
    )


def make_context() -> CaseContext:
    portfolio = Portfolio(
        request=REQUEST,
        subject=SUBJECT,
        resources=[
            _view("account_1", "USER_PROFILE", "DELETE", "identifier", "PRESENT"),
            _view("analytics_1", "ANALYTICS_PROFILE", "ANONYMIZE", "window", "ANONYMIZED"),
            _view("analytics_2", "ANALYTICS_PROFILE", "ANONYMIZE", "window-alternative", "UNKNOWN"),
            _view("audit_record_1", "SECURITY_AUDIT_LOG", "RETAIN", "identifier", "UNKNOWN"),
        ],
        notes=[],
    )
    index = EvidenceIndex.in_memory(OBSERVATIONS, files=FILES, texts=TEXTS)
    return CaseContext(portfolio, index, [], AUDIT_AT)
