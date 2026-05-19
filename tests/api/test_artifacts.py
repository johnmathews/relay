"""Run-artifacts browser (ADR-25) — route + run-scoping tests.

The exhaustive sandbox negative tests live in ``test_files.py`` and
exercise the *same* audited ``resolve_within_sandbox`` + ``serve_*``
helpers this router reuses, so they are not duplicated here. These
tests cover what is unique to the artifacts router: run-scoping (the
sandbox root is derived server-side from the run id), the
run-exists/dir-exists 404s, and that the sandbox is correctly wired to
the run artifacts root (one representative traversal + binary check).

Self-contained: a stub core (only ``get_run`` is touched) and a stub
settings on a bare app — no DB, no pi. ``asyncio.run`` style matches
the rest of ``tests/api``/``tests/orchestrator``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from relay_v2.api.artifacts import router


@dataclass
class _StubRun:
    id: str


class _StubCore:
    """Only ``get_run`` is exercised by the artifacts handlers."""

    def __init__(self, run_ids: set[str]) -> None:
        self._ids = run_ids

    async def get_run(self, run_id: str) -> _StubRun | None:
        return _StubRun(run_id) if run_id in self._ids else None


@dataclass
class _StubSettings:
    data_dir: Path


def _client(data_dir: Path, run_ids: set[str]) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.core = _StubCore(run_ids)
    app.state.settings = _StubSettings(data_dir=data_dir)
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_run_artifacts(data_dir: Path, run_id: str) -> Path:
    """Create <data_dir>/runs/<run_id>/ with a few artifacts."""
    run_dir = data_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "improvement-plan.md").write_text("# Plan\n\n- W1\n")
    (run_dir / "core.bin").write_bytes(b"PK\x03\x04\x00\x00binary")
    sub = run_dir / "discussions"
    sub.mkdir()
    (sub / "260519-x.md").write_text("decided")
    return run_dir


def test_listing_and_content_happy_path(tmp_path: Path) -> None:
    data_dir = tmp_path / ".relay"
    _make_run_artifacts(data_dir, "20260519-120000-abcd")

    async def go() -> None:
        async with _client(data_dir, {"20260519-120000-abcd"}) as c:
            r = await c.get("/api/runs/20260519-120000-abcd/artifacts")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["path"] == ""
            names = {e["name"]: e for e in body["entries"]}
            assert names["discussions"]["is_dir"] is True
            assert names["improvement-plan.md"]["is_dir"] is False
            # dirs sorted before files
            assert body["entries"][0]["is_dir"] is True

            r = await c.get(
                "/api/runs/20260519-120000-abcd/artifacts",
                params={"path": "discussions"},
            )
            assert r.status_code == 200
            assert r.json()["path"] == "discussions"

            r = await c.get(
                "/api/runs/20260519-120000-abcd/artifacts/"
                "improvement-plan.md"
            )
            assert r.status_code == 200, r.text
            assert r.json()["content"] == "# Plan\n\n- W1\n"

    _run(go())


def test_unknown_run_404(tmp_path: Path) -> None:
    async def go() -> None:
        async with _client(tmp_path / ".relay", set()) as c:
            r = await c.get("/api/runs/nope/artifacts")
            assert r.status_code == 404, r.text

    _run(go())


def test_run_exists_but_no_artifacts_dir_404(tmp_path: Path) -> None:
    # Run is known but its artifacts dir was never created.
    async def go() -> None:
        async with _client(tmp_path / ".relay", {"r1"}) as c:
            r = await c.get("/api/runs/r1/artifacts")
            assert r.status_code == 404, r.text

    _run(go())


def test_sandbox_wired_to_artifacts_root(tmp_path: Path) -> None:
    data_dir = tmp_path / ".relay"
    _make_run_artifacts(data_dir, "rid")
    # A secret sibling next to the run dir — traversal must not reach it.
    (data_dir / "runs" / "secret.txt").write_text("top secret")

    async def go() -> None:
        async with _client(data_dir, {"rid"}) as c:
            for bad in ("../secret.txt", "../../../etc/passwd"):
                r = await c.get(
                    "/api/runs/rid/artifacts", params={"path": bad}
                )
                assert r.status_code == 400, (bad, r.status_code)
            r = await c.get(
                "/api/runs/rid/artifacts", params={"path": "/etc/passwd"}
            )
            assert r.status_code == 400, r.text
            # binary → 415
            r = await c.get("/api/runs/rid/artifacts/core.bin")
            assert r.status_code == 415, r.text
            # symlink escape from inside the artifacts root → 400
            os.symlink(
                data_dir / "runs" / "secret.txt",
                data_dir / "runs" / "rid" / "leak",
            )
            r = await c.get("/api/runs/rid/artifacts/leak")
            assert r.status_code == 400, (r.status_code, r.text)

    _run(go())
