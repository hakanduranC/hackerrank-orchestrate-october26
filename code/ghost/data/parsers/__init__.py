"""Evidence parsers. Every parser turns one file into ``list[Observation]``.

Dispatch is by file extension. Images are parsed from the cached vision
extraction (``ghost.data.vision``); the model call happens once, beforehand.
"""

from __future__ import annotations

from pathlib import Path

from ghost.data.loaders import EvidenceFile
from ghost.data.observations import Observation
from ghost.data.parsers import image, json_records, logs, pdf, sql, text

TEXT_SUFFIXES = {".sql", ".json", ".log", ".txt", ".pdf"}
from ghost.data.ids import IMAGE_SUFFIXES  # noqa: E402

FORMAT_OF_SUFFIX = {
    ".sql": "sql",
    ".json": "json",
    ".log": "log",
    ".txt": "text",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}


def parse_evidence(
    entry: EvidenceFile,
    path: Path,
    vision_cache_dir: Path | None = None,
    disabled: frozenset[str] = frozenset(),
) -> list[Observation]:
    """Parse one manifest entry. Unsupported formats return an empty list; formats named in
    ``disabled`` are skipped on purpose (format ablation)."""
    suffix = path.suffix.lower()
    if FORMAT_OF_SUFFIX.get(suffix) in disabled:
        return []
    if suffix in IMAGE_SUFFIXES:
        return image.parse(entry, path, vision_cache_dir)
    if suffix == ".sql":
        return sql.parse(entry, path)
    if suffix == ".json":
        return json_records.parse(entry, path)
    if suffix == ".log":
        return logs.parse(entry, path)
    if suffix == ".txt":
        return text.parse(entry, path)
    if suffix == ".pdf":
        return pdf.parse(entry, path)
    return []
