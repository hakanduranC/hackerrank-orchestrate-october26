"""Runtime settings, read from environment variables only.

Secrets never appear in code or in committed files. ``.env`` (uncommitted) is
loaded if present; ``.env.example`` documents every variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
# The models that produced the submitted output.csv (judge) and the screenshot extractions.
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_VISION_MODEL = "claude-opus-5-5"
PARSER_FORMATS = frozenset({"sql", "json", "log", "text", "pdf", "image"})


@dataclass(frozen=True)
class Settings:
    """Typed view of the ``GHOST_*`` environment variables."""

    model: str
    effort: str
    vision_model: str
    retry_allowance: int
    judge_turn_limit: int
    escalation_turn_limit: int
    concurrency: int
    dataset_dir: Path
    output_path: Path
    cache_dir: Path
    trace_dir: Path
    disabled_parsers: frozenset[str] = frozenset()
    max_tokens: int = 8192  # per model call (thinking + output)
    model_timeout_s: int = 120  # judge call
    vision_timeout_s: int = 180  # screenshot read
    max_retries: int = 3  # SDK retries on 429 / 5xx
    read_window: int = 25  # read_evidence lines each side, judge loop
    escalation_read_window: int = 60  # the same, escalated loop


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _path(name: str, default: str) -> Path:
    raw = Path(_env(name, default))
    return raw if raw.is_absolute() else REPO_ROOT / raw


EFFORT_LEVELS = ("low", "medium", "high")


class SettingsError(ValueError):
    """A GHOST_* variable has a value the program cannot run with."""


def _int(name: str, default: str, minimum: int) -> int:
    raw = _env(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}, got {value}")
    return value


def load_settings() -> Settings:
    """Build :class:`Settings` from the environment (``.env`` loaded first) and validate it."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    effort = _env("GHOST_EFFORT", "medium").lower()
    if effort not in EFFORT_LEVELS:
        raise SettingsError(f"GHOST_EFFORT must be one of {EFFORT_LEVELS}, got {effort!r}")
    model, vision_model = (
        _env("GHOST_MODEL", DEFAULT_MODEL),
        _env("GHOST_VISION_MODEL", DEFAULT_VISION_MODEL),
    )
    if not model.strip() or not vision_model.strip():
        raise SettingsError("GHOST_MODEL and GHOST_VISION_MODEL must not be empty")
    disabled = frozenset(
        p.strip().lower() for p in _env("GHOST_DISABLE_PARSERS", "").split(",") if p.strip()
    )
    unknown = disabled - PARSER_FORMATS
    if unknown:
        raise SettingsError(
            f"GHOST_DISABLE_PARSERS names unknown formats {sorted(unknown)};"
            f" known: {sorted(PARSER_FORMATS)}"
        )
    return Settings(
        model=model,
        effort=effort,
        vision_model=vision_model,
        retry_allowance=_int("GHOST_RETRY_ALLOWANCE", "2", minimum=0),
        judge_turn_limit=_int("GHOST_JUDGE_TURN_LIMIT", "8", minimum=1),
        escalation_turn_limit=_int("GHOST_ESCALATION_TURN_LIMIT", "12", minimum=1),
        concurrency=_int("GHOST_CONCURRENCY", "4", minimum=1),
        dataset_dir=_path("GHOST_DATASET_DIR", "dataset"),
        output_path=_path("GHOST_OUTPUT_PATH", "output.csv"),
        cache_dir=_path("GHOST_CACHE_DIR", ".cache"),
        trace_dir=_path("GHOST_TRACE_DIR", "traces"),
        disabled_parsers=disabled,
        max_tokens=_int("GHOST_MAX_TOKENS", "8192", minimum=1024),
        model_timeout_s=_int("GHOST_MODEL_TIMEOUT", "120", minimum=10),
        vision_timeout_s=_int("GHOST_VISION_TIMEOUT", "180", minimum=10),
        max_retries=_int("GHOST_MAX_RETRIES", "3", minimum=0),
        read_window=_int("GHOST_READ_WINDOW", "25", minimum=5),
        escalation_read_window=_int("GHOST_ESCALATION_READ_WINDOW", "60", minimum=5),
    )


def require_api_key() -> None:
    """Fail early with a clear message when the model cannot be called."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
