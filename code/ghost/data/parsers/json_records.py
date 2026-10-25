"""JSON evidence parser.

Shapes handled (all discovered by surveying the files, not assumed):

* snapshot bundles: ``snapshots[] {captured_at, data_store, collection, records[]}``
* flat collections: ``{captured_at|exported_at, source|origin, records[]}``
* service exports: ``service_exports.<system>.<collection>[]`` with
  ``observed_at`` per record, ``profile_lookups`` (200 / 404) and
  ``index_queries`` whose hits carry the search documents
* readback records inside any of the above: ``{system, resource_id, record_type,
  result, subject_reference|lookup_value, recorded_at}``
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Observation, SourceFormat
from ghost.data.parsers.common import (
    flat_state,
    identifiers_from,
    readback_observation,
    related_resources,
    system_for,
    to_datetime,
)


def _snapshot(
    entry: EvidenceFile, snap: dict[str, Any], file_captured: datetime
) -> list[Observation]:
    """One collection snapshot of a bundle; an empty one still proves absence."""
    captured = to_datetime(snap.get("captured_at"), file_captured)
    location = f"{snap.get('data_store')}/{snap.get('collection')}"
    out: list[Observation] = []
    if not snap.get("records"):
        # An empty collection snapshot is still a snapshot: it proves that nothing
        # was in the table at that time. Keep a marker row so absence proofs see it.
        out.append(
            Observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.JSON,
                captured_at=captured,
                system=str(snap.get("data_store")),
                resource_id="",
                present=False,
                state={"snapshot": "empty"},
                location=location,
                excerpt="empty snapshot",
            )
        )
    for rec in snap.get("records", []):
        out.extend(
            _record(entry, rec, captured, snap.get("data_store"), snap.get("collection"), location)
        )
    return out


def parse(entry: EvidenceFile, path: Path) -> list[Observation]:
    """Observations of a JSON file: bundle snapshots, service exports or a flat collection."""
    data = json.loads(path.read_text(encoding="utf-8"))
    file_captured = to_datetime(
        data.get("captured_at") or data.get("exported_at"), entry.captured_at
    )
    out: list[Observation] = []
    if "snapshots" in data:
        for snap in data["snapshots"]:
            out.extend(_snapshot(entry, snap, file_captured))
    elif "service_exports" in data:
        for system, collections in data["service_exports"].items():
            for collection, records in collections.items():
                for rec in records:
                    captured = to_datetime(rec.get("observed_at"), file_captured)
                    out.extend(
                        _record(entry, rec, captured, system, collection, f"{system}/{collection}")
                    )
    elif "records" in data:
        location = str(
            data.get("collection") or data.get("origin") or data.get("source") or "records"
        )
        for rec in data["records"]:
            out.extend(_record(entry, rec, file_captured, None, data.get("collection"), location))
    return out


def _record(
    entry: EvidenceFile,
    rec: dict[str, Any],
    captured: datetime,
    system: str | None,
    collection: str | None,
    location: str,
) -> list[Observation]:
    captured = to_datetime(rec.get("recorded_at") or rec.get("observed_at"), captured)
    if "record_type" in rec and "result" in rec:
        return [
            readback_observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.JSON,
                captured_at=captured,
                system=rec.get("system") or system,
                resource_id=str(rec["resource_id"]),
                record_type=rec.get("record_type"),
                result=rec.get("result"),
                subject=rec.get("subject_reference") or rec.get("lookup_value"),
                location=location,
                excerpt=json.dumps(rec, ensure_ascii=False),
            )
        ]
    if "status_code" in rec and "profile" in rec:
        return [_profile_lookup(entry, rec, captured, system or "account_service", location)]
    if "hits" in rec and "query" in rec:
        return _index_query(entry, rec, captured, system or "search_service", location)
    resource_id = str(rec["resource_id"])
    return [
        Observation(
            evidence_id=entry.file_id,
            source_format=SourceFormat.JSON,
            captured_at=captured,
            system=system_for(resource_id, system, collection),
            resource_id=resource_id,
            identifiers=identifiers_from(rec),
            present=True,
            state=flat_state(rec),
            related_resources=related_resources(rec, resource_id),
            location=location,
            excerpt=json.dumps(rec, ensure_ascii=False)[:300],
        )
    ]


def _profile_lookup(
    entry: EvidenceFile, rec: dict[str, Any], captured: datetime, system: str, location: str
) -> Observation:
    profile = rec.get("profile") or {}
    present = int(rec.get("status_code", 0)) == 200 and bool(profile)
    state = flat_state(profile)
    state["status_code"] = rec.get("status_code")
    return Observation(
        evidence_id=entry.file_id,
        source_format=SourceFormat.JSON,
        captured_at=captured,
        system=system,
        resource_id=str(rec["resource_id"]),
        identifiers=identifiers_from(profile),
        present=present,
        state=state,
        location=location,
        excerpt=json.dumps(rec, ensure_ascii=False)[:300],
    )


def _index_query(
    entry: EvidenceFile, rec: dict[str, Any], captured: datetime, system: str, location: str
) -> list[Observation]:
    hits = rec.get("hits") or {}
    documents = hits.get("documents") or []
    identifiers = identifiers_from({"query": rec.get("query")})
    if not documents:
        return [
            Observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.JSON,
                captured_at=captured,
                system=system,
                resource_id=str(rec["resource_id"]),
                identifiers=identifiers,
                present=False,
                state={"query": rec.get("query"), "total_hits": hits.get("total", 0)},
                location=location,
                excerpt=json.dumps(rec, ensure_ascii=False)[:300],
            )
        ]
    out: list[Observation] = []
    for doc in documents:
        doc_ids = identifiers_from(doc)
        state = flat_state(doc)
        state["query"] = rec.get("query")
        out.append(
            Observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.JSON,
                captured_at=captured,
                system=system,
                resource_id=str(doc.get("resource_id") or rec["resource_id"]),
                identifiers=doc_ids,
                present=True,
                state=state,
                location=location,
                excerpt=json.dumps(doc, ensure_ascii=False)[:300],
            )
        )
    return out
