"""SQLite-compatible SQL snapshot parser.

The dumps come in three schema styles, all handled by executing the script in an
in-memory SQLite database and reading every table back:

* **inventory**: one typed table per category plus ``snapshot_scope`` with the
  capture time per origin system;
* **replica captures**: ``customer_data_primary`` / ``*_replica`` tables whose
  rows carry ``origin_system``, ``collection_name`` and a ``record_json`` blob;
* **small primaries**: ``customer_profiles_primary`` / ``_failover_replica`` and
  ``search_documents_primary`` with typed columns.

The table name is kept as the observation's ``location`` so replication drift
(present in a replica, absent from the primary) stays visible downstream.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Observation, SourceFormat
from ghost.data.parsers.common import (
    COLLECTION_SYSTEM,
    flat_state,
    identifiers_from,
    related_resources,
    system_for,
    to_datetime,
)

_CAPTURED_RE = re.compile(r"^--\s*Captured at:\s*(\S+)", re.MULTILINE)
_SKIP_TABLES = {"snapshot_scope"}


def parse(entry: EvidenceFile, path: Path) -> list[Observation]:
    """Observations of a SQL snapshot export, executed in a private in-memory SQLite."""
    script = path.read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(script)
    file_captured = _header_capture(script, entry.captured_at)
    scope = _snapshot_scope(conn)
    observations: list[Observation] = []
    for table in _tables(conn):
        if table in _SKIP_TABLES:
            continue
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            record = dict(row)
            observations.append(_row_observation(entry, table, record, scope, file_captured))
    conn.close()
    return observations


def _header_capture(script: str, fallback: datetime) -> datetime:
    match = _CAPTURED_RE.search(script)
    return to_datetime(match.group(1), fallback) if match else fallback


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [str(r["name"]) for r in rows]


def _snapshot_scope(conn: sqlite3.Connection) -> dict[str, str]:
    if "snapshot_scope" not in _tables(conn):
        return {}
    rows = conn.execute("SELECT origin_system, captured_at FROM snapshot_scope")
    return {str(r["origin_system"]): str(r["captured_at"]) for r in rows}


def _location(table: str, collection: str) -> str:
    """``table`` for typed tables, ``table/collection`` for record_json tables."""
    if collection in COLLECTION_SYSTEM and collection != table:
        return f"{table}/{collection}"
    return table


def _row_observation(
    entry: EvidenceFile,
    table: str,
    record: dict[str, Any],
    scope: dict[str, str],
    file_captured: datetime,
) -> Observation:
    collection = table
    explicit_system: str | None = None
    payload: dict[str, Any] = record
    if "record_json" in record:
        payload = json.loads(record["record_json"])
        explicit_system = record.get("origin_system")
        collection = record.get("collection_name") or table
    resource_id = str(payload.get("resource_id") or record.get("resource_id"))
    system = system_for(resource_id, explicit_system, collection)
    captured = to_datetime(scope.get(system), file_captured)
    state = flat_state(payload)
    state["table"] = table
    if collection != table:
        state["collection"] = collection
    return Observation(
        evidence_id=entry.file_id,
        source_format=SourceFormat.SQL,
        captured_at=captured,
        system=system,
        resource_id=resource_id,
        identifiers=identifiers_from(payload),
        present=True,
        state=state,
        related_resources=related_resources(payload, resource_id),
        location=_location(table, collection),
        excerpt=json.dumps(payload, ensure_ascii=False)[:300],
    )
