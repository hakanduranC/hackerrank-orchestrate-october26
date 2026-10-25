"""Subject resolution: request email -> user_id -> every identifier the person ever had.

The request email may be current (``user_identities.csv``) or historical
(an ``old_value`` in ``user_audit_log.csv``, possibly several renames back).
Old identifiers still appear in older systems, so the subject's search set is
the union of the current snapshot and every historical value in the log.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghost.data.loaders import AuditEvent, Dataset, Identity
from ghost.data.observations import Identifiers


@dataclass(frozen=True)
class Subject:
    """A resolved person: stable user_id, current email, every identifier past and present, how the
    request email matched.
    """

    user_id: str
    name: str
    current_email: str
    identifiers: Identifiers
    resolved_via: str = "current"


class IdentityResolver:
    """Index of current identities and historical identifier changes."""

    def __init__(self, dataset: Dataset) -> None:
        self._by_user: dict[str, Identity] = {i.user_id: i for i in dataset.identities}
        self._current_email: dict[str, str] = {
            Identifiers.norm_email(i.email): i.user_id for i in dataset.identities
        }
        self._historical_email: dict[str, str] = {}
        self._events_by_user: dict[str, list[AuditEvent]] = {}
        for event in dataset.audit_log:
            self._events_by_user.setdefault(event.user_id, []).append(event)
            if event.field_name == "email":
                for value in (event.old_value, event.new_value):
                    self._historical_email.setdefault(Identifiers.norm_email(value), event.user_id)

    def resolve(self, email: str) -> Subject | None:
        """The subject a request email belongs to (current or historical), or None."""
        key = Identifiers.norm_email(email)
        via = "current"
        user_id = self._current_email.get(key)
        if user_id is None:
            user_id = self._historical_email.get(key)
            via = "historical"
        if user_id is None:
            return None
        return self.subject(user_id, via)

    def subject(self, user_id: str, via: str = "current") -> Subject:
        """The subject for a user_id."""
        identity = self._by_user[user_id]
        ids = Identifiers()
        ids.add_user_id(user_id)
        ids.add_email(identity.email)
        ids.add_phone(identity.phone)
        ids.add_hash(identity.email_hash)
        events = self._events_by_user.get(user_id, [])
        for event in events:
            for value in (event.old_value, event.new_value):
                if event.field_name == "email":
                    ids.add_email(value)
                elif event.field_name == "phone":
                    ids.add_phone(value)
                elif event.field_name == "email_hash":
                    ids.add_hash(value)
        return Subject(
            user_id=user_id,
            name=identity.name,
            current_email=identity.email,
            identifiers=ids,
            resolved_via=via,
        )
