"""Settings come from the environment and are validated with clear messages."""

from __future__ import annotations

import pytest
from ghost.config import SettingsError, load_settings


def test_defaults_are_the_models_that_produced_the_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("GHOST_MODEL", "GHOST_VISION_MODEL", "GHOST_EFFORT", "GHOST_MAX_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.model == "claude-sonnet-5-5" and s.vision_model == "claude-opus-5-5"
    assert s.max_tokens == 8192 and s.read_window == 25 and s.escalation_read_window == 60


@pytest.mark.parametrize(
    ("var", "value", "fragment"),
    [
        ("GHOST_EFFORT", "hgih", "GHOST_EFFORT must be one of"),
        ("GHOST_CONCURRENCY", "0", "GHOST_CONCURRENCY must be >= 1"),
        ("GHOST_JUDGE_TURN_LIMIT", "eight", "must be an integer"),
        ("GHOST_DISABLE_PARSERS", "sql,xml", "unknown formats ['xml']"),
    ],
)
def test_bad_values_are_rejected_with_the_variable_named(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str, fragment: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(SettingsError, match=fragment.replace("[", "\\[").replace("]", "\\]")):
        load_settings()
