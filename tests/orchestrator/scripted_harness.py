"""A scripted :class:`Harness` test double (no pi, fully offline).

Phase 2 verification (plan.md) must exercise the loop end-to-end
*without* spawning pi — pi e2e stays gated behind ``PI_INTEGRATION=1``.
The orchestrator takes its harness by injection (``RelayCore(...,
harness=...)``); this double implements the same normalized protocol the
loop consumes, replaying a deterministic per-iter script.

Each ``spawn`` pops the next script, so a multi-iter test scripts each
iter independently (e.g. ``[handoff_block, done_block]``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from relay_v2.harness import (
    AssistantText,
    HarnessEvent,
    SessionEnded,
    SessionStarted,
)


@dataclass
class TextScript:
    """One iter: emit a single assistant turn carrying ``text``, then end
    cleanly. ``text`` is what the signaling parser sees."""

    text: str


@dataclass
class HangScript:
    """One iter: open the session, then never produce a closing signal —
    used to drive the per-iter timeout and external-cancel paths."""


Script = TextScript | HangScript


class ScriptedSession:
    def __init__(self, script: Script, idx: int) -> None:
        self._script = script
        self.session_id = f"scripted-{idx}"
        self._cancelled = asyncio.Event()
        self._final: SessionEnded | None = None

    async def events(self) -> AsyncIterator[HarnessEvent]:
        now = time.time()
        yield SessionStarted(
            seq=1, ts=now, session_id=self.session_id, cwd="."
        )
        if isinstance(self._script, TextScript):
            yield AssistantText(
                seq=2,
                ts=time.time(),
                text=self._script.text,
                turn_seq=1,
                kind="text",
            )
            ended = SessionEnded(
                seq=3, ts=time.time(), messages=[], stop_reason="clean"
            )
            self._final = ended
            yield ended
        else:  # HangScript — block until cancelled / timed out.
            await self._cancelled.wait()

    async def cancel(self) -> None:
        self._cancelled.set()
        if self._final is None:
            self._final = SessionEnded(
                seq=99, ts=time.time(), messages=[], stop_reason="cancelled"
            )

    async def wait(self) -> SessionEnded:
        if self._final is None:
            self._final = SessionEnded(
                seq=99, ts=time.time(), messages=[], stop_reason="cancelled"
            )
        return self._final


class ScriptedHarness:
    name = "scripted"

    def __init__(self, scripts: list[Script]) -> None:
        self._scripts = scripts
        self._calls = 0

    async def spawn(
        self,
        prompt: str,
        cwd: Path,
        env: dict[str, str],
        signal_config: object,
        resume_from: str | None = None,
    ) -> ScriptedSession:
        idx = self._calls
        self._calls += 1
        script = (
            self._scripts[idx]
            if idx < len(self._scripts)
            else TextScript("(no script)")
        )
        return ScriptedSession(script, idx)
