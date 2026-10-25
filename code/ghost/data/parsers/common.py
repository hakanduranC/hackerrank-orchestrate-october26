"""Shared helpers for the evidence parsers.

Two conventions are implemented here so every format agrees on them:

* **Identifier extraction** scans every string value of a record and classifies
  it by shape (email / phone / 64-hex hash / ``user_N``). Names are deliberately
  not identifiers: matching on names is how decoys get in.
* **Readback records** (``system, resource_id, record_type, result, subject``)
  appear in JSON, TXT and log files with the same vocabulary; they map to one
  observation shape via :func:`readback_observation`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ghost.data.loaders import parse_ts
from ghost.data.observations import Identifiers, Observation, SourceFormat

ResourceState = dict[str, str | int | bool | None]

from ghost.data.ids import RESOURCE_ID_RE as _RESOURCE_RE  # noqa: E402

# Table / collection names -> the system that owns the record.
COLLECTION_SYSTEM: dict[str, str] = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "customer_analytics_reports": "analytics_platform",
    "support_records": "support_platform",
    "support_conversations": "support_platform",
    "ticket_attachments": "support_platform",
    "customer_exports": "export_service",
    "customer_export_jobs": "export_service",
    "connector_subscriptions": "integration_service",
    "connector_configurations": "integration_service",
    "backup_restore_entries": "backup_service",
    "backup_catalog": "backup_service",
    "security_audit_records": "security_service",
    "security_audit_events": "security_service",
    "deleted_customer_references": "identity_service",
    "retained_order_records": "legal_service",
}

# Resource-id prefix -> system, used when a record carries no explicit system.
PREFIX_SYSTEM: dict[str, str] = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "object": "object_storage",
}

# Readback results that mean "the record is not there".
ABSENT_RESULTS = {"NOT_FOUND", "ABSENT", "MISS"}


def system_for(resource_id: str, explicit: str | None = None, collection: str | None = None) -> str:
    """Resolve the owning system: explicit field > collection name > id prefix."""
    if explicit:
        return explicit
    if collection and collection in COLLECTION_SYSTEM:
        return COLLECTION_SYSTEM[collection]
    prefix = resource_id.rsplit("_", 1)[0]
    return PREFIX_SYSTEM.get(prefix, "unknown")


def find_resource_ids(text: str) -> list[str]:
    """All resource ids mentioned in a string, in order of appearance, unique."""
    seen: list[str] = []
    for match in _RESOURCE_RE.finditer(text):
        if match.group(0) not in seen:
            seen.append(match.group(0))
    return seen


def classify_value(value: str, into: Identifiers) -> None:
    """Add ``value`` to the right identifier bucket if it has an identifier shape."""
    stripped = value.strip()
    if not stripped:
        return
    into.add_user_id(stripped)
    into.add_hash(stripped)
    into.add_email(stripped)
    if stripped.startswith("+") and sum(ch.isdigit() for ch in stripped) >= 7:
        into.add_phone(stripped)


def identifiers_from(record: object) -> Identifiers:
    """Scan every string value in a (possibly nested) record for identifiers."""
    found = Identifiers()
    _walk(record, found)
    return found


def _walk(node: object, into: Identifiers) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("full_name", "name", "display_name", "requester_name"):
                continue
            _walk(value, into)
    elif isinstance(node, list):
        for item in node:
            _walk(item, into)
    elif isinstance(node, str):
        classify_value(node, into)
        # Identifiers embedded in free text (PDF lines, message bodies, log details).
        for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", node):
            into.add_email(email)
        for user_id in re.findall(r"\buser_\d+\b", node):
            into.add_user_id(user_id)
        for digest in re.findall(r"\b[0-9a-fA-F]{64}\b", node):
            into.add_hash(digest)
        for phone in re.findall(r"(?<![\w+])\+\d{7,15}\b", node):
            into.add_phone(phone)


def related_resources(record: dict[str, Any], own_id: str) -> list[str]:
    """Other resource ids a record points at (e.g. a search document's source account)."""
    related: list[str] = []
    for key, value in record.items():
        if isinstance(value, str) and key != "resource_id" and key.endswith("resource_id"):
            if value != own_id and value not in related:
                related.append(value)
    return related


def flat_state(record: dict[str, Any]) -> ResourceState:
    """Keep scalar fields of a record as the observation's state."""
    state: ResourceState = {}
    for key, value in record.items():
        if isinstance(value, bool | int | str) or value is None:
            state[key] = value
        elif isinstance(value, float):
            state[key] = str(value)
    return state


def to_datetime(value: str | None, fallback: datetime) -> datetime:
    """Parse an ISO timestamp if present, else return the fallback."""
    if not value:
        return fallback
    try:
        return parse_ts(value)
    except ValueError:
        return fallback


def readback_observation(
    *,
    evidence_id: str,
    source_format: SourceFormat,
    captured_at: datetime,
    system: str | None,
    resource_id: str,
    record_type: str | None,
    result: str | None,
    subject: str | None,
    location: str,
    excerpt: str,
) -> Observation:
    """Build an observation from a readback record (FOUND / NOT_FOUND / ACTIVE ...)."""
    identifiers = Identifiers()
    if subject:
        classify_value(subject, identifiers)
    result_norm = (result or "").upper()
    state: ResourceState = {"record_type": record_type, "result": result_norm}
    return Observation(
        evidence_id=evidence_id,
        source_format=source_format,
        captured_at=captured_at,
        system=system_for(resource_id, system),
        resource_id=resource_id,
        identifiers=identifiers,
        present=result_norm not in ABSENT_RESULTS,
        state=state,
        location=location,
        excerpt=excerpt[:300],
    )
