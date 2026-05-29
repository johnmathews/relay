"""W2: REST routes for runs / projects / prompts (Phase 3).

Routes are thin adapters over the single shared ``RelayCore`` (ADR-07/
ADR-15). These tests drive the real ``create_app`` + lifespan against a
scripted harness double (the ``harness=`` injection seam added to
``create_app``) so ``POST /api/runs`` never spawns pi — pi e2e stays
gated behind ``PI_INTEGRATION=1``.

pytest-asyncio is not globally enabled yet (W5 turns on
``asyncio_mode=auto``); follow ``tests/orchestrator/``'s ``asyncio.run``
wrapper pattern. The app's lifespan is entered via
``app.router.lifespan_context(app)`` so ``app.state.core`` exists.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from relay.app import create_app
from relay.config import Settings
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """Build the app with a scripted harness, enter the lifespan (sets
    ``app.state.core``), and yield an ASGI client. ``create_app`` already
    mounts every Phase 3 router via ``include_api_routers`` — do not mount
    again here or routes/OpenAPI paths double up."""
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    app = create_app(settings, harness=harness)
    async with app.router.lifespan_context(app):
        assert getattr(app.state, "core", None) is not None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac


def _run[T](
    body: Callable[[AsyncClient], Awaitable[T]], settings: Settings
) -> T:
    async def _main() -> T:
        async with _client(settings) as ac:
            return await body(ac)

    return asyncio.run(_main())


# ── projects ───────────────────────────────────────────────────────────


def test_project_crud(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body(ac: AsyncClient) -> None:
        r = await ac.post(
            "/api/projects",
            json={"root_path": str(proj_root), "name": "demo"},
        )
        assert r.status_code == 201, r.text
        created = r.json()
        pid = created["id"]
        assert created["name"] == "demo"
        assert created["root_path"] == str(proj_root)

        r = await ac.get("/api/projects")
        assert r.status_code == 200
        assert [p["id"] for p in r.json()] == [pid]

        r = await ac.get(f"/api/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid

        r = await ac.get("/api/projects/99999")
        assert r.status_code == 404

        r = await ac.delete(f"/api/projects/{pid}")
        assert r.status_code == 204

        r = await ac.get(f"/api/projects/{pid}")
        assert r.status_code == 404

        r = await ac.delete(f"/api/projects/{pid}")
        assert r.status_code == 404

    _run(body, s)


def test_delete_project_409_when_run_is_active(tmp_path: Path) -> None:
    """W7: DELETE /api/projects/{id} returns 409 when any run is
    currently active (`running` or `awaiting_children`). The cascade
    refuses; the caller must cancel the run first."""
    from sqlalchemy import select

    from relay.db.models import Project
    from relay.orchestrator.lifecycle import (
        create_run,
        open_iter,
        set_run_status,
    )

    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    app = create_app(s, harness=harness)

    async def body() -> None:
        async with app.router.lifespan_context(app):
            core = app.state.core
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as ac:
                pid = await core.register_project(proj_root, "demo")
                # Seed a `running` run directly through the lifecycle
                # helpers — no harness drive, no terminal status.
                await create_run(
                    core._sm,
                    run_id="live",
                    project_id=pid,
                    prompt_body="seeded",
                    max_iters=1,
                    iter_timeout=60,
                    worktree_path=None,
                    branch=None,
                )
                await open_iter(
                    core._sm,
                    run_id="live",
                    seq=1,
                    phase=None,
                    prompt="p",
                    preamble="pre",
                )
                await set_run_status(
                    core._sm, "live", "running", ended=False
                )

                r = await ac.delete(f"/api/projects/{pid}")
                assert r.status_code == 409
                assert "active run" in r.json()["detail"]

                # Project row is intact.
                async with core._sm() as sess:
                    assert (
                        await sess.scalar(
                            select(Project).where(Project.id == pid)
                        )
                    ) is not None

                # Settle the run so lifespan teardown's orphan-sweep
                # finds nothing in flight.
                await set_run_status(
                    core._sm, "live", "cancelled", ended=True
                )

    asyncio.run(body())


def test_project_register_expands_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug A: ``~`` in root_path is expanded at the registration boundary,
    so a "~/foo" registration does not lurk as the literal string
    ``<cwd>/~/foo`` and FileNotFoundError pi spawns later."""
    s = _settings(tmp_path)
    home = tmp_path / "home"
    target = home / "proj"
    target.mkdir(parents=True)
    # Re-home so Path.expanduser("~") points at our tmp tree (scoped to
    # this test via monkeypatch — auto-restored after).
    monkeypatch.setenv("HOME", str(home))

    async def body(ac: AsyncClient) -> None:
        r = await ac.post(
            "/api/projects",
            json={"root_path": "~/proj", "name": "tilded"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["root_path"] == str(target.resolve())

    _run(body, s)


def test_project_register_rejects_missing_path(tmp_path: Path) -> None:
    """Bug A: a path that does not exist is a 400 at registration, not a
    silent corruption that fails an iter much later."""
    s = _settings(tmp_path)

    async def body(ac: AsyncClient) -> None:
        r = await ac.post(
            "/api/projects",
            json={
                "root_path": str(tmp_path / "definitely-missing"),
                "name": "ghost",
            },
        )
        assert r.status_code == 400, r.text
        assert "does not exist" in r.json()["detail"]

    _run(body, s)


# ── prompts ────────────────────────────────────────────────────────────


def test_prompt_versioning(tmp_path: Path) -> None:
    s = _settings(tmp_path)

    async def body(ac: AsyncClient) -> None:
        # Global prompt (no project_id) works.
        r = await ac.post(
            "/api/prompts", json={"name": "p", "body": "v1 body"}
        )
        assert r.status_code == 201, r.text
        created = r.json()
        pid = created["id"]
        assert created["version"] == 1
        assert created["project_id"] is None

        # List shows the latest only.
        r = await ac.get("/api/prompts")
        assert r.status_code == 200
        assert len(r.json()) == 1

        # PUT bumps the version.
        r = await ac.put(f"/api/prompts/{pid}", json={"body": "v2 body"})
        assert r.status_code == 200, r.text
        v2 = r.json()
        assert v2["version"] == 2
        assert v2["body"] == "v2 body"

        # /versions shows both.
        r = await ac.get(f"/api/prompts/{pid}/versions")
        assert r.status_code == 200
        versions = r.json()["versions"]
        assert [v["version"] for v in versions] == [1, 2]

        # GET a specific (old) version id still returns v1.
        r = await ac.get(f"/api/prompts/{pid}")
        assert r.status_code == 200
        assert r.json()["version"] == 1

        # List still shows one entry (latest).
        r = await ac.get("/api/prompts")
        assert len(r.json()) == 1

        # Unknown id.
        r = await ac.get("/api/prompts/99999")
        assert r.status_code == 404
        r = await ac.get("/api/prompts/99999/versions")
        assert r.status_code == 404

        # Duplicate (project_id, name) → 409.
        r = await ac.post(
            "/api/prompts", json={"name": "p", "body": "dup"}
        )
        assert r.status_code == 409

        # DELETE removes all versions.
        r = await ac.delete(f"/api/prompts/{pid}")
        assert r.status_code == 204
        r = await ac.get("/api/prompts")
        assert r.json() == []
        r = await ac.delete(f"/api/prompts/{pid}")
        assert r.status_code == 404

    _run(body, s)


def test_prompt_unknown_project(tmp_path: Path) -> None:
    s = _settings(tmp_path)

    async def body(ac: AsyncClient) -> None:
        r = await ac.post(
            "/api/prompts",
            json={"project_id": 4242, "name": "x", "body": "b"},
        )
        assert r.status_code == 404

    _run(body, s)


# ── runs ───────────────────────────────────────────────────────────────


async def _register_project(ac: AsyncClient, root: Path) -> int:
    r = await ac.post(
        "/api/projects", json={"root_path": str(root), "name": "p"}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def test_run_create_and_detail(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body(ac: AsyncClient) -> None:
        pid = await _register_project(ac, proj_root)

        r = await ac.post(
            "/api/runs",
            json={"project_id": pid, "prompt_body": "do the work"},
        )
        assert r.status_code == 201, r.text
        run = r.json()
        rid = run["id"]
        assert run["status"]  # status present
        assert run["prompt_body"] == "do the work"
        assert run["project_id"] == pid

        # List + project_id filter.
        r = await ac.get("/api/runs", params={"project_id": pid})
        assert r.status_code == 200
        assert [x["id"] for x in r.json()] == [rid]

        # Pagination: offset past the only row → empty.
        r = await ac.get(
            "/api/runs", params={"project_id": pid, "offset": 5}
        )
        assert r.json() == []

        # Status filter that matches nothing.
        r = await ac.get(
            "/api/runs",
            params={"project_id": pid, "status": "nonsense-status"},
        )
        assert r.json() == []

        # Detail includes iters[].
        r = await ac.get(f"/api/runs/{rid}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["id"] == rid
        assert "iters" in detail
        assert isinstance(detail["iters"], list)

        # Unknown run → 404.
        r = await ac.get("/api/runs/does-not-exist")
        assert r.status_code == 404

        # Resume a non-paused run → 409.
        r = await ac.post(
            f"/api/runs/{rid}/resume", json={"answer": "go"}
        )
        assert r.status_code == 409

        # Events endpoint returns the paginated envelope.
        r = await ac.get(f"/api/runs/{rid}/events")
        assert r.status_code == 200
        env = r.json()
        assert env["limit"] == 100
        assert env["after_seq"] == 0
        assert isinstance(env["events"], list)
        assert any(e["kind"] == "run_started" for e in env["events"])

    _run(body, s)


def test_run_create_with_prompt_id(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body(ac: AsyncClient) -> None:
        pid = await _register_project(ac, proj_root)
        r = await ac.post(
            "/api/prompts",
            json={"project_id": pid, "name": "task", "body": "RESOLVED BODY"},
        )
        assert r.status_code == 201
        prompt_id = r.json()["id"]

        r = await ac.post(
            "/api/runs",
            json={"project_id": pid, "prompt_id": prompt_id},
        )
        assert r.status_code == 201, r.text
        # prompt_id resolved to its body.
        assert r.json()["prompt_body"] == "RESOLVED BODY"

        # Neither prompt source → 422 (RunCreate validator).
        r = await ac.post("/api/runs", json={"project_id": pid})
        assert r.status_code == 422
        # Both sources → 422.
        r = await ac.post(
            "/api/runs",
            json={
                "project_id": pid,
                "prompt_id": prompt_id,
                "prompt_body": "x",
            },
        )
        assert r.status_code == 422

        # Unknown prompt_id → 404.
        r = await ac.post(
            "/api/runs", json={"project_id": pid, "prompt_id": 99999}
        )
        assert r.status_code == 404

    _run(body, s)


def test_preview_has_no_side_effects(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body(ac: AsyncClient) -> None:
        pid = await _register_project(ac, proj_root)

        before = await ac.get("/api/runs", params={"project_id": pid})
        assert before.json() == []

        r = await ac.get(
            f"/api/runs/{pid}/preview",
            params={"prompt_body": "preview me", "phase": "planning"},
        )
        assert r.status_code == 200, r.text
        rendered = r.json()
        assert rendered["body"] == "preview me"
        assert "preview me" in rendered["prompt"]
        assert rendered["preamble"]
        assert "<preview>" in rendered["run_dir"]

        # No run row was created.
        after = await ac.get("/api/runs", params={"project_id": pid})
        assert after.json() == []

        # Neither prompt arg → 400.
        r = await ac.get(f"/api/runs/{pid}/preview")
        assert r.status_code == 400

        # Both prompt args → 400.
        r = await ac.get(
            f"/api/runs/{pid}/preview",
            params={"prompt_body": "a", "prompt_id": 1},
        )
        assert r.status_code == 400

        # Unknown project → 404.
        r = await ac.get(
            "/api/runs/99999/preview", params={"prompt_body": "x"}
        )
        assert r.status_code == 404

        # Non-numeric path segment → 400.
        r = await ac.get(
            "/api/runs/not-an-int/preview", params={"prompt_body": "x"}
        )
        assert r.status_code == 400

    _run(body, s)


def test_cancel_unknown_run(tmp_path: Path) -> None:
    s = _settings(tmp_path)

    async def body(ac: AsyncClient) -> None:
        r = await ac.post("/api/runs/nope/cancel")
        assert r.status_code == 404
        r = await ac.post("/api/runs/nope/resume", json={"answer": "a"})
        assert r.status_code == 404

    _run(body, s)
