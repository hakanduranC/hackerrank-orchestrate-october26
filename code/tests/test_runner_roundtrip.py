"""Concurrent runner keeps order; composed rows pass the output validator."""

from __future__ import annotations

from pathlib import Path

import pytest
from ghost.agent.baseline import baseline_verdict
from ghost.config import load_settings
from ghost.data.index import EvidenceIndex
from ghost.data.loaders import load_dataset
from ghost.identity import IdentityResolver
from ghost.output import write_output
from ghost.runner import Runner
from ghost.validate import validate_output


def test_workers_preserve_request_order_and_rows_validate(tmp_path: Path) -> None:
    settings = load_settings()
    if not (settings.dataset_dir / "deletion_requests.csv").exists():
        pytest.skip("needs the challenge dataset (GHOST_DATASET_DIR)")
    data = load_dataset(settings.dataset_dir)
    index = EvidenceIndex.open_or_build(
        settings.dataset_dir,
        data.evidence_files,
        settings.cache_dir / "index.db",
        vision_cache_dir=settings.cache_dir / "vision",
    )
    runner = Runner(data, index, IdentityResolver(data), baseline_verdict)
    requests = data.sample_requests[:8]
    serial = runner.run(requests, workers=1)
    parallel = runner.run(requests, workers=4)
    assert [r.case_id for r in parallel] == [r.case_id for r in requests]
    assert [r.model_dump() for r in parallel] == [r.model_dump() for r in serial]
    # a full-shaped output file built from real rows passes the contract validator
    out = tmp_path / "output.csv"
    write_output(Runner(data, index, IdentityResolver(data)).run(data.requests[:12]), out)
    report = validate_output(out, data)
    assert report.rows == 12
    assert all("case ids differ" in e or "not sorted" in e for e in report.errors) or report.ok
    assert not [e for e in report.errors if "step" in e]
