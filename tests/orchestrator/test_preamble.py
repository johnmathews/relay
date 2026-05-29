"""W7: unit coverage for the preamble builder (previously only tested
through full loop scenarios)."""

from __future__ import annotations

from pathlib import Path

from relay.orchestrator.preamble import build_preamble, compose_prompt


def test_build_preamble_with_phase() -> None:
    pre = build_preamble(Path("/tmp/relay/runs/abc"), "planning")
    assert pre == "RELAY_RUN_DIR: /tmp/relay/runs/abc\nRELAY_PHASE: planning"


def test_build_preamble_without_phase_omits_phase_line() -> None:
    pre = build_preamble(Path("/tmp/relay/runs/abc"), None)
    assert pre == "RELAY_RUN_DIR: /tmp/relay/runs/abc"
    assert "RELAY_PHASE" not in pre


def test_compose_prompt_is_preamble_then_body() -> None:
    full = compose_prompt(Path("/tmp/r"), "development", "do the work")
    assert full.startswith("RELAY_RUN_DIR: /tmp/r\nRELAY_PHASE: development")
    assert full.endswith("do the work")
    assert "do the work" in full.split("RELAY_PHASE: development", 1)[1]
