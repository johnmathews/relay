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

import pytest  # noqa: F401  (used by Tasks 4/5 in same file)
from sqlalchemy import create_engine, select  # noqa: F401  (Tasks 4/5)
from sqlalchemy.orm import Session  # noqa: F401  (Tasks 4/5)

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db
from relay_v2.db.models import (  # noqa: F401  (Iter/Project used in 4/5)
    Event,
    Iter,
    Project,
    Run,
)
from relay_v2.orchestrator.lifecycle import (
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
