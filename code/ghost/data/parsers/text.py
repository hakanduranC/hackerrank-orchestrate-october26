"""Plain-text handover parser.

Lines look like::

    - <time> | account_service | account_4248 | USER_PROFILE | NOT_FOUND | novak.tomasz@icloud.com

which is the readback record shape with a pipe delimiter.
"""

from __future__ import annotations

from pathlib import Path

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Observation, SourceFormat
from ghost.data.parsers.common import readback_observation, to_datetime


def parse(entry: EvidenceFile, path: Path) -> list[Observation]:
    """Observations of a pipe-separated handover note."""
    out: list[Observation] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().lstrip("-").strip()
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 6 or not parts[2].count("_"):
            continue
        when, system, resource_id, record_type, result, subject = parts
        out.append(
            readback_observation(
                evidence_id=entry.file_id,
                source_format=SourceFormat.TEXT,
                captured_at=to_datetime(when, entry.captured_at),
                system=system,
                resource_id=resource_id,
                record_type=record_type,
                result=result,
                subject=None if subject.startswith("no direct") else subject,
                location="handover",
                excerpt=raw,
            )
        )
    return out
