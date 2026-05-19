"""EventStore — the append-only event-log writer (ADR-10, spec.md §3.2).

The ``events`` table is the single source of truth for observability.
Every observable action is one append-only row; status transitions are
**new events**, never in-place rewrites of an earlier row. The mutable
projection columns (``runs.status``, ``iters.exit_reason`` …) are a
convenience view the dashboard can read cheaply — each transition that
touches them *also* appends the corresponding event here, so the log
alone fully reconstructs a run.

The orchestrator is the sole writer (ADR-07/ADR-15). A single
``asyncio.Lock`` serialises the seq-assignment + insert so per-run
``seq`` is strictly monotonic even with several runs active at once;
this also serialises SQLite's single-writer file.

Tool-result truncation lives here, not in the harness (plan.md Phase 1
follow-up): the harness passes payloads through verbatim; the write
layer is where unbounded ``tool_use_end`` results get capped.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from relay_v2.db.models import Event
from relay_v2.harness import (
    AssistantText,
    HarnessEvent,
    SessionStarted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)

__all__ = ["EventStore", "TOOL_RESULT_CAP"]

# Max characters of a JSON-serialised tool result kept verbatim. Beyond
# this the payload is replaced with a bounded preview + size marker so a
# pathological tool dump can't bloat the event log.
TOOL_RESULT_CAP = 16_384


def _truncate_result(result: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(result, default=str)
    if len(encoded) <= TOOL_RESULT_CAP:
        return result
    return {
        "_truncated": True,
        "_original_bytes": len(encoded),
        "preview": encoded[:TOOL_RESULT_CAP],
    }


class EventStore:
    """Append-only writer over the ``events`` table."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._lock = asyncio.Lock()
        self._seq: dict[str, int] = {}

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        """The one async session factory. The loop reuses it for its
        small iter-row writes so DB access is a single injected dep."""
        return self._sessionmaker

    async def _next_seq(self, run_id: str) -> int:
        """Monotonic per-run seq. Seeded from the DB the first time a run
        is seen so a resumed process continues the sequence instead of
        restarting at 1."""
        cached = self._seq.get(run_id)
        if cached is None:
            async with self._sessionmaker() as s:
                current = await s.scalar(
                    select(func.max(Event.seq)).where(Event.run_id == run_id)
                )
            cached = int(current or 0)
        self._seq[run_id] = cached + 1
        return self._seq[run_id]

    async def append(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        iter_id: int | None = None,
    ) -> int:
        """Append one event; return its per-run ``seq``."""
        async with self._lock:
            seq = await self._next_seq(run_id)
            async with self._sessionmaker() as s:
                s.add(
                    Event(
                        run_id=run_id,
                        iter_id=iter_id,
                        seq=seq,
                        kind=kind,
                        payload=payload,
                    )
                )
                await s.commit()
            return seq

    async def store_harness_event(
        self, run_id: str, iter_id: int, ev: HarnessEvent
    ) -> None:
        """Map a normalized :class:`HarnessEvent` to an ``events`` row.

        ``SessionStarted`` / ``SessionEnded`` are loop-lifecycle facts
        recorded as ``iter_*`` events by the loop, not here. ``ToolUseUpdate``
        is high-frequency partial noise and is intentionally not persisted
        (spec.md §3.2 has no event kind for it).
        """
        if isinstance(ev, AssistantText):
            await self.append(
                run_id,
                "assistant_text",
                {"text": ev.text, "turn_seq": ev.turn_seq, "kind": ev.kind},
                iter_id=iter_id,
            )
        elif isinstance(ev, ToolUseStart):
            await self.append(
                run_id,
                "tool_use_start",
                {"tool_id": ev.tool_id, "name": ev.name, "args": ev.args},
                iter_id=iter_id,
            )
        elif isinstance(ev, ToolUseEnd):
            await self.append(
                run_id,
                "tool_use_end",
                {
                    "tool_id": ev.tool_id,
                    "result": _truncate_result(ev.result),
                    "is_error": ev.is_error,
                    "duration_ms": ev.duration_ms,
                },
                iter_id=iter_id,
            )
        elif isinstance(ev, (SessionStarted, ToolUseUpdate)):
            return
