"""Image evidence parser: turns a cached vision extraction into observations.

The model reads each screenshot once (``ghost.data.vision``); this parser is
pure and offline. It links identifiers at the row level, so a table of four
backups with four different owners yields four separately-owned observations,
and only the record a screen is *about* (title, detail panel) inherits the
screen-level identifiers.
"""

from __future__ import annotations

import json
from pathlib import Path

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Identifiers, Observation, SourceFormat
from ghost.data.parsers.common import find_resource_ids, identifiers_from, system_for
from ghost.data.vision import ImageExtraction, find_cached

_ORIGIN_SYSTEM = {
    "account_admin_portal": "account_service",
    "search_index_admin": "search_service",
    "integration_admin": "integration_service",
    "analytics_platform": "analytics_platform",
    "export_service": "export_service",
    "support_platform": "support_platform",
    "backup_service": "backup_service",
    "security_service": "security_service",
    "identity_service": "identity_service",
    "object_storage": "object_storage",
    "marketing_platform": "marketing_platform",
}
_ABSENT_WORDS = ("deleted", "not found", "404", "removed", "purged", "no longer exists")
_ON_WORDS = (
    "enabled",
    "active",
    "running",
    "on",
    "healthy",
    "connected",
    "live",
    "syncing",
    "continuous sync",
    "continuous",
)
_OFF_WORDS = ("disabled", "paused", "off", "stopped", "inactive")
_RESTORABLE_WORDS = ("restorable", "restore allowed", "eligible")
_BLOCKED_WORDS = ("blocked", "restore blocked", "legal hold", "deletion block")


def load_extraction(path: Path, cache_dir: Path | None) -> ImageExtraction | None:
    """The cached vision extraction of an image (runtime cache, then the bundled copy), or None."""
    cache = find_cached(path, cache_dir)
    if cache is None:
        return None
    payload = json.loads(cache.read_text(encoding="utf-8"))
    return ImageExtraction.model_validate(payload["extraction"])


def transcription(path: Path, cache_dir: Path | None) -> str:
    """The image's visible text as lines, for the raw-line index."""
    ex = load_extraction(path, cache_dir)
    if ex is None:
        return ""
    parts = [ex.title, *ex.lines]
    parts += [f"{k}: {v}" for k, v in ex.fields.items()]
    parts += [" | ".join(f"{k}: {v}" for k, v in row.items()) for row in ex.records]
    return "\n".join(p for p in parts if p)


def _state_of(values: dict[str, str]) -> dict[str, str | int | None]:
    state: dict[str, str | int | None] = dict(values)
    for key, value in values.items():
        k, v = key.lower(), value.lower().strip()
        if "status" in k or "state" in k or "mode" in k or "health" in k:
            if v in _ON_WORDS:
                state["enabled"] = True
            elif v in _OFF_WORDS:
                state["enabled"] = False
        if any(w in v for w in _RESTORABLE_WORDS):
            state["restore_allowed"] = True
        if any(w in v for w in _BLOCKED_WORDS):
            state["deletion_block"] = True
    return state


def _present(values: dict[str, str]) -> bool:
    return not any(
        any(w in value.lower() for w in _ABSENT_WORDS)
        for key, value in values.items()
        if "status" in key.lower() or "state" in key.lower() or "result" in key.lower()
    )


def parse(entry: EvidenceFile, path: Path, cache_dir: Path | None) -> list[Observation]:
    """Observations of a screenshot from its cached extraction; none without an extraction."""
    ex = load_extraction(path, cache_dir)
    if ex is None:
        return []
    system_hint = _ORIGIN_SYSTEM.get(entry.origin)
    text = transcription(path, cache_dir)
    screen_ids = identifiers_from(
        {"fields": ex.fields, "lines": ex.lines, "identifiers": ex.identifiers.model_dump()}
    )
    headline = " ".join([ex.title, *ex.lines[:12], *ex.fields.values()])
    has_rows = bool(ex.records)
    observations: list[Observation] = []
    for rid in find_resource_ids(text):
        ids, values = _attest(rid, ex, headline, screen_ids, has_rows)
        observations.append(
            Observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.IMAGE,
                captured_at=entry.captured_at,
                system=_system(rid, system_hint),
                resource_id=rid,
                identifiers=ids,
                present=_present(values),
                state={**_state_of(values), "document_kind": ex.document_kind},
                location=path.name,
                excerpt=json.dumps(values, ensure_ascii=False)[:300],
            )
        )
    if not observations:
        observations.append(
            Observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.IMAGE,
                captured_at=entry.captured_at,
                system=system_hint or "unknown",
                resource_id=f"document:{entry.file_id}",
                identifiers=screen_ids,
                present=True,
                state={**_state_of(ex.fields), "document_kind": ex.document_kind},
                location=path.name,
                excerpt=text[:300],
            )
        )
    return observations


def _attest(
    rid: str, ex: ImageExtraction, headline: str, screen_ids: Identifiers, has_rows: bool
) -> tuple[Identifiers, dict[str, str]]:
    """Identifiers and field values a screen attests for one record.

    The record a screen displays in detail (title / side panel) inherits its table row and
    the panel fields; list rows are context only; a screen without any table is about the
    records it names.
    """
    rows = [row for row in ex.records if rid in " ".join(row.values())]
    in_detail = rid in headline
    ids = Identifiers()
    values: dict[str, str] = {}
    for row in rows:
        values = {**row, **values}
    if in_detail:
        for row in rows:
            ids = _merge(ids, identifiers_from(row))
        ids = _merge(ids, identifiers_from(ex.fields))
        values = {**ex.fields, **values}
    elif not has_rows:
        ids = _merge(ids, screen_ids)
    return ids, values


def _system(rid: str, origin_hint: str | None) -> str:
    """Id prefix first (support_123 is the support platform's even on an account screen)."""
    by_prefix = system_for(rid)
    return by_prefix if by_prefix != "unknown" else (origin_hint or "unknown")


def _merge(a: Identifiers, b: Identifiers) -> Identifiers:
    return Identifiers(
        user_ids=a.user_ids | b.user_ids,
        emails=a.emails | b.emails,
        phones=a.phones | b.phones,
        email_hashes=a.email_hashes | b.email_hashes,
    )
