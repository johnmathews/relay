"""W3: sandboxed read-only file browser — route + sandbox tests.

Self-contained: a tiny stub core (only ``get_project`` is exercised by
the handlers) is mounted on a bare FastAPI app, so these tests do not
depend on W2/W5 app wiring or spawn anything. ``asyncio.run`` test style
matches ``tests/orchestrator/`` (pytest-asyncio is not globally on).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from relay_v2.api.files import (
    SandboxViolation,
    resolve_within_sandbox,
    router,
)

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class _StubProject:
    root_path: str


class _StubCore:
    """Only ``get_project`` is touched by the file-browser handlers."""

    def __init__(self, projects: dict[int, str]) -> None:
        self._projects = projects

    async def get_project(self, project_id: int) -> _StubProject | None:
        root = self._projects.get(project_id)
        return None if root is None else _StubProject(root_path=root)


def _client(sandbox_root: Path) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.core = _StubCore({1: str(sandbox_root)})
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "sample.md").write_text("# Hello\n")
    (root / "sample.bin").write_bytes(b"\x89PNG\x00\x00binary")
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested-body")
    return root


# ── the audited sandbox function (unit-level negative tests) ───────────


def test_sandbox_rejects_absolute(tmp_path: Path) -> None:
    root = tmp_path
    for bad in ("/etc/passwd", "/", "//etc/passwd"):
        try:
            resolve_within_sandbox(root, bad)
            raise AssertionError(f"expected SandboxViolation for {bad!r}")
        except SandboxViolation:
            pass


def test_sandbox_rejects_dotdot(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    for bad in ("..", "../", "../../etc/passwd", "a/../../b", "sub/../.."):
        try:
            resolve_within_sandbox(root, bad)
            raise AssertionError(f"expected SandboxViolation for {bad!r}")
        except SandboxViolation:
            pass


def test_sandbox_rejects_nul(tmp_path: Path) -> None:
    try:
        resolve_within_sandbox(tmp_path, "a\x00b")
        raise AssertionError("expected SandboxViolation for NUL")
    except SandboxViolation:
        pass


def test_sandbox_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")
    link = root / "escape"
    os.symlink(outside, link)
    try:
        resolve_within_sandbox(root, "escape")
        raise AssertionError("expected SandboxViolation for symlink")
    except SandboxViolation:
        pass


def test_sandbox_allows_legitimate(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)
    assert resolve_within_sandbox(root, "") == root.resolve()
    assert resolve_within_sandbox(root, "sample.md") == (
        root / "sample.md"
    ).resolve()
    assert resolve_within_sandbox(root, "sub/nested.txt") == (
        root / "sub" / "nested.txt"
    ).resolve()


def test_sandbox_missing_root_is_filenotfound(tmp_path: Path) -> None:
    try:
        resolve_within_sandbox(tmp_path / "nope", "x")
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass
    except SandboxViolation as exc:
        raise AssertionError("missing root must be 404 not 400") from exc


# ── route-level negative tests (HTTP status mapping) ───────────────────


def test_route_traversal_query_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            for q in ("../", "../../etc/passwd"):
                r = await c.get("/api/projects/1/files", params={"path": q})
                assert r.status_code == 400, (q, r.status_code, r.text)

    _run(scenario())


def test_route_url_encoded_traversal_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            # Raw percent-encoded traversal in the path segment; httpx
            # sends it, Starlette decodes it before the handler. Final
            # observable behavior must be 400 (not a successful escape).
            for raw in (
                "/api/projects/1/files/%2e%2e%2fpasswd",
                "/api/projects/1/files/..%2Fpasswd",
                "/api/projects/1/files/%2e%2e/%2e%2e/etc/passwd",
            ):
                r = await c.get(raw)
                assert r.status_code in (400, 404), (raw, r.status_code)
                # crucially never 200 (would mean traversal succeeded)
                assert r.status_code != 200, raw
            # encoded traversal via the listing query param → 400
            r = await c.get(
                "/api/projects/1/files", params={"path": "../../etc"}
            )
            assert r.status_code == 400

    _run(scenario())


def test_route_absolute_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get(
                "/api/projects/1/files", params={"path": "/etc/passwd"}
            )
            assert r.status_code == 400, r.text

    _run(scenario())


def test_route_symlink_escape_content_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)
    outside = tmp_path / "outside_secret"
    outside.write_text("/etc/passwd-like secret")
    os.symlink(outside, root / "evil")

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get("/api/projects/1/files/evil")
            assert r.status_code == 400, (r.status_code, r.text)

    _run(scenario())


def test_route_binary_415(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get("/api/projects/1/files/sample.bin")
            assert r.status_code == 415, (r.status_code, r.text)

    _run(scenario())


def test_route_markdown_200_verbatim(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get("/api/projects/1/files/sample.md")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["content"] == "# Hello\n"
            assert body["path"] == "sample.md"
            assert body["size"] == len("# Hello\n")
            assert body["modified"]

    _run(scenario())


def test_committed_binary_fixture_415(tmp_path: Path) -> None:
    """The committed tests/api/fixtures/sample.bin (has a NUL) → 415."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "sample.bin").write_bytes(
        (FIXTURES / "sample.bin").read_bytes()
    )
    (root / "sample.md").write_bytes(
        (FIXTURES / "sample.md").read_bytes()
    )

    async def scenario() -> None:
        async with _client(root) as c:
            assert (
                await c.get("/api/projects/1/files/sample.bin")
            ).status_code == 415
            r = await c.get("/api/projects/1/files/sample.md")
            assert r.status_code == 200
            assert r.json()["content"] == "# Hello\n"

    _run(scenario())


def test_route_listing_default_and_nested(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get("/api/projects/1/files")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["path"] == ""
            names = [e["name"] for e in body["entries"]]
            # dirs first (sub), then files asc (sample.bin, sample.md)
            assert names == ["sub", "sample.bin", "sample.md"]
            sub = next(e for e in body["entries"] if e["name"] == "sub")
            assert sub["is_dir"] is True
            md = next(
                e for e in body["entries"] if e["name"] == "sample.md"
            )
            assert md["is_dir"] is False
            assert md["size"] == len("# Hello\n")

            r2 = await c.get(
                "/api/projects/1/files", params={"path": "sub"}
            )
            assert r2.status_code == 200
            b2 = r2.json()
            assert b2["path"] == "sub"
            assert [e["name"] for e in b2["entries"]] == ["nested.txt"]
            assert b2["entries"][0]["size"] == len("nested-body")

    _run(scenario())


def test_route_unknown_project_404(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            assert (
                await c.get("/api/projects/999/files")
            ).status_code == 404
            assert (
                await c.get("/api/projects/999/files/sample.md")
            ).status_code == 404

    _run(scenario())


def test_route_nonexistent_path_404(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            assert (
                await c.get("/api/projects/1/files/missing.txt")
            ).status_code == 404
            r = await c.get(
                "/api/projects/1/files", params={"path": "no_such_dir"}
            )
            assert r.status_code == 404

    _run(scenario())


def test_route_dir_on_content_endpoint_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get("/api/projects/1/files/sub")
            assert r.status_code == 400, (r.status_code, r.text)

    _run(scenario())


def test_route_file_on_listing_endpoint_400(tmp_path: Path) -> None:
    root = _make_sandbox(tmp_path)

    async def scenario() -> None:
        async with _client(root) as c:
            r = await c.get(
                "/api/projects/1/files", params={"path": "sample.md"}
            )
            assert r.status_code == 400, (r.status_code, r.text)

    _run(scenario())
