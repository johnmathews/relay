"""Phase 8 (spec §11.2): the conditional production SPA mount.

Two regimes:

* **No built frontend** (the normal dev/test state, and what the
  worktree/repo looks like with no ``npm run build``): the mount is a
  no-op and the app surface is byte-for-byte what it was pre-Phase-8 —
  ``/`` 404s, every API/health/openapi path is unchanged.
* **Built frontend present**: ``/`` and unknown *non-asset* paths serve
  ``index.html`` (vue-router history-mode SPA fallback); a missing
  *asset* still 404s (a broken asset ref must not be masked as the
  shell); and ``/health`` / ``/api`` / ``/openapi.json`` / ``/mcp`` are
  never shadowed because the catch-all is mounted last.

Mirrors ``tests/api/test_w2_routes.py``: real ``create_app`` + lifespan
via ``app.router.lifespan_context`` with a scripted harness so no pi is
spawned.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import relay_v2.api.static as static_mod
from relay_v2.app import create_app
from relay_v2.config import Settings
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


@asynccontextmanager
async def _client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    app = create_app(Settings(data_dir=tmp_path / ".relay"), harness=harness)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac


def _run[T](body: Callable[[AsyncClient], Awaitable[T]], tmp_path: Path) -> T:
    async def _main() -> T:
        async with _client(tmp_path) as ac:
            return await body(ac)

    return asyncio.run(_main())


def test_no_build_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no built frontend the app behaves exactly as pre-Phase-8.

    Monkeypatches :func:`frontend_dist_dir` to ``None`` so the test is
    hermetic — a dev checkout with a lingering ``frontend/dist/`` from a
    prior ``npm run build`` (gitignored) must not flip the assertion.
    """
    monkeypatch.setattr(static_mod, "frontend_dist_dir", lambda: None)
    assert static_mod.frontend_dist_dir() is None

    async def body(ac: AsyncClient) -> None:
        assert (await ac.get("/health")).json() == {"status": "ok"}
        assert (await ac.get("/openapi.json")).status_code == 200
        # No SPA shell: an unknown path is a plain 404, not index.html.
        assert (await ac.get("/projects/x")).status_code == 404

    _run(body, tmp_path)


def test_built_frontend_is_served_with_spa_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>relay</title>")
    (dist / "assets" / "app.js").write_text("console.log('relay')")
    monkeypatch.setattr(static_mod, "frontend_dist_dir", lambda: dist)

    async def body(ac: AsyncClient) -> None:
        # Root serves the shell.
        root = await ac.get("/")
        assert root.status_code == 200
        assert "relay" in root.text
        # A real asset is served as itself.
        asset = await ac.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text
        # Unknown non-asset path → SPA fallback to index.html.
        deep = await ac.get("/projects/abc/runs/1")
        assert deep.status_code == 200
        assert "<title>relay</title>" in deep.text
        # A missing *asset* still 404s (broken ref must not be masked).
        assert (await ac.get("/assets/missing.js")).status_code == 404
        # The catch-all never shadows API / health / openapi.
        assert (await ac.get("/health")).json() == {"status": "ok"}
        assert (await ac.get("/openapi.json")).status_code == 200
        assert (await ac.get("/api/projects")).status_code == 200

    _run(body, tmp_path)
