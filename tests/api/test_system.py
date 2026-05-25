"""`/api/system/browse` — the directory picker's read-only listing.

The handler is intentionally NOT sandboxed (the use case is letting the
user pick any project root). The tests cover the happy path, parent
walking, the `~` default, and the 404/403 error paths. No core/DB
needed — mount the router on a bare FastAPI app.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from relay_v2.api.system import router


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
    )


def _client_with_core(settings: Any) -> httpx.AsyncClient:
    """Like :func:`_client` but attaches a stub ``core`` with a
    ``.settings`` attribute so the ``/defaults`` route can read it.
    """
    app = FastAPI()
    app.include_router(router)

    class _StubCore:
        def __init__(self) -> None:
            self.settings = settings

    app.state.core = _StubCore()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_browse_lists_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "a-file.txt").write_text("ignored")

    async def go() -> None:
        async with _client() as c:
            r = await c.get(f"/api/system/browse?path={tmp_path}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path"] == str(tmp_path.resolve())
        names = [e["name"] for e in body["entries"]]
        assert names == ["alpha", "beta"]
        for entry in body["entries"]:
            assert entry["path"] == str(tmp_path / entry["name"])

    _run(go())


def test_browse_default_path_is_home() -> None:
    async def go() -> None:
        async with _client() as c:
            r = await c.get("/api/system/browse")
        assert r.status_code == 200, r.text
        body = r.json()
        # `~` expansion gives the user's home directory.
        assert body["path"] == str(Path.home().resolve())

    _run(go())


def test_browse_parent_is_null_at_root() -> None:
    async def go() -> None:
        async with _client() as c:
            r = await c.get("/api/system/browse?path=/")
        assert r.status_code == 200, r.text
        assert r.json()["parent"] is None

    _run(go())


def test_browse_parent_is_set_below_root(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()

    async def go() -> None:
        async with _client() as c:
            r = await c.get(f"/api/system/browse?path={child}")
        assert r.status_code == 200, r.text
        assert r.json()["parent"] == str(tmp_path.resolve())

    _run(go())


def test_browse_missing_path_is_404(tmp_path: Path) -> None:
    missing = tmp_path / "definitely-not-here"

    async def go() -> None:
        async with _client() as c:
            r = await c.get(f"/api/system/browse?path={missing}")
        assert r.status_code == 404, r.text

    _run(go())


def test_browse_file_path_is_404(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x")

    async def go() -> None:
        async with _client() as c:
            r = await c.get(f"/api/system/browse?path={f}")
        assert r.status_code == 404, r.text

    _run(go())


def test_defaults_returns_settings_values() -> None:
    """The wizard's defaults endpoint surfaces ``Settings.max_iters`` /
    ``iter_timeout`` so the form can prefill concrete numbers."""

    class _StubSettings:
        max_iters = 7
        iter_timeout = 123

    async def go() -> None:
        async with _client_with_core(_StubSettings()) as c:
            r = await c.get("/api/system/defaults")
        assert r.status_code == 200, r.text
        assert r.json() == {"max_iters": 7, "iter_timeout": 123}

    _run(go())


@pytest.mark.skipif(
    os.geteuid() == 0,  # type: ignore[attr-defined]
    reason="root bypasses permission bits",
)
def test_browse_permission_denied_is_403(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o000)
    try:

        async def go() -> None:
            async with _client() as c:
                r = await c.get(f"/api/system/browse?path={locked}")
            assert r.status_code == 403, r.text

        _run(go())
    finally:
        locked.chmod(0o700)
