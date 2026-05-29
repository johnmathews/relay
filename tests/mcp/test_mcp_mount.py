"""W3: MCP server mounted at ``/mcp`` in the app lifespan (ADR-27).

This is the explicit test for the highest-risk line in Phase 5: the
``async with mcp.session_manager.run():`` wrap in
:func:`relay.app.create_app`'s lifespan. A sub-app mounted via
``app.mount()`` does not get its ASGI lifespan auto-run by Starlette
(the #1367 footgun); ``streamable_http_app()``'s session manager is
started in that lifespan. If the wrap is missing, every ``/mcp``
request hangs or 500s — so every assertion here runs under a short
``timeout`` and a hang fails the test rather than blocking the suite.

Drives the full ``create_app`` lifespan with a scripted harness (the
``tests/api/`` Approach-B pattern: ``app.router.lifespan_context`` +
``httpx.AsyncClient`` over ``ASGITransport``). The base URL carries a
port so the Host header matches FastMCP's localhost DNS-rebinding
allow-list (``127.0.0.1:*``) — the same security that protects the
real single-user localhost deployment (ADR-12).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from relay.app import create_app
from relay.config import Settings
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _first_sse_json(body: str) -> dict[str, Any]:
    """Extract the first ``data:`` JSON object from an SSE response."""
    for line in body.splitlines():
        if line.startswith("data: "):
            obj: dict[str, Any] = json.loads(line[6:])
            return obj
    raise AssertionError(f"no SSE data frame in response: {body[:200]!r}")


def _run(
    body: Callable[[httpx.AsyncClient], Awaitable[None]],
    settings: Settings,
) -> None:
    async def _main() -> None:
        app = create_app(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            # host:port so the Host header matches FastMCP's
            # ``127.0.0.1:*`` DNS-rebinding allow-list.
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:7800"
            ) as ac:
                await body(ac)

    asyncio.run(_main())


def test_mcp_mounted_handshake_and_tools(tmp_path: Path) -> None:
    """initialize handshake returns relay + a session id (proves the
    session manager is running, not hung), and tools/list returns all
    seven spec §8 tools — over the mounted ``/mcp`` endpoint."""

    async def body(ac: httpx.AsyncClient) -> None:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "itest", "version": "0"},
            },
        }
        r = await ac.post(
            "/mcp/",
            json=init,
            headers=_MCP_HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        assert r.status_code == 200, r.text
        sid = r.headers.get("mcp-session-id")
        assert sid, "no Mcp-Session-Id — session manager not running"
        assert (
            _first_sse_json(r.text)["result"]["serverInfo"]["name"]
            == "relay"
        )

        sess_headers = {**_MCP_HEADERS, "mcp-session-id": sid}
        await ac.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=sess_headers,
            timeout=10,
            follow_redirects=True,
        )
        r2 = await ac.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=sess_headers,
            timeout=10,
            follow_redirects=True,
        )
        assert r2.status_code == 200, r2.text
        names = sorted(
            t["name"] for t in _first_sse_json(r2.text)["result"]["tools"]
        )
        assert names == sorted(
            [
                "relay__list_runs",
                "relay__get_run",
                "relay__start_run",
                "relay__cancel_run",
                "relay__pause_response",
                "relay__tail_events",
                "relay__read_artifact",
            ]
        )

    _run(body, _settings(tmp_path))


def test_existing_routes_unaffected_by_mcp_mount(tmp_path: Path) -> None:
    """The lifespan wrap + ``/mcp`` mount must not regress existing
    routes — ``/health`` and the REST surface still respond."""

    async def body(ac: httpx.AsyncClient) -> None:
        h = await ac.get("/health", timeout=10)
        assert h.status_code == 200 and h.json() == {"status": "ok"}
        # An /api route still resolves (empty list, but 200 not 404/500).
        p = await ac.get("/api/projects", timeout=10)
        assert p.status_code == 200

    _run(body, _settings(tmp_path))
