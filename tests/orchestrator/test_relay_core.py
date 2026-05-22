"""Unit tests for RelayCore service-layer methods (Phase 9e).

Covers:
- list_children: empty for a run with no fanout; direct children only
  (not grandchildren); ordered by started_at asc.

Uses bare ``async def test_*`` with pytest-asyncio auto mode (ADR-24).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


# ── helpers ────────────────────────────────────────────────────────────


async def _make_core(
    tmp_path: Path,
) -> tuple[RelayCore, Settings]:
    """Return a started RelayCore + its Settings for a throw-away data dir."""
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()
    core = RelayCore(
        settings,
        harness=ScriptedHarness([TextScript(DONE_BLOCK)]),
    )
    await core.start()
    return core, settings


async def _make_project(core: RelayCore, project_root: Path) -> int:
    """Register a project and return its id."""
    project_root.mkdir(parents=True, exist_ok=True)
    return await core.register_project(project_root, "test-project")


async def _make_child_run(
    core: RelayCore,
    project_id: int,
    parent_run_id: str,
    prompt_body: str,
) -> str:
    """Insert a child run row directly (no fanout sentinel)."""
    from relay_v2.orchestrator.lifecycle import create_run

    child_id = core._new_run_id()
    await create_run(
        core._sm,
        run_id=child_id,
        project_id=project_id,
        prompt_body=prompt_body,
        max_iters=1,
        iter_timeout=60,
        worktree_path=None,
        branch=None,
        parent_run_id=parent_run_id,
    )
    return child_id


# ── list_children ──────────────────────────────────────────────────────


async def test_list_children_empty_for_run_without_fanout(
    tmp_path: Path,
) -> None:
    """A run with no fanout has no children — empty list, not None."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        run_id = await core.start_run(project_id, "hello", max_iters=1)
        children = await core.list_children(run_id)
        assert children == []
    finally:
        await core.aclose()


async def test_list_children_returns_direct_children_only(
    tmp_path: Path,
) -> None:
    """list_children returns rows where parent_run_id == argument.

    Ordered by started_at asc. Recursive (grandchildren) are out of scope for
    9e — the pane renders a flat list per direct child.
    """
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        # Directly insert two children + one grandchild via the DB layer (no
        # fanout sentinel needed — we're testing list_children, not dispatch).
        #
        # Insertion order is REVERSED from the expected ASC timestamp order so
        # that the ORDER BY actually does work: without it, SQLite returns rows
        # in insertion order [child_b, child_a]; with it, the backdated
        # started_at on child_a forces [child_a, child_b].  Both orderings
        # agree only when ORDER BY is present.
        child_b = await _make_child_run(core, project_id, parent_id, "child-b")
        child_a = await _make_child_run(core, project_id, parent_id, "child-a")
        _grandchild = await _make_child_run(core, project_id, child_a, "grandchild")

        # SQLite's current_timestamp has 1-second granularity — back-to-back
        # inserts collide.  Backdate child_a so its started_at is strictly
        # earlier than child_b's, making the desired ORDER BY effect visible.
        from sqlalchemy import update

        from relay_v2.db.models import Run

        async with core._sm() as s:
            await s.execute(
                update(Run)
                .where(Run.id == child_a)
                .values(
                    started_at=datetime.now(UTC) - timedelta(seconds=5)
                )
            )
            await s.commit()

        direct = await core.list_children(parent_id)
        # Set equality covers presence; the list equality below also pins order.
        assert {r.id for r in direct} == {child_a, child_b}
        # child_a has the earlier started_at, so ORDER BY started_at ASC must
        # return it first — child_b was inserted first (reversed order) so
        # without ORDER BY the result would be [child_b, child_a], failing here.
        assert [r.id for r in direct] == [child_a, child_b]
    finally:
        await core.aclose()


# ── list_runs ──────────────────────────────────────────────────────────


async def test_list_runs_excludes_children_by_default(
    tmp_path: Path,
) -> None:
    """list_runs() default behaviour: top-level rows only (parent_run_id IS NULL)."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        _child_id = await _make_child_run(core, project_id, parent_id, "child")

        rows = await core.list_runs(project_id)
        assert {r.id for r in rows} == {parent_id}
    finally:
        await core.aclose()


async def test_list_runs_includes_children_when_requested(
    tmp_path: Path,
) -> None:
    """list_runs(include_children=True) returns the full set."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        child_id = await _make_child_run(core, project_id, parent_id, "child")

        rows = await core.list_runs(project_id, include_children=True)
        assert {r.id for r in rows} == {parent_id, child_id}
    finally:
        await core.aclose()
