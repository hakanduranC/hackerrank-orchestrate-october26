"""Index tests: exact matching, time cutoff, history order, absence proofs, links."""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.data.index import EvidenceIndex
from ghost.data.observations import Identifiers, Observation, SourceFormat


def _obs(evidence: str, day: int, resource: str, system: str, **kw: object) -> Observation:
    ids = Identifiers()
    for email in kw.pop("emails", []):
        ids.add_email(str(email))
    for uid in kw.pop("user_ids", []):
        ids.add_user_id(str(uid))
    return Observation(
        evidence_id=evidence,
        source_format=SourceFormat(kw.pop("fmt", "json")),
        captured_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
        system=system,
        resource_id=resource,
        identifiers=ids,
        present=bool(kw.pop("present", True)),
        related_resources=list(kw.pop("related", [])),
        location=str(kw.pop("location", "")),
    )


def _subject(*emails: str) -> Identifiers:
    ids = Identifiers()
    for email in emails:
        ids.add_email(email)
    return ids


def test_exact_match_rejects_near_miss_and_respects_cutoff() -> None:
    index = EvidenceIndex.in_memory(
        [
            _obs("e1", 1, "search_1", "search_service", emails=["g.silva76@gmail.com"]),
            _obs("e2", 2, "search_2", "search_service", emails=["g.silva76.jr@gmail.com"]),
            _obs("e3", 20, "search_1", "search_service", emails=["G.Silva76@gmail.com"]),
        ]
    )
    found = index.find_by_identifiers(_subject("g.silva76@gmail.com"))
    assert [o.evidence_id for o in found] == ["e3", "e1"]  # newest first, decoy excluded
    cut = index.find_by_identifiers(
        _subject("g.silva76@gmail.com"), before=datetime(2026, 9, 10, tzinfo=UTC)
    )
    assert [o.evidence_id for o in cut] == ["e1"]


def test_history_links_and_absence_proofs() -> None:
    index = EvidenceIndex.in_memory(
        [
            _obs(
                "e1",
                1,
                "account_1",
                "account_service",
                emails=["a@x.com"],
                fmt="sql",
                location="customer_profiles_primary",
            ),
            _obs(
                "e2",
                5,
                "account_9",
                "account_service",
                fmt="sql",
                location="customer_profiles_primary",
            ),
            _obs(
                "e2",
                5,
                "account_1",
                "account_service",
                fmt="sql",
                location="customer_profiles_failover_replica",
            ),
            _obs(
                "e3",
                9,
                "search_1",
                "search_service",
                emails=["a@x.com"],
                fmt="sql",
                location="search_documents_primary",
                related=["account_1"],
            ),
        ]
    )
    assert [o.evidence_id for o in index.history("account_1")] == ["e2", "e1"]
    linked = index.find_by_identifiers(_subject("a@x.com"))
    assert {r for o in linked for r in o.related_resources} == {"account_1"}
    proofs = index.absence_proofs("account_1", "account_service")
    assert [(p.evidence_id, p.table) for p in proofs] == [("e2", "customer_profiles_primary")]
    # account_9 is absent from the day-1 primary snapshot but present on day 5
    assert [p.evidence_id for p in index.absence_proofs("account_9", "account_service")] == ["e1"]
    assert (
        index.absence_proofs(
            "account_1", "account_service", before=datetime(2026, 9, 3, tzinfo=UTC)
        )
        == []
    )


def test_file_mentions_text_matches_whole_ids_only() -> None:
    from datetime import UTC, datetime

    from ghost.data.index import EvidenceIndex
    from ghost.data.loaders import EvidenceFile

    f = EvidenceFile("evidence_x", "x", "o", datetime(2026, 9, 1, tzinfo=UTC))
    ix = EvidenceIndex.in_memory(
        [], files=[f], texts={"evidence_x": "row account_10 user_12 a_b%c\n"}
    )
    assert not ix.file_mentions_text("evidence_x", "account_1")
    assert ix.file_mentions_text("evidence_x", "account_10")
    assert not ix.file_mentions_text("evidence_x", "user_1")
    assert ix.file_mentions_text("evidence_x", "a_b%c")  # wildcards escaped, literal match
