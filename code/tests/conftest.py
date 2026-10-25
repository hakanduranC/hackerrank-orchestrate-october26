"""Shared fixtures: make ``ghost`` importable and provide an evidence manifest entry."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.data.loaders import EvidenceFile  # noqa: E402


@pytest.fixture
def entry() -> EvidenceFile:
    return EvidenceFile(
        file_id="evidence_t1",
        file_path="evidence/test/file",
        origin="test",
        captured_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )
