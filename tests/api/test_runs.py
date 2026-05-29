"""Route tests for GET /api/runs/{run_id}/children and the
include_children query param on GET /api/runs (Phase 9e).

Uses the same asyncio.run + ASGITransport pattern as test_w2_routes.py.
The _client_with_core context manager exposes both the ASGI client and
the live RelayCore (via app.state.core) so tests can seed child rows
directly via create_run without going through the fanout sentinel path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from relay.app import create_app
from relay.config import Settings
from relay.core import RelayCore
from relay.harness.protocol import Harness
from relay.orchestrator.lifecycle import create_run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


@asynccontextmanager
async def _client_with_core(
    settings: Settings,
    harness: Harness | None = None,
) -> AsyncIterator[tuple[AsyncClient, RelayCore]]:
    """Build the app with a scripted harness, enter the lifespan, and
    yield (AsyncClient, RelayCore).  ``app.state.core`` is set by the
    lifespan so it is always available inside this context.

    Pass *harness* to override the default single-script DONE_BLOCK
    harness (useful for integration tests that need a full fanout
    sequence).
    """
    if harness is None:
        harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    app = create_app(settings, harness=harness)
    async with app.router.lifespan_context(app):
        core: RelayCore = app.state.core
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac, core


# ── helpers ────────────────────────────────────────────────────────────


async def _register_project(ac: AsyncClient, root: Path) -> int:
    r = await ac.post(
        "/api/projects", json={"root_path": str(root), "name": "p"}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _start_run(
    ac: AsyncClient, project_id: int, prompt: str
) -> str:
    r = await ac.post(
        "/api/runs",
        json={"project_id": project_id, "prompt_body": prompt},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _seed_child(
    core: RelayCore,
    project_id: int,
    parent_run_id: str,
    prompt_body: str,
) -> str:
    """Insert a child run row directly (no fanout sentinel needed)."""
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


# ── GET /api/runs/{run_id}/children ────────────────────────────────────


def test_get_run_children_empty(tmp_path: Path) -> None:
    """A run that never fanned out returns an empty children list."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, _core):
            project_id = await _register_project(ac, proj_root)
            run_id = await _start_run(ac, project_id, "hello")

            res = await ac.get(f"/api/runs/{run_id}/children")
            assert res.status_code == 200
            assert res.json() == []

    asyncio.run(body())


def test_get_run_children_unknown_run(tmp_path: Path) -> None:
    """Unknown run → 404."""
    s = _settings(tmp_path)

    async def body() -> None:
        async with _client_with_core(s) as (ac, _core):
            res = await ac.get("/api/runs/unknown-run-id/children")
            assert res.status_code == 404

    asyncio.run(body())


def test_get_run_children_returns_direct_children(tmp_path: Path) -> None:
    """A parent with two direct children returns them as list[RunOut].

    Order is not pinned here — ordering correctness is covered by the
    test_relay_core.py unit test which backdates timestamps.  We only
    assert set membership + that every row is a full RunOut with the
    expected parent_run_id.
    """
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            parent_id = await _start_run(ac, project_id, "parent")

            child_a = await _seed_child(core, project_id, parent_id, "child-a")
            child_b = await _seed_child(core, project_id, parent_id, "child-b")

            res = await ac.get(f"/api/runs/{parent_id}/children")
            assert res.status_code == 200
            body_json: list[dict[str, Any]] = res.json()

            assert {row["id"] for row in body_json} == {child_a, child_b}
            # Every row is a full RunOut with the correct parent link.
            for row in body_json:
                assert row["parent_run_id"] == parent_id
                assert "status" in row
                assert "branch" in row

    asyncio.run(body())


# ── GET /api/runs ?include_children ────────────────────────────────────


def test_list_runs_excludes_children_by_default(tmp_path: Path) -> None:
    """GET /api/runs returns only top-level runs by default."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            parent_id = await _start_run(ac, project_id, "parent")
            _child_id = await _seed_child(core, project_id, parent_id, "child")

            res = await ac.get("/api/runs", params={"project_id": project_id})
            assert res.status_code == 200
            body_json: list[dict[str, Any]] = res.json()
            assert {row["id"] for row in body_json} == {parent_id}

    asyncio.run(body())


def test_list_runs_includes_children_when_requested(tmp_path: Path) -> None:
    """GET /api/runs?include_children=true returns child runs too."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            parent_id = await _start_run(ac, project_id, "parent")
            child_id = await _seed_child(core, project_id, parent_id, "child")

            res = await ac.get(
                "/api/runs",
                params={"project_id": project_id, "include_children": "true"},
            )
            assert res.status_code == 200
            body_json: list[dict[str, Any]] = res.json()
            assert {row["id"] for row in body_json} == {parent_id, child_id}

    asyncio.run(body())


# ── REST integration: scripted fanout → children endpoint ──────────────


# Fanout sentinel identical to the one used in test_fanout_integration.py.
_FANOUT_TWO = (
    "Dispatching two explorers.\n\n"
    "[[engteam:fanout-start]]\n"
    "{"
    '"children": ['
    '{"role": "explorer-frontend", "prompt": "Audit frontend."},'
    '{"role": "explorer-backend", "prompt": "Audit backend."}'
    "],"
    '"join_prompt": "Synthesize the two audits."'
    "}\n"
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
_DONE = "Audit complete.\n\n[[engteam:done]]"


def test_get_run_children_after_scripted_fanout(tmp_path: Path) -> None:
    """Full REST integration proof for the four 9e backend tasks.

    Uses a ScriptedHarness that drives the complete fanout-join
    sequence (parent fanout, child A done, child B done, synthesizer
    done) through the real app lifespan and REST layer, then asserts:

    1. GET /api/runs/{parent_id}/children returns 2 rows with the
       correct parent_run_id and done status.
    2. GET /api/runs (no include_children) shows only the parent.
    3. GET /api/runs?include_children=true shows all three runs.

    This bridges the gap between the unit/route tests above (which seed
    children directly via create_run) and the deferred manual
    scripted-harness smoke — it exercises Tasks 1-4 composing correctly
    end-to-end through real REST calls.
    """
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    # Scripts: parent fanout iter, child A done, child B done, synthesizer done.
    fanout_harness = ScriptedHarness(
        [
            TextScript(_FANOUT_TWO),
            TextScript(_DONE),
            TextScript(_DONE),
            TextScript(_DONE),
        ]
    )

    async def body() -> None:
        async with _client_with_core(s, harness=fanout_harness) as (ac, core):
            project_id = await _register_project(ac, proj_root)

            # Start the parent run via REST.
            parent_id = await _start_run(ac, project_id, "Investigate the system.")

            # First settle: parent reaches awaiting_children after dispatching.
            first = await core.wait_for_run(parent_id)
            assert first.status == "awaiting_children", (
                f"expected awaiting_children, got {first.status}"
            )

            # Wait for each child to settle before waiting for the synthesizer.
            children_snapshot = await core.list_children(parent_id)
            for child in children_snapshot:
                await core.wait_for_run(child.id)

            # Second settle: synthesizer iter runs and parent reaches done.
            final = await core.wait_for_run(parent_id)
            assert final.status == "done", (
                f"expected parent done, got {final.status}"
            )

            # 1. GET /api/runs/{parent_id}/children — must return 2 children.
            res = await ac.get(f"/api/runs/{parent_id}/children")
            assert res.status_code == 200
            children: list[dict[str, Any]] = res.json()
            assert len(children) == 2, f"expected 2 children, got {len(children)}"
            for child in children:
                assert child["parent_run_id"] == parent_id
                assert child["status"] == "done", (
                    f"child {child['id']} status: {child['status']}"
                )
            child_ids = {child["id"] for child in children}

            # 2. GET /api/runs (default: top-level only) — parent only.
            res2 = await ac.get("/api/runs", params={"project_id": project_id})
            assert res2.status_code == 200
            top_level_ids = {row["id"] for row in res2.json()}
            assert top_level_ids == {parent_id}, (
                f"top-level list should contain only parent; got {top_level_ids}"
            )

            # 3. GET /api/runs?include_children=true — all three runs.
            res3 = await ac.get(
                "/api/runs",
                params={"project_id": project_id, "include_children": "true"},
            )
            assert res3.status_code == 200
            all_ids = {row["id"] for row in res3.json()}
            assert all_ids == {parent_id} | child_ids, (
                f"full list should be parent + 2 children; got {all_ids}"
            )

    asyncio.run(body())


# ── DELETE /api/runs/{run_id} ──────────────────────────────────────────


def test_delete_run_unknown_returns_404(tmp_path: Path) -> None:
    s = _settings(tmp_path)

    async def body() -> None:
        async with _client_with_core(s) as (ac, _core):
            res = await ac.delete("/api/runs/nope")
            assert res.status_code == 404

    asyncio.run(body())


def test_delete_run_terminal_returns_204(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id = await _start_run(ac, project_id, "hello")
            await core.wait_for_run(run_id)  # settles to done

            res = await ac.delete(f"/api/runs/{run_id}")
            assert res.status_code == 204
            # Subsequent GET → 404.
            assert (await ac.get(f"/api/runs/{run_id}")).status_code == 404

    asyncio.run(body())


def test_delete_run_active_returns_409(tmp_path: Path) -> None:
    """A row in 'running' status (e.g. a prior process left it stuck before
    orphan recovery sweeps it) must be cancelled first."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id = await _start_run(ac, project_id, "hello")
            await core.wait_for_run(run_id)
            # Force the row back to 'running' to simulate an active run
            # (the scripted harness already settled, so no real task races us).
            from relay.orchestrator.lifecycle import set_run_status
            await set_run_status(core._sm, run_id, "running", ended=False)

            res = await ac.delete(f"/api/runs/{run_id}")
            assert res.status_code == 409, res.text
            assert "running" in res.json()["detail"]

    asyncio.run(body())
