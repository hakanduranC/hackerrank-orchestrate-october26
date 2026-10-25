"""The one shape every evidence format is parsed into.

An :class:`Observation` is "at time T, evidence E showed resource R in system S
with these identifiers and this state". SQL rows, JSON records, correlated log
traces, handover lines, PDF facts and vision extractions all become observations,
so everything downstream (index, portfolio, agent tools, guards) is format-agnostic.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from ghost.data.ids import EMAIL_RE as _EMAIL_RE  # noqa: E402

_PHONE_STRIP_RE = re.compile(r"[^\d+]")


class SourceFormat(StrEnum):
    """The evidence format an observation was parsed from."""

    SQL = "sql"
    JSON = "json"
    LOG = "log"
    TEXT = "text"
    PDF = "pdf"
    IMAGE = "image"


class Identifiers(BaseModel):
    """Normalized identifiers found on one observation. Matching is exact."""

    user_ids: set[str] = Field(default_factory=set)
    emails: set[str] = Field(default_factory=set)
    phones: set[str] = Field(default_factory=set)
    email_hashes: set[str] = Field(default_factory=set)

    @staticmethod
    def norm_email(value: str) -> str:
        # Sentence punctuation after an address in prose ("...@gmail.com.") is not part of it.
        """Lower-case, trailing sentence punctuation removed."""
        return value.strip().lower().rstrip(".,;:")

    @staticmethod
    def norm_phone(value: str) -> str:
        """Digits and the leading plus only."""
        return _PHONE_STRIP_RE.sub("", value)

    @staticmethod
    def norm_hash(value: str) -> str:
        """Lower-case hex."""
        return value.strip().lower()

    def add_email(self, value: str | None) -> None:
        """Add a value if it has the shape of an email address."""
        if value and _EMAIL_RE.fullmatch(self.norm_email(value)):
            self.emails.add(self.norm_email(value))

    def add_phone(self, value: str | None) -> None:
        """Add a value if it contains digits."""
        if value and any(ch.isdigit() for ch in value):
            self.phones.add(self.norm_phone(value))

    def add_hash(self, value: str | None) -> None:
        """Add a value if it is a 64-character hex digest."""
        if value and re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
            self.email_hashes.add(self.norm_hash(value))

    def add_user_id(self, value: str | None) -> None:
        """Add a value if it is a ``user_<n>`` id."""
        if value and re.fullmatch(r"user_\d+", value.strip()):
            self.user_ids.add(value.strip())

    def is_empty(self) -> bool:
        """True when no identifier of any kind is present."""
        return not (self.user_ids or self.emails or self.phones or self.email_hashes)

    def intersects(self, other: Identifiers) -> bool:
        """True when the two sets share at least one identifier of any kind."""
        return bool(
            self.user_ids & other.user_ids
            or self.emails & other.emails
            or self.phones & other.phones
            or self.email_hashes & other.email_hashes
        )


class Observation(BaseModel):
    """One evidence-backed fact about one resource at one point in time."""

    evidence_id: str
    source_format: SourceFormat
    captured_at: datetime
    system: str
    resource_id: str
    identifiers: Identifiers = Field(default_factory=Identifiers)
    present: bool = True
    state: dict[str, str | int | bool | None] = Field(default_factory=dict)
    related_resources: list[str] = Field(default_factory=list)
    location: str = ""
    excerpt: str = ""
