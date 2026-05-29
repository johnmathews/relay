"""Bundled-skill injection into the pi spawn argv (ADR-42, 2026-05-25).

The engineering-team skill ships with relay; ``PiHarness._build_argv``
must append a ``--skill <path>`` pair for every path the Settings model
resolves. The default resolves to the bundled tree;
``RELAY_PI_SKILLS=`` opts out; a colon-separated env value overrides.
All-static — no subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from relay.config import Settings
from relay.harness.pi import PiHarness
from relay.harness.skills import bundled_skill_dir


def _argv(settings: Settings, prompt: str = "p") -> list[str]:
    harness = PiHarness(settings)
    return harness._build_argv(
        prompt, model="m", provider="anthropic", resume_from=None
    )


def test_bundled_skill_dir_resolves_to_real_tree() -> None:
    src = bundled_skill_dir()
    assert src.is_dir()
    assert src.name == "pi"
    assert (src / "SKILL.md").is_file()
    assert (src / "phases" / "phase-1-evaluation.md").is_file()
    assert (src / "references" / "sentinels.md").is_file()


def test_default_settings_inject_bundled_skill() -> None:
    settings = Settings()
    paths = settings.pi_skill_paths
    assert paths == [bundled_skill_dir()]

    argv = _argv(settings)
    # Exactly one --skill <bundled-path> pair, in order.
    skill_args = [
        (argv[i], argv[i + 1])
        for i in range(len(argv) - 1)
        if argv[i] == "--skill"
    ]
    assert skill_args == [("--skill", str(bundled_skill_dir()))]


def test_empty_env_disables_explicit_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RELAY_PI_SKILLS=`` is the explicit opt-out — pi falls back to
    its own auto-discovery only. Empty argv carries no --skill at all."""
    monkeypatch.setenv("RELAY_PI_SKILLS", "")
    settings = Settings()
    assert settings.pi_skill_paths == []
    argv = _argv(settings)
    assert "--skill" not in argv


def test_env_override_replaces_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A colon-separated env value REPLACES (not augments) the default."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("RELAY_PI_SKILLS", f"{a}:{b}")
    settings = Settings()
    assert settings.pi_skill_paths == [a, b]
    argv = _argv(settings)
    # Two --skill pairs, preserving order.
    skill_pairs = [
        (argv[i], argv[i + 1])
        for i in range(len(argv) - 1)
        if argv[i] == "--skill"
    ]
    assert skill_pairs == [("--skill", str(a)), ("--skill", str(b))]


def test_skill_injection_placed_before_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash-recovery `--session` resume must come AFTER --skill so the
    flag-pairing parser on pi's side sees them as distinct repeats, not
    as values to each other. (Defensive — argparse doesn't care, but
    the explicit ordering documents intent.)"""
    harness = PiHarness(Settings())
    argv = harness._build_argv(
        "p", model="m", provider="anthropic", resume_from="session-xyz"
    )
    skill_idx = argv.index("--skill")
    session_idx = argv.index("--session")
    assert skill_idx < session_idx
