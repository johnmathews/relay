"""Unit tests for RelayCore's fanout-join watcher (9c).

Covers:
- _collect_child_results: shape + ordering by started_at.
- _maybe_resume_parent: skip-when-not-awaiting; skip-when-some-running;
  emits subagent_return/child_runs_resolved and re-enqueues when all
  children terminal; mixed-status (partial-failure) still resumes;
  double-fire idempotency.

All scripted (no pi). Direct DB seeding for the watcher's preconditions.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay.config import Settings
from relay.core import RelayCore
from relay.db import init_db
from relay.db.models import Event, Run
from relay.orchestrator.lifecycle import (
    close_iter,
    create_run,
    open_iter,
    set_run_status,
)
from tests.orchestrator.scripted_harness import ScriptedHarness


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


async def _seed_fanout_state(
    core: RelayCore,
    project_root: Path,
    *,
    child_statuses: list[str],
    child_summaries: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Seed a parent in awaiting_children with N child rows + their
    closing run_ended events.

    Returns (parent_run_id, [child_run_id, ...]).
    """
    project_id = await core.register_project(project_root, "p")
    parent_id = core._new_run_id()
    await create_run(
        core._sm, run_id=parent_id, project_id=project_id,
        prompt_body="parent", max_iters=4, iter_timeout=60,
        worktree_path=str(project_root), branch=None,
    )
    # Parent run_started + closing fanout iter with the payload 9b would
    # have written. The synthesizer needs join_prompt from here.
    await core._store.append(
        parent_id, "run_started",
        {"project_id": project_id, "prompt_body": "parent", "max_iters": 4},
    )
    iter_id = await open_iter(
        core._sm, run_id=parent_id, seq=1, phase=None,
        prompt="parent", preamble="",
    )
    await close_iter(
        core._sm, iter_id, signal_kind="fanout",
        signal_args={"payload": {
            "children": [
                {"role": f"r-{i}", "prompt": f"do {i}"}
                for i in range(len(child_statuses))
            ],
            "join_prompt": "Synthesize.",
        }},
        exit_reason="signal",
    )
    await set_run_status(core._sm, parent_id, "awaiting_children",
                        ended=False)

    child_ids: list[str] = []
    summaries = child_summaries or [
        f"child {i} ok" for i in range(len(child_statuses))
    ]
    for i, (status, summary) in enumerate(
        zip(child_statuses, summaries, strict=True)
    ):
        cid = core._new_run_id()
        await create_run(
            core._sm, run_id=cid, project_id=project_id,
            prompt_body=f"do {i}", max_iters=4, iter_timeout=60,
            worktree_path=str(project_root / f"wt-{i}"),
            branch=f"relay/{cid}", parent_run_id=parent_id,
        )
        await core._store.append(
            cid, "run_started",
            {"project_id": project_id, "prompt_body": f"do {i}",
             "max_iters": 4},
        )
        await set_run_status(core._sm, cid, status, ended=True)
        await core._store.append(
            cid, "run_ended", {"status": status, "summary": summary},
        )
        child_ids.append(cid)
    return parent_id, child_ids


def test_collect_child_results_returns_one_per_child(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario() -> list[dict[str, Any]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["frontend ok", "backend ok"],
            )
            return await core._collect_child_results(parent_id)
        finally:
            await core._engine.dispose()

    results = asyncio.run(scenario())
    assert len(results) == 2
    statuses = {r["status"] for r in results}
    summaries = {r["summary"] for r in results}
    assert statuses == {"done"}
    assert summaries == {"frontend ok", "backend ok"}
    for r in results:
        assert set(r.keys()) >= {
            "id", "role", "status", "summary", "branch", "worktree_path"
        }
        assert r["branch"].startswith("relay/")


def test_collect_child_results_uses_subagent_dispatch_role(
    tmp_path: Path,
) -> None:
    """Role comes from the parent's subagent_dispatch event payload, not
    a column on the child run (we don't store it there).
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[list[dict[str, Any]], list[str]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path, child_statuses=["done", "done"],
            )
            # Emit subagent_dispatch events on the parent — the watcher
            # joins these to children by child_run_id to recover role.
            for i, cid in enumerate(child_ids):
                await core._store.append(
                    parent_id, "subagent_dispatch",
                    {"child_run_id": cid, "role": f"role-{i}",
                     "prompt": f"p-{i}"},
                )
            return await core._collect_child_results(parent_id), child_ids
        finally:
            await core._engine.dispose()

    results, child_ids = asyncio.run(scenario())
    by_id = {r["id"]: r for r in results}
    assert by_id[child_ids[0]]["role"] == "role-0"
    assert by_id[child_ids[1]]["role"] == "role-1"


def test_collect_child_results_includes_all_children(tmp_path: Path) -> None:
    """Three children dispatched → trailer has three entries, regardless
    of within-second ordering. (Strict dispatch-order is not asserted
    here — SQLite current_timestamp is second-precision so two
    same-second inserts have identical ``started_at``; the secondary
    ``id`` tiebreaker is timestamp-prefixed + random hex, non-deterministic
    within a second. The trailer's stability for the agent is "all N
    children present, status field correct"; the strict dispatch order
    is enforced indirectly through the parent's ``subagent_dispatch``
    event seqs which downstream tooling can correlate.)
    """
    settings = _settings(tmp_path)

    async def scenario() -> list[str]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done", "done"],
            )
            results = await core._collect_child_results(parent_id)
            return [r["id"] for r in results]
        finally:
            await core._engine.dispose()

    returned_ids = asyncio.run(scenario())
    assert len(returned_ids) == 3
    assert len(set(returned_ids)) == 3  # no duplicates


def test_maybe_resume_parent_no_op_when_parent_not_awaiting(
    tmp_path: Path,
) -> None:
    """Cascade-cancelled / already-resumed parent: watcher returns
    silently, emits no events, enqueues nothing.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            # Flip parent off awaiting_children (simulate cascade-cancel
            # or an already-fired watcher).
            await set_run_status(core._sm, parent_id, "cancelled",
                                 ended=True)
            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            qsize_after = core._queue.qsize()
            return parent_id, qsize_after - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 0

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            ]
            assert "subagent_return" not in kinds
            assert "child_runs_resolved" not in kinds
    finally:
        engine.dispose()


def test_maybe_resume_parent_no_op_when_some_children_still_running(
    tmp_path: Path,
) -> None:
    """Watcher must NOT resume if any sibling is still non-terminal."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "running"],
            )
            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            return parent_id, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 0

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "awaiting_children"
            assert "subagent_return" not in {
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            }
    finally:
        engine.dispose()


def test_maybe_resume_parent_no_op_when_parent_unknown(
    tmp_path: Path,
) -> None:
    """Unknown parent id — never raises."""
    settings = _settings(tmp_path)

    async def scenario() -> None:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            # Should be a silent no-op, not a raise.
            await core._maybe_resume_parent("does-not-exist")
        finally:
            await core._engine.dispose()

    asyncio.run(scenario())


def test_maybe_resume_parent_emits_events_and_enqueues(
    tmp_path: Path,
) -> None:
    """Happy path: two children done → two subagent_return + one
    child_runs_resolved + parent status running + one new queue entry.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, list[str], int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["frontend ok", "backend ok"],
            )
            # Need a _RunState entry so resume can install a fresh one;
            # simulate "parent's first task settled" exactly like _run
            # would have left it.
            from relay.core import _RunState
            from relay.orchestrator.loop import LoopResult
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].result = LoopResult(
                "awaiting_children", reason="signal",
            )
            core._runs[parent_id].settled.set()

            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            return parent_id, child_ids, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, child_ids, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 1, "synthesizer RunContext should be enqueued"

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "running"
            assert parent.ended_at is None

            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                    .order_by(Event.seq.asc())
                )
            ]
            assert kinds.count("subagent_return") == 2
            assert kinds.count("child_runs_resolved") == 1
            # Ordering: all subagent_return events precede child_runs_resolved.
            first_resolved = kinds.index("child_runs_resolved")
            last_return = max(
                i for i, k in enumerate(kinds) if k == "subagent_return"
            )
            assert last_return < first_resolved

            returns = list(s.scalars(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "subagent_return",
                ).order_by(Event.seq.asc())
            ))
            return_ids = {e.payload["child_run_id"] for e in returns}
            assert return_ids == set(child_ids)
            for r in returns:
                assert r.payload["status"] == "done"
                assert r.payload["summary"] in {"frontend ok", "backend ok"}

            resolved = s.scalar(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "child_runs_resolved",
                )
            )
            assert resolved is not None
            assert resolved.payload["children_count"] == 2
            assert set(resolved.payload["terminal_statuses"].keys()) == set(
                child_ids
            )
            assert all(
                v == "done"
                for v in resolved.payload["terminal_statuses"].values()
            )
    finally:
        engine.dispose()


def test_maybe_resume_parent_synthesizer_runcontext_body(
    tmp_path: Path,
) -> None:
    """The enqueued RunContext.body must start with join_prompt and
    contain a RELAY_CHILD_RESULTS trailer with one entry per child.
    """
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["a ok", "b ok"],
            )
            from relay.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()

            await core._maybe_resume_parent(parent_id)
            # Peek the queue without blocking the supervisor.
            ctx = await core._queue.get()
            core._queue.task_done()
            return ctx.body
        finally:
            await core._engine.dispose()

    body = asyncio.run(scenario())
    assert body.startswith("Synthesize.")
    assert "RELAY_CHILD_RESULTS:" in body
    assert body.count("- id: ") == 2


def test_maybe_resume_parent_continues_iter_seq(tmp_path: Path) -> None:
    """The synthesizer iter must continue from the closing fanout iter's
    seq + 1 (the loop does seq += 1 on entry, so start_seq is the
    closing iter's seq).
    """
    settings = _settings(tmp_path)

    async def scenario() -> int:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            from relay.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()
            await core._maybe_resume_parent(parent_id)
            ctx = await core._queue.get()
            core._queue.task_done()
            return ctx.start_seq
        finally:
            await core._engine.dispose()

    start_seq = asyncio.run(scenario())
    # Closing fanout iter was seq=1; synthesizer continues from there.
    assert start_seq == 1


def test_maybe_resume_parent_partial_failure_still_resumes(
    tmp_path: Path,
) -> None:
    """Mixed child outcomes — one done, one failed, one cancelled —
    still resumes the parent. OCQ-6: orchestrator never auto-fails the
    parent on a child's failure; the agent decides via the trailer.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, str]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "failed", "cancelled"],
                child_summaries=["ok", "timed out", "user cancelled"],
            )
            from relay.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()
            await core._maybe_resume_parent(parent_id)
            ctx = await core._queue.get()
            core._queue.task_done()
            return parent_id, ctx.body
        finally:
            await core._engine.dispose()

    parent_id, body = asyncio.run(scenario())
    assert "  status: done" in body
    assert "  status: failed" in body
    assert "  status: cancelled" in body

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "running"  # parent resumed, NOT failed
    finally:
        engine.dispose()


def test_maybe_resume_parent_idempotent_under_double_fire(
    tmp_path: Path,
) -> None:
    """Two concurrent watcher calls (last two children settle near-
    simultaneously) must not double-resume — exactly one enqueue, one
    set of return events, one child_runs_resolved.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            from relay.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()

            qsize_before = core._queue.qsize()
            # Fire twice concurrently.
            await asyncio.gather(
                core._maybe_resume_parent(parent_id),
                core._maybe_resume_parent(parent_id),
            )
            return parent_id, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 1, "exactly one synthesizer should be enqueued"

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            ]
            assert kinds.count("subagent_return") == 2
            assert kinds.count("child_runs_resolved") == 1
    finally:
        engine.dispose()
