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


@dataclass
class EventScript:
    """One iter: emit an explicit list of normalized events, then a
    given :class:`SessionEnded`. Lets the observability suite (Phase 7)
    drive ``ToolUseStart``/``ToolUseEnd`` and a usage-bearing
    ``SessionEnded.messages`` payload without pi. ``events`` should
    include an ``AssistantText(kind="text")`` carrying a column-0
    closing sentinel so the loop terminates as it would under pi."""

    events: list[HarnessEvent]
    final: SessionEnded


Script = TextScript | HangScript | EventScript


class ScriptedSession:
    def __init__(
        self, script: Script, idx: int, blocked: asyncio.Event | None = None
    ) -> None:
        self._script = script
        self.session_id = f"scripted-{idx}"
        self._cancelled = asyncio.Event()
        self._final: SessionEnded | None = None
        # Set right before a HangScript blocks, so a test can await the
        # exact moment the iter is hung instead of sleeping (no races).
        self._blocked = blocked

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
        elif isinstance(self._script, EventScript):
            # Model the pi harness's Option-D guarantee (ADR-29):
            # ``_final`` (with pi's verbatim usage messages) is
            # available by the time the orchestrator can act on the
            # terminal sentinel and break — so wait() returns it even
            # when events() is abandoned mid-stream.
            self._final = self._script.final
            for ev in self._script.events:
                yield ev
            yield self._script.final
        else:  # HangScript — block until cancelled / timed out.
            if self._blocked is not None:
                self._blocked.set()
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
        # Fired by the first HangScript session when it actually blocks;
        # lets cancel/aclose tests await that point deterministically.
        self.blocked = asyncio.Event()

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
        return ScriptedSession(script, idx, blocked=self.blocked)
