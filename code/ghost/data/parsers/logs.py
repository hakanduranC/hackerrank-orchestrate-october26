"""Log evidence parser.

Six line syntaxes exist in the corpus. Each is normalised into the same
:class:`LogLine` (timestamp, service, trace id, event, fields) and then:

* ``record_lookup`` lines are readback records (``system``, ``resource_id``,
  ``result``, ``subject_reference``);
* operational traces are joined by ``trace_id``: the ``*_started`` line carries
  the lookup key (``lookup_key`` / ``match_key`` / ``subject_key`` / ``scan_key``
  / ``subject_ref``) and the ``*_completed`` / ``*_scan`` line carries the
  ``resource_id`` and the result fields. Some completed lines carry a
  ``correlation_key`` inline instead of a started line.

Absence is signalled by ``http_status=404``, ``rows=0`` or ``result=miss``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ghost.data.loaders import EvidenceFile, parse_ts
from ghost.data.observations import Identifiers, Observation, SourceFormat
from ghost.data.parsers.common import (
    ResourceState,
    classify_value,
    identifiers_from,
    readback_observation,
    system_for,
)

_KV_RE = re.compile(r'(\w+)=("((?:[^"\\]|\\.)*)"|\S+)')
_BRACKET_RE = re.compile(r"^(\S+) \[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] (\w+): (.*)$")
_PIPE_RE = re.compile(r"^(\S+) \| (\w+) \| ([^|]+) \| ([^|]+) \| (\w+) \| (.*)$")
_SYSLOG_RE = re.compile(r"^(\S+) <\d+>\d+ \S+ (\S+) (\S+) - - (.*)$")
_PLAIN_RE = re.compile(r"^(\S+) (.*)$")

LOOKUP_KEYS = (
    "lookup_key",
    "match_key",
    "subject_key",
    "scan_key",
    "subject_ref",
    "correlation_key",
)
RESULT_EVENTS = (
    "_completed",
    "_pii_scan",
    "_identity_scan",
    "marker_check",
    "_evaluated",
    "analytics_profile_read",
    "connector_heartbeat",
)
# A line with a resource id and any of these fields describes a state, whatever its event name.
STATE_FIELDS = (
    "enabled",
    "restore_allowed",
    "http_status",
    "rows",
    "result",
    "matched_value",
    "archive_present",
    "marker_found",
    "download_available",
    "decision",
)
NOISE_SERVICES = {
    "readback_collector",
    "log_router",
    "queue_consumer",
    "dns_cache",
    "secret_agent",
    "webhook_delivery",
    "feature_flags",
    "circuit_breaker",
    "attachment_preview",
}


@dataclass
class LogLine:
    """A log line normalised across the six syntaxes."""

    timestamp: datetime
    service: str
    trace_id: str | None
    event: str
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""


def parse(entry: EvidenceFile, path: Path) -> list[Observation]:
    """Observations of a log file: readback records and trace-joined operations."""
    lines = [
        parsed
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (parsed := parse_line(raw))
    ]
    out: list[Observation] = []
    lookup_by_trace: dict[str, str] = {}
    for line in lines:
        if line.event == "record_lookup":
            out.append(_readback(entry, line))
            continue
        key = _lookup_key(line)
        if line.trace_id and key and not _is_result(line):
            lookup_by_trace.setdefault(line.trace_id, key)
    for line in lines:
        if (
            line.event == "record_lookup"
            or not _is_result(line)
            or "resource_id" not in line.fields
        ):
            continue
        if line.service in NOISE_SERVICES or line.event.endswith("_started"):
            continue
        out.append(_trace_observation(entry, line, lookup_by_trace.get(line.trace_id or "")))
    return out


def parse_line(raw: str) -> LogLine | None:
    """Normalise one line from any of the six syntaxes; ``None`` for noise."""
    raw = raw.rstrip()
    if not raw:
        return None
    if match := _BRACKET_RE.match(raw):
        ts, _, service, trace, event, rest = match.groups()
        return _make(ts, service, trace, event, _kv(rest), raw)
    if match := _PIPE_RE.match(raw):
        ts, _, service, trace, event, rest = match.groups()
        return _make(ts, service.strip(), trace.strip(), event, _kv(rest), raw)
    if match := _SYSLOG_RE.match(raw):
        ts, service, trace, rest = match.groups()
        fields = _kv(rest)
        return _make(ts, service, trace, fields.pop("event", ""), fields, raw)
    if match := _PLAIN_RE.match(raw):
        ts, rest = match.groups()
        if rest.startswith("{"):
            return _from_json(ts, rest, raw)
        fields = _kv(rest)
        if "message" in fields and "function" in fields:
            inner = _kv(fields["message"].replace('\\"', '"'))
            fields = {**fields, **inner}
            fields.setdefault("service", fields.get("function", ""))
            fields.setdefault("trace_id", fields.get("requestId", ""))
        return _make(
            ts,
            fields.pop("service", ""),
            fields.pop("trace_id", None),
            fields.pop("event", ""),
            fields,
            raw,
        )
    return None


def _from_json(ts: str, body: str, raw: str) -> LogLine | None:
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return None
    fields = {str(k): str(v) for k, v in obj.items()}
    return _make(
        ts,
        fields.pop("service", fields.pop("logger", "")),
        fields.pop("trace_id", None),
        fields.pop("event", ""),
        fields,
        raw,
    )


def _make(
    ts: str, service: str, trace: str | None, event: str, fields: dict[str, str], raw: str
) -> LogLine | None:
    try:
        when = parse_ts(ts)
    except ValueError:
        return None
    trace_id = trace if trace and trace.startswith(("tr-", "rb-")) else None
    return LogLine(
        timestamp=when, service=service, trace_id=trace_id, event=event, fields=fields, raw=raw
    )


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value, quoted in _KV_RE.findall(text):
        out[key] = quoted if value.startswith('"') else value
    return out


def _lookup_key(line: LogLine) -> str | None:
    for key in LOOKUP_KEYS:
        if value := line.fields.get(key):
            return value if value != "null" else None
    return None


def _is_result(line: LogLine) -> bool:
    if any(line.event.endswith(suffix) or line.event == suffix for suffix in RESULT_EVENTS):
        return True
    return "resource_id" in line.fields and any(k in line.fields for k in STATE_FIELDS)


def _readback(entry: EvidenceFile, line: LogLine) -> Observation:
    f = line.fields
    return readback_observation(
        evidence_id=entry.file_id,
        source_format=SourceFormat.LOG,
        captured_at=line.timestamp,
        system=f.get("system"),
        resource_id=f["resource_id"],
        record_type=f.get("record_type"),
        result=f.get("result"),
        subject=f.get("subject_reference"),
        location=line.service,
        excerpt=line.raw,
    )


def _trace_observation(entry: EvidenceFile, line: LogLine, lookup_key: str | None) -> Observation:
    f = line.fields
    identifiers: Identifiers = identifiers_from({k: v for k, v in f.items() if v != "null"})
    if lookup_key:
        classify_value(lookup_key, identifiers)
    state: ResourceState = {
        k: v for k, v in f.items() if k not in ("instance", "pod", "region", "shard")
    }
    state["event"] = line.event
    state["lookup_key"] = lookup_key
    return Observation(
        evidence_id=entry.file_id,
        source_format=SourceFormat.LOG,
        captured_at=line.timestamp,
        system=system_for(
            f["resource_id"],
            line.service if line.service.endswith(("_service", "_platform")) else None,
        ),
        resource_id=f["resource_id"],
        identifiers=identifiers,
        present=_present(f),
        state=state,
        related_resources=[
            v for k, v in f.items() if k != "resource_id" and k.endswith("resource_id")
        ],
        location=f"{line.service}:{line.event}",
        excerpt=line.raw[:300],
    )


def _present(f: dict[str, str]) -> bool:
    if f.get("http_status") == "404" or f.get("rows") == "0":
        return False
    if f.get("result", "").lower() in ("miss", "not_found"):
        return False
    if f.get("archive_present") == "false":
        return False
    return True
