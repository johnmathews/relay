"""Option D (ADR-29): the pi harness consumes ``agent_end`` before the
sentinel-bearing ``AssistantText`` reaches the orchestrator, so a
terminal-sentinel close still recovers pi's verbatim usage messages.

Fully offline — a fake subprocess replays a synthetic pi stream whose
order (``…turn_end, agent_end``) matches the committed fixtures.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from relay_v2.harness.pi import PiSession
from relay_v2.harness.protocol import AssistantText, SessionEnded

DONE = "All done.\n\n[[engteam:done]]"
USAGE_MSGS = [
    {
        "role": "assistant",
        "content": [{"type": "text", "text": DONE}],
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "usage": {"input": 3, "output": 5, "cost": {"total": 0.01}},
    }
]
_CLEAN_STREAM = [
    {"type": "session", "id": "sid-1", "cwd": "/tmp"},
    {"type": "turn_start"},
    {
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": DONE},
    },
    {"type": "turn_end"},
    {"type": "agent_end", "messages": USAGE_MSGS},
]


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    """Just enough of asyncio.subprocess.Process for PiSession."""

    def __init__(self, stream: list[dict[str, Any]]) -> None:
        self.stdout = _FakeStdout(
            [(json.dumps(e) + "\n").encode() for e in stream]
        )
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_terminal_sentinel_close_still_recovers_usage() -> None:
    """Simulate the orchestrator: break on the sentinel AssistantText,
    then wait(). _final must already carry pi's messages (Option D)."""

    async def scenario() -> SessionEnded:
        session = PiSession(_FakeProc(_CLEAN_STREAM))  # type: ignore[arg-type]
        agen = session.events()
        async for ev in agen:
            if isinstance(ev, AssistantText) and "[[engteam:done]]" in ev.text:
                break  # exactly what _drive_iter does on a terminal sig
        await agen.aclose()
        return await session.wait()

    final = asyncio.run(scenario())
    assert isinstance(final, SessionEnded)
    assert final.stop_reason == "clean"
    assert final.messages == USAGE_MSGS


def test_fully_consumed_external_order_is_unchanged() -> None:
    """Without an early break the stream is still
    SessionStarted → AssistantText → SessionEnded (lookahead only
    delays delivery by one slot; it never reorders)."""

    async def scenario() -> list[str]:
        session = PiSession(_FakeProc(_CLEAN_STREAM))  # type: ignore[arg-type]
        return [type(ev).__name__ async for ev in session.events()]

    assert asyncio.run(scenario()) == [
        "SessionStarted",
        "AssistantText",
        "SessionEnded",
    ]


def test_crash_without_agent_end_still_synthesizes() -> None:
    """No agent_end (crash/timeout): the held text is still delivered at
    stream end and wait() synthesizes an empty terminal — unchanged
    fallback behavior."""
    stream = _CLEAN_STREAM[:-1]  # drop agent_end

    async def scenario() -> SessionEnded:
        session = PiSession(_FakeProc(stream))  # type: ignore[arg-type]
        saw_text = False
        async for ev in session.events():
            if isinstance(ev, AssistantText):
                saw_text = True
        assert saw_text  # buffered text flushed at end-of-stream
        return await session.wait()

    final = asyncio.run(scenario())
    assert isinstance(final, SessionEnded)
    assert final.stop_reason == "crash"
    assert final.messages == []
