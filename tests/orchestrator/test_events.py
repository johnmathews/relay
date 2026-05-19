"""EventStore integrity invariants (W5).

The event log is the single source of truth (ADR-10). Two mechanisms
that Phase 3 (SSE / restart) leans on were previously untested: the
``_next_seq`` cold-cache reseed from the DB, and the ``tool_use_end``
result truncation cap. These tests lock both down. SQLite does not
enforce FKs (no ``PRAGMA foreign_keys=ON`` anywhere), so a bare string
``run_id`` exercises exactly the production write path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import select

from relay_v2.config import Settings
from relay_v2.db import init_db, make_async_engine, make_async_sessionmaker
from relay_v2.db.models import Event
from relay_v2.events import TOOL_RESULT_CAP, EventStore, _truncate_result
from relay_v2.harness.protocol import (
    SessionEnded,
    SessionStarted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)


@asynccontextmanager
async def _store(
    tmp_path: Path,
) -> AsyncIterator[tuple[Settings, EventStore]]:
    """Yield an EventStore over a fresh DB and dispose its engines.

    The sync `init_db` bootstrap engine is disposed immediately (it only
    runs `create_all`); the async engine is disposed in `finally` inside
    the caller's event loop — otherwise the aiosqlite connection is GC'd
    unclosed and warns."""
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()  # sync bootstrap engine — done after DDL
    engine = make_async_engine(settings.async_db_url)
    try:
        yield settings, EventStore(make_async_sessionmaker(engine))
    finally:
        await engine.dispose()


def test_truncate_result_over_cap_pure() -> None:
    small = {"data": "ok"}
    assert _truncate_result(small) is small  # unchanged, same object

    big = _truncate_result({"data": "x" * (TOOL_RESULT_CAP + 5000)})
    assert big["_truncated"] is True
    assert big["_original_bytes"] > TOOL_RESULT_CAP
    assert len(big["preview"]) == TOOL_RESULT_CAP


def test_event_store_seq_reseed_on_restart(tmp_path: Path) -> None:
    """A second EventStore over the same DB (cold seq cache, i.e. a
    process restart) continues the per-run seq from the DB max, not 1 —
    otherwise a resumed run violates UNIQUE(run_id, seq)."""
    async def scenario() -> tuple[list[int], int]:
        async with _store(tmp_path) as (settings, store1):
            first = [
                await store1.append("r1", "iter_started", {"n": i})
                for i in range(3)
            ]
            # New EventStore == cold cache == simulated process restart.
            engine2 = make_async_engine(settings.async_db_url)
            try:
                store2 = EventStore(make_async_sessionmaker(engine2))
                after_restart = await store2.append(
                    "r1", "iter_started", {"n": 3}
                )
            finally:
                await engine2.dispose()
            return first, after_restart

    first, after_restart = asyncio.run(scenario())
    assert first == [1, 2, 3]
    assert after_restart == 4  # reseeded from DB max(seq)=3, not 1


def test_store_harness_event_tool_branches(tmp_path: Path) -> None:
    """ToolUseStart/End persist; ToolUseUpdate/SessionStarted/
    SessionEnded are intentionally dropped (no event kind for them)."""
    async def scenario() -> list[str]:
        async with _store(tmp_path) as (_settings, store):
            await store.store_harness_event(
                "r1", 1, ToolUseStart(1, 0.0, "t1", "Bash", {"cmd": "ls"})
            )
            await store.store_harness_event(
                "r1", 1, ToolUseEnd(2, 0.0, "t1", {"out": "ok"}, False, 5)
            )
            await store.store_harness_event(
                "r1", 1, ToolUseUpdate(3, 0.0, "t1", {"partial": 1})
            )
            await store.store_harness_event(
                "r1", 1, SessionStarted(4, 0.0, "sid", "/cwd")
            )
            await store.store_harness_event(
                "r1", 1, SessionEnded(5, 0.0, [], "clean")
            )
            async with store.sessionmaker() as s:
                rows = list(
                    await s.scalars(
                        select(Event).where(Event.run_id == "r1")
                        .order_by(Event.seq)
                    )
                )
            return [r.kind for r in rows]

    kinds = asyncio.run(scenario())
    assert kinds == ["tool_use_start", "tool_use_end"]


def test_store_harness_event_truncates_large_tool_result(
    tmp_path: Path,
) -> None:
    async def scenario() -> dict[str, object]:
        async with _store(tmp_path) as (_settings, store):
            big = {"blob": "y" * (TOOL_RESULT_CAP + 9000)}
            await store.store_harness_event(
                "r1", 1, ToolUseEnd(1, 0.0, "t1", big, False, 9)
            )
            async with store.sessionmaker() as s:
                row = (
                    await s.scalars(
                        select(Event).where(Event.kind == "tool_use_end")
                    )
                ).one()
            return row.payload["result"]

    result = asyncio.run(scenario())
    assert result["_truncated"] is True
    assert result["_original_bytes"] > TOOL_RESULT_CAP
