"""Identity tests: current email, historical chain of renames, full identifier set."""

from __future__ import annotations

from datetime import UTC, datetime

from ghost.data.loaders import AuditEvent, Dataset, Identity
from ghost.identity import IdentityResolver

T = datetime(2026, 1, 1, tzinfo=UTC)


def _dataset() -> Dataset:
    identities = [
        Identity(
            "user_10", "Nadia Petrescu", "n.petrescu@yahoo.com", "+40 723 481 638", "a" * 64, T
        ),
        Identity(
            "user_11", "Someone Else", "nadia.popescu@yahoo.com", "+40 700 000 000", "b" * 64, T
        ),
    ]
    audit = [
        AuditEvent(
            "audit_1", "user_10", T, "email", "nadia.ionescu@yahoo.com", "nadia.petrescu@yahoo.com"
        ),
        AuditEvent("audit_2", "user_10", T, "email_hash", "c" * 64, "d" * 64),
        AuditEvent(
            "audit_3", "user_10", T, "email", "nadia.petrescu@yahoo.com", "n.petrescu@yahoo.com"
        ),
        AuditEvent("audit_4", "user_10", T, "phone", "+40 711 111 111", "+40 723 481 638"),
    ]
    return Dataset([], [], [], identities, audit, [], [], [])


def test_current_and_two_rename_chain() -> None:
    resolver = IdentityResolver(_dataset())
    current = resolver.resolve("N.Petrescu@yahoo.com")
    assert current and current.user_id == "user_10" and current.resolved_via == "current"
    old = resolver.resolve("nadia.ionescu@yahoo.com")  # two renames back
    assert old and old.user_id == "user_10" and old.resolved_via == "historical"
    assert old.identifiers.emails == {
        "nadia.ionescu@yahoo.com",
        "nadia.petrescu@yahoo.com",
        "n.petrescu@yahoo.com",
    }
    assert old.identifiers.phones == {"+40723481638", "+40711111111"}
    assert old.identifiers.email_hashes == {"a" * 64, "c" * 64, "d" * 64}
    assert old.identifiers.user_ids == {"user_10"}


def test_decoy_and_unknown() -> None:
    resolver = IdentityResolver(_dataset())
    assert (
        resolver.resolve("nadia.popescu@yahoo.com").user_id == "user_11"
    )  # similar name, other person
    assert resolver.resolve("nobody@example.com") is None
