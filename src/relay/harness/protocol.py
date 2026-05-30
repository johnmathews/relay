"""Harness protocol and normalized event types (spec.md §4.1).

This module is the entire contract the orchestrator sees. Only the
``harness`` package may know that pi exists; everything outside the
package consumes the normalized :class:`HarnessEvent` hierarchy and the
:class:`Harness` / :class:`HarnessSession` protocols defined here
(ADR-04 — harness isolation invariant).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "HarnessEvent",
    "SessionStarted",
    "AssistantText",
    "AssistantTextDelta",
    "ToolUseStart",
    "ToolUseUpdate",
    "ToolUseEnd",
    "SessionEnded",
    "SignalConfig",
    "SignalEmitted",
    "HarnessSession",
    "Harness",
]


@dataclass
class HarnessEvent:
    """Base class for every normalized event a harness session emits.

    ``seq`` is monotonic within a single session; ``ts`` is unix epoch
    seconds recorded when the harness produced the normalized event.
    """

    seq: int
    ts: float


@dataclass
class SessionStarted(HarnessEvent):
    session_id: str
    cwd: str


@dataclass
class AssistantText(HarnessEvent):
    """Accumulated assistant output for one turn.

    ``kind`` distinguishes user-visible response text (``"text"``) from
    model reasoning (``"thinking"``). Per ADR-18 the signaling layer
    only inspects ``kind == "text"`` so that sentinels mentioned inside
    chain-of-thought never trigger a false signal. ``kind`` defaults to
    ``"text"`` to keep the spec.md §4.1 two-field constructor working.
    """

    text: str
    turn_seq: int
    kind: Literal["text", "thinking"] = "text"


@dataclass
class AssistantTextDelta(HarnessEvent):
    """A single streamed text chunk inside an in-progress turn.

    Emitted inline by the harness as pi feeds ``text_delta`` /
    ``thinking_delta`` events — **in addition to**, not instead of,
    the accumulated :class:`AssistantText` flushed at ``turn_end``.
    The orchestrator does NOT persist these: spec.md §3.2 has no
    event kind for deltas. They flow through the SSE broadcaster's
    ephemeral channel so the dashboard can render an in-progress
    pending row as tokens arrive (ADR-46 Plan B). The post-turn
    :class:`AssistantText` remains the canonical persisted record;
    concatenating the deltas of a (turn_seq, kind) equals the
    canonical text (ADR-18 invariant preserved).

    ``delta_seq`` is monotonic within a turn and resets across turns
    — useful for ordering on the client side without relying on
    arrival order.
    """

    text: str
    turn_seq: int
    delta_seq: int
    kind: Literal["text", "thinking"] = "text"


@dataclass
class ToolUseStart(HarnessEvent):
    tool_id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolUseUpdate(HarnessEvent):
    tool_id: str
    partial_result: dict[str, Any]


@dataclass
class ToolUseEnd(HarnessEvent):
    tool_id: str
    result: dict[str, Any]
    is_error: bool
    duration_ms: int


@dataclass
class SessionEnded(HarnessEvent):
    """Terminal event. ``messages`` is the harness's compiled message
    list, passed through verbatim and never interpreted here (OQ-1).
    ``stop_reason`` is normalized to one of the four values below.
    """

    messages: list[Any]
    stop_reason: Literal["clean", "crash", "timeout", "cancelled"]


@dataclass
class SignalConfig:
    """Selects how the orchestrator detects state-transition signals
    (ADR-05). Lives here because it is part of the harness contract:
    :meth:`Harness.spawn` accepts it.
    """

    strategy: Literal["text_sentinels", "mcp_tools"]
    mcp_tool_prefix: str = "relay__"


@dataclass
class SignalEmitted:
    """Normalized signal, emitted regardless of detection strategy
    (spec.md §5). ``kind`` is the sentinel verb in snake_case
    (``phase_start``, ``unit_start``, ``unit_done``, ``unit_abandoned``,
    ``handoff``, ``done``, ``pause``); ``args`` carries verb-specific
    fields (e.g. ``next_prompt``, ``question``, ``phase``).
    """

    kind: str
    args: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class HarnessSession(Protocol):
    session_id: str

    def events(self) -> AsyncIterator[HarnessEvent]:
        """Async iterator of normalized events for this session."""
        ...

    async def cancel(self) -> None: ...

    async def wait(self) -> SessionEnded: ...


@runtime_checkable
class Harness(Protocol):
    name: str

    async def spawn(
        self,
        prompt: str,
        cwd: Path,
        env: dict[str, str],
        signal_config: SignalConfig,
        resume_from: str | None = None,
        skill_paths: list[Path] | None = None,
    ) -> HarnessSession: ...
