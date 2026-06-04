"""W7: unit coverage for the preamble builder (previously only tested
through full loop scenarios)."""

from __future__ import annotations

from pathlib import Path

from relay.orchestrator.preamble import build_preamble, compose_prompt


def test_build_preamble_with_phase() -> None:
    pre = build_preamble(Path("/tmp/relay/runs/abc"), "planning")
    lines = pre.splitlines()
    assert lines[0] == "RELAY_RUN_DIR: /tmp/relay/runs/abc"
    assert lines[1] == "RELAY_PHASE: planning"
    # WU2 (resilient-iter-close): every task-mode preamble carries a
    # sentinel-discipline reminder as its trailing line.
    assert any("RELAY_SENTINEL_REMINDER:" in line for line in lines[2:])


def test_build_preamble_without_phase_omits_phase_line() -> None:
    pre = build_preamble(Path("/tmp/relay/runs/abc"), None)
    assert pre.startswith("RELAY_RUN_DIR: /tmp/relay/runs/abc")
    assert "RELAY_PHASE" not in pre
    assert "RELAY_SENTINEL_REMINDER:" in pre


def test_preamble_reminder_lists_all_four_terminal_sentinels() -> None:
    pre = build_preamble(Path("/tmp/x"), None)
    reminder = next(
        line for line in pre.splitlines()
        if line.startswith("RELAY_SENTINEL_REMINDER:")
    )
    for token in ("done", "handoff", "pause-for-input", "fanout"):
        assert token in reminder, (
            f"reminder line {reminder!r} missing terminal sentinel {token!r}"
        )


def test_compose_prompt_is_preamble_then_body() -> None:
    full = compose_prompt(Path("/tmp/r"), "development", "do the work")
    assert full.startswith("RELAY_RUN_DIR: /tmp/r\nRELAY_PHASE: development")
    assert full.endswith("do the work")
    assert "do the work" in full.split("RELAY_PHASE: development", 1)[1]
