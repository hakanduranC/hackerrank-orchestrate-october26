"""Deletion policy: ``deletion_rules.csv`` -> data category -> required treatment.

The category of a record is derived from what the evidence says about it
(readback ``record_type``, the collection it came from) and, failing that, from
its resource-id prefix. The treatment vocabulary matches the output contract:
DELETE, REDACT, ANONYMIZE, RETAIN, plus DISABLE / BLOCK for the two recreation
paths (active data flows, restorable backups), which the rules describe but do
not name as categories.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghost.data.loaders import DeletionRule
from ghost.data.observations import Observation

# Resource-id prefix -> data category when nothing more specific is known.
PREFIX_CATEGORY: dict[str, str] = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "cache": "CACHE_ENTRY",
    "support": "SUPPORT_MESSAGE",
    "object": "SUPPORT_ATTACHMENT",
    "analytics": "ANALYTICS_PROFILE",
    "export": "USER_EXPORT",
    "marketing": "MARKETING_PROFILE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "data_flow": "DATA_FLOW",
}

# Collections that pin the support sub-category.
ATTACHMENT_COLLECTIONS = {"ticket_attachments"}

# Treatments for the two recreation paths, which have no rule row of their own.
RECREATION_TREATMENT: dict[str, str] = {"DATA_FLOW": "DISABLE", "BACKUP_ARCHIVE": "BLOCK"}

RETAINED_CATEGORIES = frozenset({"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE"})


@dataclass(frozen=True)
class Treatment:
    """The required action for a data category and the rule and condition it comes from."""

    category: str
    action: str
    rule_id: str
    condition: str


class Policy:
    """Lookup of the required treatment per data category."""

    def __init__(self, rules: list[DeletionRule]) -> None:
        self._by_category: dict[str, DeletionRule] = {r.data_category: r for r in rules}

    def treatment(self, category: str) -> Treatment:
        """The treatment for a category (RETAIN-style defaults for recreation paths)."""
        if category in RECREATION_TREATMENT:
            rule = self._by_category.get("BACKUP_ARCHIVE") if category == "BACKUP_ARCHIVE" else None
            return Treatment(
                category=category,
                action=RECREATION_TREATMENT[category],
                rule_id=rule.rule_id if rule else "",
                condition=rule.condition if rule else "An active process can recreate the data",
            )
        rule = self._by_category.get(category)
        if rule is None:
            return Treatment(category=category, action="DELETE", rule_id="", condition="")
        return Treatment(category, rule.required_action, rule.rule_id, rule.condition)


def category_of(resource_id: str, observations: list[Observation]) -> str:
    """Best category for a resource from its evidence, else from its id prefix."""
    for obs in observations:
        record_type = obs.state.get("record_type")
        if isinstance(record_type, str) and record_type:
            return record_type
        collection = obs.state.get("collection") or obs.location.rsplit("/", 1)[-1]
        if (
            collection in ATTACHMENT_COLLECTIONS
            or obs.state.get("file_name")
            or obs.state.get("document_kind") == "attachment"
        ):
            return "SUPPORT_ATTACHMENT"
    prefix = resource_id.rsplit("_", 1)[0]
    return PREFIX_CATEGORY.get(prefix, "UNKNOWN")
