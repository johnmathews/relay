"""Unit tests for RelayCore._dispatch_children and depth enforcement (9b)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Event, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE = "Done.\n\n[[engteam:done]]"
FANOUT_TWO = (
    "Dispatching.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "a", "prompt": "Do A."}, '
    '{"role": "b", "prompt": "Do B."}], "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(data_dir=tmp_path / ".relay", **kw)


def _run_sync(
    coro: Callable[[RelayCore], Awaitable[Any]],
    settings: Settings,
    harness: ScriptedHarness,
) -> Any:
    async def _main() -> Any:
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()
    return asyncio.run(_main())


def test_dispatch_creates_two_child_runs_with_parent_run_id(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> tuple[str, list[str]]:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                children = list(
                    s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                )
        finally:
            engine.dispose()
        child_ids = [c.id for c in children]
        for cid in child_ids:
            await core.wait_for_run(cid)
        return parent_id, child_ids

    parent_id, child_ids = _run_sync(scenario, settings, harness)

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None and parent.status == "awaiting_children"
            assert parent.ended_at is None
            assert len(child_ids) == 2
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.parent_run_id == parent_id
                assert child.status == "done"
            dispatches = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
            assert len(dispatches) == 2
            roles = {e.payload["role"] for e in dispatches}
            assert roles == {"a", "b"}
    finally:
        engine.dispose()


def test_subagent_dispatch_events_are_iter_scoped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        return parent_id

    parent_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            dispatches = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
            assert all(e.iter_id is not None for e in dispatches)
    finally:
        engine.dispose()


def test_dispatch_depth_limit_fails_child_run(tmp_path: Path) -> None:
    """Child at depth 1 trying to fanout when max_fanout_depth=1 fails."""
    settings = _settings(tmp_path, max_fanout_depth=1)
    # parent fanouts -> child-a tries to fanout (exceeds cap) -> child-b done
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(FANOUT_TWO), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> tuple[str, list[str]]:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                children = list(
                    s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                )
        finally:
            engine.dispose()
        child_ids = [c.id for c in children]
        for cid in child_ids:
            await core.wait_for_run(cid)
        return parent_id, child_ids

    parent_id, child_ids = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            statuses = {s.get(Run, cid).status for cid in child_ids}  # type: ignore[union-attr]
            assert "failed" in statuses  # depth-exceeded child fails
            assert "done" in statuses    # other child succeeds
    finally:
        engine.dispose()


def test_parent_run_no_run_ended_event(tmp_path: Path) -> None:
    """awaiting_children parent must have no run_ended event (9c's territory)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        return parent_id

    parent_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id).order_by(Event.seq)
                )
            ]
            assert "run_ended" not in kinds
    finally:
        engine.dispose()
