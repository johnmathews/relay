"""Route tests for GET /api/runs/{run_id}/children (Phase 9e).

Uses the same asyncio.run + ASGITransport pattern as test_w2_routes.py.
The _client_with_core context manager exposes both the ASGI client and
the live RelayCore (via app.state.core) so tests can seed child rows
directly via create_run without going through the fanout sentinel path.

Task 4 will extend this file with tests for the include_children query
param on GET /api/runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from relay_v2.app import create_app
from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.orchestrator.lifecycle import create_run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


@asynccontextmanager
async def _client_with_core(
    settings: Settings,
) -> AsyncIterator[tuple[AsyncClient, RelayCore]]:
    """Build the app with a scripted harness, enter the lifespan, and
    yield (AsyncClient, RelayCore).  ``app.state.core`` is set by the
    lifespan so it is always available inside this context."""
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
