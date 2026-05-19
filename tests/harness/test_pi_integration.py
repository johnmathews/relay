"""W5 — real-pi end-to-end. SKIPPED unless ``PI_INTEGRATION=1``.

Verifies the full Phase 1 flow against a live pi v0.74.0: spawn ->
stream normalized events -> accumulate turn text -> the
``text_sentinels`` parser turns an emitted handoff sentinel into a
``SignalEmitted`` -> the session ends cleanly. pi is NEVER invoked
unless the gate env var is explicitly set (plan.md Phase 1).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from relay_v2.harness import AssistantText, SessionEnded
from relay_v2.harness.pi import PiHarness
from relay_v2.harness.protocol import SignalConfig
from relay_v2.harness.signaling import detect_in_text

pytestmark = pytest.mark.skipif(
    os.environ.get("PI_INTEGRATION") != "1",
    reason="real-pi e2e; set PI_INTEGRATION=1 to run",
)

_PROMPT = (
    "Output the following five lines verbatim as your entire response, "
    "with no preamble, no code fence, and nothing after them:\n\n"
    "[[engteam:prompt-start]]\n"
    "Continue with W2 next iter.\n"
    "[[engteam:prompt-end]]\n\n"
    "[[engteam:handoff]]"
)


async def _run(cwd: Path) -> tuple[object, SessionEnded]:
    harness = PiHarness()
    session = await harness.spawn(
        prompt=_PROMPT,
        cwd=cwd,
        env={},
        signal_config=SignalConfig(strategy="text_sentinels"),
    )
    cfg = SignalConfig(strategy="text_sentinels")
    by_turn: dict[int, list[str]] = {}
    signal = None
    async for ev in session.events():
        if isinstance(ev, AssistantText) and ev.kind == "text":
            by_turn.setdefault(ev.turn_seq, []).append(ev.text)
            signal = detect_in_text("\n".join(by_turn[ev.turn_seq]), cfg)
            if signal is not None:
                break
    if signal is not None:
        await session.cancel()
    result = await session.wait()
    return signal, result


def test_pi_handoff_end_to_end(tmp_path: Path) -> None:
    signal, result = asyncio.run(asyncio.wait_for(_run(tmp_path), timeout=120))
    assert signal is not None, "no sentinel detected in pi output"
    assert signal.kind == "handoff"  # type: ignore[attr-defined]
    assert "W2" in signal.args["next_prompt"]  # type: ignore[attr-defined]
    assert isinstance(result, SessionEnded)
    assert result.stop_reason in {"clean", "cancelled"}
