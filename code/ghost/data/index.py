"""SQLite index over every parsed observation.

Built once from ``evidence_files.csv`` + the parsers, cached at
``.cache/index.db`` and rebuilt when any evidence file changes (content hash).
Lookups are exact: an observation matches a subject when any of its normalized
identifiers (user_id, email, phone, email hash) equals one of the subject's.

Three retrieval paths exist because evidence links records in three ways:

* **identifier match** (``find_by_identifiers``): the record carries the subject's
  email / phone / hash / user_id;
* **resource link** (``related_resources`` on an observation, ``links`` table):
  the record points at another resource, e.g. a search document's source account;
* **absence by table** (``absence_proofs``): a complete primary-table snapshot
  that does *not* contain a resource proves it is gone at that time.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ghost.data.ids import IMAGE_SUFFIXES
from ghost.data.loaders import EvidenceFile, parse_ts
from ghost.data.observations import Identifiers, Observation, SourceFormat
from ghost.data.parsers import TEXT_SUFFIXES, parse_evidence
from ghost.data.parsers.image import transcription
from ghost.data.parsers.pdf import extract_text
from ghost.data.vision import BUNDLED_DIR


def describe_file(evidence_id: str, path: Path) -> list[CatalogRow]:
    """Structural catalog of one evidence file, built deterministically (no model).

    SQL dumps: every table with row count and columns, plus the snapshot scope. JSON: every
    collection with record count and keys. Logs / text / PDFs / images: the line count.
    """
    suffix = path.suffix.lower()
    if suffix == ".sql":
        return _describe_sql(evidence_id, path)
    if suffix == ".json":
        return _describe_json(evidence_id, path)
    lines = raw_text(path).count("\n")
    return [CatalogRow(evidence_id, "lines", path.name, lines, "")]


def _describe_sql(evidence_id: str, path: Path) -> list[CatalogRow]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(path.read_text(encoding="utf-8"))
        out: list[CatalogRow] = []
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')]
            n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            out.append(CatalogRow(evidence_id, "table", table, int(n), ", ".join(cols)))
        return out
    except sqlite3.Error as exc:
        return [CatalogRow(evidence_id, "error", path.name, 0, str(exc)[:120])]
    finally:
        conn.close()


def _describe_json(evidence_id: str, path: Path) -> list[CatalogRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[CatalogRow] = []

    def add(name: str, records: object) -> None:
        if isinstance(records, list):
            keys = sorted({k for r in records if isinstance(r, dict) for k in r})
            out.append(CatalogRow(evidence_id, "collection", name, len(records), ", ".join(keys)))

    if isinstance(data, dict) and "snapshots" in data:
        for snap in data["snapshots"]:
            add(f"{snap.get('data_store')}/{snap.get('collection')}", snap.get("records", []))
    elif isinstance(data, dict) and "service_exports" in data:
        for system, collections in data["service_exports"].items():
            for name, records in collections.items():
                add(f"{system}/{name}", records)
    elif isinstance(data, dict) and "records" in data:
        add(str(data.get("collection") or data.get("origin") or "records"), data["records"])
    elif isinstance(data, dict):
        add("document", [data])
    return out


def raw_text(path: Path, vision_cache_dir: Path | None = None) -> str:
    """Plain text of an evidence file (PDFs through pdfplumber, images via the vision cache)."""
    if path.suffix.lower() == ".pdf":
        return extract_text(path)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return transcription(path, vision_cache_dir)
    return path.read_text(encoding="utf-8", errors="replace")


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    source_format TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    file_captured_at TEXT NOT NULL,
    system TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    present INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    location TEXT NOT NULL,
    excerpt TEXT NOT NULL
);
CREATE TABLE identifiers (
    obs_id INTEGER NOT NULL REFERENCES observations(id),
    kind TEXT NOT NULL,
    value TEXT NOT NULL
);
CREATE TABLE links (
    obs_id INTEGER NOT NULL REFERENCES observations(id),
    resource_id TEXT NOT NULL
);
CREATE TABLE files (
    evidence_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    origin TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TABLE raw_lines (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE raw_fts USING fts5(text, content='raw_lines', content_rowid='id');
CREATE TABLE catalog (
    evidence_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    rows INTEGER NOT NULL,
    columns TEXT NOT NULL
);
CREATE INDEX idx_ident ON identifiers(kind, value);
CREATE INDEX idx_links ON links(obs_id);
CREATE INDEX idx_obs_resource ON observations(resource_id);
CREATE INDEX idx_obs_evidence ON observations(evidence_id);
CREATE INDEX idx_obs_location ON observations(evidence_id, location);
CREATE INDEX idx_raw_file ON raw_lines(evidence_id, line_no);
"""


@dataclass(frozen=True)
class RawHit:
    """One raw line matching a search: file, line number, text and the file's capture time."""

    evidence_id: str
    line_no: int
    text: str
    file_captured_at: datetime


@dataclass(frozen=True)
class CatalogRow:
    """One structural fact about a file: a table / collection, its size and its columns."""

    evidence_id: str
    kind: str
    name: str
    rows: int
    columns: str


@dataclass(frozen=True)
class FileEntry:
    """A manifest entry as stored in the index."""

    evidence_id: str
    file_path: str
    origin: str
    captured_at: datetime


_KINDS = ("user_ids", "emails", "phones", "email_hashes")

# Snapshots that list every row of a primary table. A resource missing from one
# of these at capture time is proven absent at that time. Typed SQL primaries and
# the assurance bundles' collection snapshots qualify; partial captures (inventory
# rows, replica record_json dumps, lookups) are deliberately excluded.
_ID_LIKE_RE = re.compile(r"[a-z_]+_\d+")


def _like_escape(term: str) -> str:
    """Escape LIKE wildcards so ``_`` and ``%`` in a term match literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def token_pattern(term: str) -> re.Pattern[str]:
    """A whole-token match for ids (``account_1`` not inside ``account_10``); a plain
    case-insensitive substring match for everything else (emails, names, words)."""
    if _ID_LIKE_RE.fullmatch(term):
        return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9])")
    return re.compile(re.escape(term), re.I)


FULL_TABLES = frozenset(
    {
        "customer_profiles_primary",
        "search_documents_primary",
        "account_service/customer_profiles",
        "search_service/customer_search_documents",
    }
)


@dataclass(frozen=True)
class AbsenceProof:
    """A full snapshot that lacks a record: proof the record was absent at that time."""

    evidence_id: str
    captured_at: datetime
    system: str
    table: str


def corpus_fingerprint(
    dataset_dir: Path,
    entries: Iterable[EvidenceFile],
    vision_cache_dir: Path | None = None,
    disabled_parsers: frozenset[str] | None = None,
) -> str:
    """Hash of the evidence corpus, the vision extractions, the index schema and the ablation
    settings; a change rebuilds the cache.
    """
    digest = hashlib.sha256(_SCHEMA.encode())  # layout changes rebuild the cache
    for entry in sorted(entries, key=lambda e: e.file_id):
        path = dataset_dir / entry.file_path
        digest.update(entry.file_id.encode())
        if path.exists():
            digest.update(path.read_bytes())
    folders = [f for f in (vision_cache_dir, BUNDLED_DIR) if f is not None and f.exists()]
    for folder in folders:
        for cached in sorted(folder.glob("*.json")):  # extractions are part of the corpus
            digest.update(cached.name.encode())
            digest.update(cached.read_bytes())
    digest.update(",".join(sorted(disabled_parsers or ())).encode())  # ablations rebuild too
    return digest.hexdigest()


class EvidenceIndex:
    """Exact-match retrieval over parsed evidence."""

    def __init__(self, conn: sqlite3.Connection, path: Path | None = None) -> None:
        self._main_conn = conn
        self._main_conn.row_factory = sqlite3.Row
        self._path = path
        self._owner = threading.get_ident()
        self._local = threading.local()
        self._file_identifiers: dict[str, list[Identifiers]] = {}

    @property
    def _conn(self) -> sqlite3.Connection:
        """One SQLite connection per thread: the agent's tools run in worker threads."""
        if self._path is None or threading.get_ident() == self._owner:
            return self._main_conn
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    @classmethod
    def in_memory(
        cls,
        observations: Iterable[Observation],
        files: Iterable[EvidenceFile] = (),
        texts: dict[str, str] | None = None,
    ) -> EvidenceIndex:
        """Index observations without touching disk (tests, fixtures).

        ``files`` registers manifest entries (needed by the guards) and ``texts`` maps a
        file_id to its raw text so keyword search and mention checks work too.
        """
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.executescript(_SCHEMA)
        index = cls(conn)
        for entry in files:
            conn.execute(
                "INSERT INTO files VALUES (?,?,?,?)",
                (entry.file_id, entry.file_path, entry.origin, entry.captured_at.isoformat()),
            )
        captured = {entry.file_id: entry.captured_at for entry in files}
        for obs in observations:
            index.insert(obs, file_captured_at=captured.get(obs.evidence_id))
        for file_id, text in (texts or {}).items():
            index._insert_raw_lines(file_id, text)
        return index

    @classmethod
    def open_or_build(
        cls,
        dataset_dir: Path,
        entries: list[EvidenceFile],
        cache_path: Path,
        force: bool = False,
        vision_cache_dir: Path | None = None,
        disabled_parsers: frozenset[str] = frozenset(),
    ) -> EvidenceIndex:
        """Open the on-disk index when its fingerprint matches the corpus, otherwise rebuild it."""
        fingerprint = corpus_fingerprint(dataset_dir, entries, vision_cache_dir, disabled_parsers)
        if cache_path.exists() and not force:
            conn = sqlite3.connect(cache_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row and row["value"] == fingerprint:
                return cls(conn, cache_path)
            conn.close()
        if cache_path.exists():
            cache_path.unlink()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(cache_path, check_same_thread=False)
        conn.executescript(_SCHEMA)
        index = cls(conn, cache_path)
        index._populate(dataset_dir, entries, vision_cache_dir, disabled_parsers)
        conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
        conn.commit()
        return index

    def _populate(
        self,
        dataset_dir: Path,
        entries: list[EvidenceFile],
        vision_cache_dir: Path | None,
        disabled_parsers: frozenset[str] = frozenset(),
    ) -> None:
        """Parse every file. ``disabled_parsers`` (format ablation) drops a format's structured
        observations while its raw lines and catalog stay searchable - what an unseen format
        looks like to the agent."""
        for entry in entries:
            path = dataset_dir / entry.file_path
            self._conn.execute(
                "INSERT INTO files VALUES (?,?,?,?)",
                (entry.file_id, entry.file_path, entry.origin, entry.captured_at.isoformat()),
            )
            if path.suffix.lower() not in TEXT_SUFFIXES | IMAGE_SUFFIXES:
                continue
            self._index_file(entry, path, vision_cache_dir, disabled_parsers)

    def _index_file(
        self,
        entry: EvidenceFile,
        path: Path,
        vision_cache_dir: Path | None,
        disabled_parsers: frozenset[str],
    ) -> None:
        """Index one file; a malformed file is recorded, not fatal.

        Structured parsing, raw lines and the catalog are attempted independently, so a dump
        that SQLite rejects still has its text searchable, and the failure is visible to the
        agent through ``describe_evidence`` (catalog kind ``error``).
        """
        problems: list[str] = []
        try:
            for obs in parse_evidence(entry, path, vision_cache_dir, disabled_parsers):
                self.insert(obs, file_captured_at=entry.captured_at)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            problems.append(f"parse: {type(exc).__name__}: {exc}"[:200])
        try:
            self._insert_raw_lines(entry.file_id, raw_text(path, vision_cache_dir))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"text: {type(exc).__name__}: {exc}"[:200])
        try:
            rows = describe_file(entry.file_id, path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"catalog: {type(exc).__name__}: {exc}"[:200])
            rows = []
        for problem in problems:
            rows.append(CatalogRow(entry.file_id, "error", path.name, 0, problem))
        for row in rows:
            self._conn.execute(
                "INSERT INTO catalog VALUES (?,?,?,?,?)",
                (row.evidence_id, row.kind, row.name, row.rows, row.columns),
            )

    def build_problems(self) -> list[CatalogRow]:
        """Files that could not be fully indexed (catalog rows of kind ``error``)."""
        rows = self._conn.execute(
            "SELECT evidence_id, kind, name, rows, columns FROM catalog WHERE kind='error'"
        )
        return [CatalogRow(r[0], r[1], r[2], int(r[3]), r[4]) for r in rows]

    def catalog(self, evidence_id: str) -> list[CatalogRow]:
        """The structural catalog rows of one file."""
        rows = self._conn.execute(
            "SELECT evidence_id, kind, name, rows, columns FROM catalog WHERE evidence_id=?"
            " ORDER BY kind, name",
            (evidence_id,),
        )
        return [CatalogRow(r[0], r[1], r[2], int(r[3]), r[4]) for r in rows]

    def _insert_raw_lines(self, evidence_id: str, text: str) -> None:
        """Keep every raw line searchable, so the agent is not bounded by the parsers."""
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            cur = self._conn.execute(
                "INSERT INTO raw_lines (evidence_id, line_no, text) VALUES (?,?,?)",
                (evidence_id, line_no, line),
            )
            self._conn.execute(
                "INSERT INTO raw_fts (rowid, text) VALUES (?,?)", (cur.lastrowid, line)
            )

    def search_raw(
        self, term: str, before: datetime | None = None, limit: int = 15
    ) -> list[RawHit]:
        """Exact keyword / phrase search over raw lines of files captured by ``before``."""
        phrase = '"' + term.replace('"', " ").strip() + '"'
        sql = (
            "SELECT r.evidence_id, r.line_no, r.text, f.captured_at FROM raw_fts"
            " JOIN raw_lines r ON r.id = raw_fts.rowid"
            " JOIN files f ON f.evidence_id = r.evidence_id WHERE raw_fts MATCH ?"
        )
        params: list[str | int] = [phrase]
        if before is not None:
            sql += " AND f.captured_at <= ?"
            params.append(before.isoformat())
        sql += " ORDER BY f.captured_at DESC, r.evidence_id, r.line_no LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:  # substrings (partial ids) fall back to a plain scan
            like_sql = (
                "SELECT r.evidence_id, r.line_no, r.text, f.captured_at FROM raw_lines r"
                " JOIN files f ON f.evidence_id = r.evidence_id WHERE r.text LIKE ? ESCAPE '\\'"
            )
            like_params: list[str | int] = [f"%{_like_escape(term)}%"]
            if before is not None:
                like_sql += " AND f.captured_at <= ?"
                like_params.append(before.isoformat())
            like_sql += " ORDER BY f.captured_at DESC, r.evidence_id, r.line_no LIMIT ?"
            like_params.append(limit * 4)
            rows = self._conn.execute(like_sql, like_params).fetchall()
            boundary = token_pattern(term)  # account_418 must not surface account_4181
            rows = [r for r in rows if boundary.search(str(r[2]))]
            rows = rows[:limit]
        return [RawHit(r[0], int(r[1]), str(r[2]), parse_ts(r[3])) for r in rows]

    def read_raw(self, evidence_id: str, line_no: int, around: int = 10) -> list[tuple[int, str]]:
        """Raw lines of a file around a line number."""
        rows = self._conn.execute(
            "SELECT line_no, text FROM raw_lines WHERE evidence_id=? AND line_no BETWEEN ? AND ?"
            " ORDER BY line_no",
            (evidence_id, max(1, line_no - around), line_no + around),
        )
        return [(int(r[0]), str(r[1])) for r in rows]

    def list_files(
        self, origin_contains: str | None = None, before: datetime | None = None
    ) -> list[FileEntry]:
        """Manifest entries, optionally filtered by origin or path substring and by capture time."""
        sql = "SELECT evidence_id, file_path, origin, captured_at FROM files WHERE 1=1"
        params: list[str] = []
        if origin_contains:
            sql += " AND (origin LIKE ? OR file_path LIKE ?)"
            params.extend([f"%{origin_contains}%", f"%{origin_contains}%"])
        if before is not None:
            sql += " AND captured_at <= ?"
            params.append(before.isoformat())
        sql += " ORDER BY captured_at DESC"
        return [
            FileEntry(r[0], r[1], r[2], parse_ts(r[3])) for r in self._conn.execute(sql, params)
        ]

    def file_entry(self, evidence_id: str) -> FileEntry | None:
        """The manifest entry of one file, or None."""
        row = self._conn.execute(
            "SELECT evidence_id, file_path, origin, captured_at FROM files WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        return FileEntry(row[0], row[1], row[2], parse_ts(row[3])) if row else None

    def file_mentions_text(self, evidence_id: str, term: str) -> bool:
        """Does a raw line of the file mention ``term`` as a whole token?

        ``account_1`` is not mentioned by a file that only holds ``account_10``; LIKE
        wildcards in the term are escaped.
        """
        rows = self._conn.execute(
            "SELECT text FROM raw_lines WHERE evidence_id=? AND text LIKE ? ESCAPE '\\'",
            (evidence_id, f"%{_like_escape(term)}%"),
        )
        pattern = token_pattern(term)
        return any(pattern.search(str(r[0])) for r in rows)

    def insert(self, obs: Observation, file_captured_at: datetime | None = None) -> None:
        """Store one observation. ``file_captured_at`` is the manifest capture time of its
        file: an observation is usable for a case only if the *file* existed by ``audit_at``,
        whatever timestamp the record inside carries."""
        cur = self._conn.execute(
            "INSERT INTO observations (evidence_id, source_format, captured_at, file_captured_at,"
            " system, resource_id, present, state_json, location, excerpt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                obs.evidence_id,
                obs.source_format.value,
                obs.captured_at.isoformat(),
                (file_captured_at or obs.captured_at).isoformat(),
                obs.system,
                obs.resource_id,
                int(obs.present),
                json.dumps(obs.state, ensure_ascii=False),
                obs.location,
                obs.excerpt,
            ),
        )
        obs_id = cur.lastrowid
        self._conn.executemany(
            "INSERT INTO identifiers VALUES (?,?,?)",
            [
                (obs_id, kind, value)
                for kind in _KINDS
                for value in sorted(getattr(obs.identifiers, kind))
            ],
        )
        self._conn.executemany(
            "INSERT INTO links VALUES (?,?)", [(obs_id, rid) for rid in obs.related_resources]
        )

    def find_by_identifiers(
        self, identifiers: Identifiers, before: datetime | None = None
    ) -> list[Observation]:
        """Every observation sharing at least one identifier with the subject."""
        clauses: list[str] = []
        params: list[str] = []
        for kind in _KINDS:
            values = sorted(getattr(identifiers, kind))
            if values:
                marks = ",".join("?" * len(values))
                clauses.append(f"(kind=? AND value IN ({marks}))")
                params.extend([kind, *values])
        if not clauses:
            return []
        sql = (
            "SELECT DISTINCT o.* FROM observations o JOIN identifiers i ON i.obs_id=o.id "
            f"WHERE ({' OR '.join(clauses)})"
        )
        if before is not None:
            sql += " AND o.captured_at <= ? AND o.file_captured_at <= ?"
            params.extend([before.isoformat(), before.isoformat()])
        sql += " ORDER BY o.captured_at DESC, o.id"
        return [self._row_to_obs(r) for r in self._conn.execute(sql, params)]

    def history(self, resource_id: str, before: datetime | None = None) -> list[Observation]:
        """Every observation of one record (newest first), optionally only those captured by
        ``before``.
        """
        sql = "SELECT * FROM observations WHERE resource_id=?"
        params: list[str] = [resource_id]
        if before is not None:
            sql += " AND captured_at <= ? AND file_captured_at <= ?"
            params.extend([before.isoformat(), before.isoformat()])
        sql += " ORDER BY captured_at DESC, id"
        return [self._row_to_obs(r) for r in self._conn.execute(sql, params)]

    def by_evidence(self, evidence_id: str) -> list[Observation]:
        """Every observation parsed from one file."""
        rows = self._conn.execute("SELECT * FROM observations WHERE evidence_id=?", (evidence_id,))
        return [self._row_to_obs(r) for r in rows]

    def absence_proofs(
        self,
        resource_id: str,
        system: str,
        before: datetime | None = None,
        subject: Identifiers | None = None,
    ) -> list[AbsenceProof]:
        """Snapshots of ``system`` captured in time that do not contain the resource.

        Typed SQL primaries are whole tables and prove absence for anyone. A bundle
        collection snapshot is scoped to the subjects the bundle covers, so it proves
        absence only when the same file holds another record of this subject.
        """
        marks = ",".join("?" * len(FULL_TABLES))
        sql = (
            "SELECT DISTINCT evidence_id, location, captured_at FROM observations "
            f"WHERE system=? AND location IN ({marks})"
        )
        params: list[str] = [system, *sorted(FULL_TABLES)]
        if before is not None:
            sql += " AND captured_at <= ? AND file_captured_at <= ?"
            params.extend([before.isoformat(), before.isoformat()])
        proofs: list[AbsenceProof] = []
        for row in self._conn.execute(sql, params):
            hit = self._conn.execute(
                "SELECT 1 FROM observations WHERE evidence_id=? AND location=? AND resource_id=?",
                (row["evidence_id"], row["location"], resource_id),
            ).fetchone()
            if hit is not None:
                continue
            scoped = "/" in str(row["location"])
            if scoped and not self._file_mentions(str(row["evidence_id"]), subject):
                continue
            proofs.append(
                AbsenceProof(
                    row["evidence_id"], parse_ts(row["captured_at"]), system, row["location"]
                )
            )
        return sorted(proofs, key=lambda p: p.captured_at, reverse=True)

    def file_mentions_subject(self, evidence_id: str, subject: Identifiers | None) -> bool:
        """Public form of :meth:`_file_mentions` for the verdict hardening."""
        return self._file_mentions(evidence_id, subject)

    def _file_mentions(self, evidence_id: str, subject: Identifiers | None) -> bool:
        """Does any observation in the file carry one of the subject's identifiers?"""
        if subject is None:
            return False
        if evidence_id not in self._file_identifiers:
            self._file_identifiers[evidence_id] = [
                obs.identifiers for obs in self.by_evidence(evidence_id)
            ]
        return any(ids.intersects(subject) for ids in self._file_identifiers[evidence_id])

    def counts_by_format(self) -> dict[str, int]:
        """Observations per source format (for the build report)."""
        rows = self._conn.execute(
            "SELECT source_format, COUNT(*) AS n FROM observations GROUP BY source_format"
        )
        return {str(r["source_format"]): int(r["n"]) for r in rows}

    def _row_to_obs(self, row: sqlite3.Row) -> Observation:
        ids = Identifiers()
        for ident in self._conn.execute(
            "SELECT kind, value FROM identifiers WHERE obs_id=?", (row["id"],)
        ):
            getattr(ids, ident["kind"]).add(ident["value"])
        related = [
            str(r["resource_id"])
            for r in self._conn.execute(
                "SELECT resource_id FROM links WHERE obs_id=?", (row["id"],)
            )
        ]
        return Observation(
            evidence_id=row["evidence_id"],
            source_format=SourceFormat(row["source_format"]),
            captured_at=parse_ts(row["captured_at"]),
            system=row["system"],
            resource_id=row["resource_id"],
            identifiers=ids,
            present=bool(row["present"]),
            state=json.loads(row["state_json"]),
            related_resources=related,
            location=row["location"],
            excerpt=row["excerpt"],
        )
