"""Entry point: ``uv run python code/main.py``.

Thin wrapper so the package can be named ``ghost`` (a package literally named
``code`` would shadow the standard-library module of the same name).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
