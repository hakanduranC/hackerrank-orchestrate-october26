"""PDF evidence parser (pdfplumber, pure Python).

PDFs are business documents (statements, support attachments, retention
registers, compliance extracts). They rarely contain a verdict; they contain
identifiers and resource references. The parser emits one observation per
resource id mentioned, carrying every identifier found in the document, and a
document-level observation when no resource id is present so identifier lookups
can still surface the file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Observation, SourceFormat
from ghost.data.parsers.common import find_resource_ids, identifiers_from, system_for

_ORIGIN_SYSTEM = {
    "support_platform": "support_platform",
    "security_service": "security_service",
    "legal_retention_service": "legal_service",
    "account_history_service": "account_service",
}
_RETAIN_RE = re.compile(r"[Rr]etain(?:ed)?\s+until\s+([0-9]{1,2}\s+\w+\s+[0-9]{4}|\S+)")


def extract_text(path: Path) -> str:
    """The text of every page, joined."""
    with pdfplumber.open(path) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages)


def parse(entry: EvidenceFile, path: Path) -> list[Observation]:
    """Observations of a PDF: one per resource id it names, carrying the identifiers found in the
    text.
    """
    text = extract_text(path)
    identifiers = identifiers_from({"text": text})
    system_hint = _ORIGIN_SYSTEM.get(entry.origin)
    retain = _RETAIN_RE.search(text)
    state = {
        "origin": entry.origin,
        "retain_until": retain.group(1) if retain else None,
        # A support-platform PDF is a copy of a ticket attachment, i.e. customer data itself.
        "document_kind": "attachment" if entry.origin == "support_platform" else "document",
    }
    resource_ids = find_resource_ids(text) or [f"document:{entry.file_id}"]
    return [
        Observation(
            evidence_id=entry.file_id,
            source_format=SourceFormat.PDF,
            captured_at=entry.captured_at,
            system=system_for(rid, system_hint)
            if not rid.startswith("document:")
            else system_hint or "unknown",
            resource_id=rid,
            identifiers=identifiers,
            present=True,
            state=dict(state),
            location=path.name,
            excerpt=text[:300],
        )
        for rid in resource_ids
    ]
