"""Evaluation entry point: ``uv run python code/evaluation/main.py --baseline --verbose``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.evaluation.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
