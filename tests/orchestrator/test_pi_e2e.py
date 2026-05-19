"""Real-pi end-to-end for the orchestrator (Phase 2) and the REST layer
(Phase 3). SKIPPED unless ``PI_INTEGRATION=1``.

The Phase 1 harness already has a live-pi e2e
(``tests/harness/test_pi_integration.py``). This closes the gap one
layer up: that ``RelayCore.run_loop`` drives a *real* pi session to a
clean ``done`` (Phase 2), and that the same path works when driven
through the REST API with the production ``PiHarness`` — no scripted
double (Phase 3). pi is NEVER invoked unless the gate env var is set.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from relay_v2.app import create_app
from relay_v2.config import Settings
from relay_v2.core import RelayCore

pytestmark = pytest.mark.skipif(
    os.environ.get("PI_INTEGRATION") != "1",
    reason="real-pi e2e; set PI_INTEGRATION=1 to run",
)

# `done` takes NO prompt-marker body (markers before `done` are a
# contract violation → MarkerError → agent_end_no_signal). Ask pi for a
# single column-0 sentinel line and nothing else — same verbatim framing
# the harness e2e uses, which is known to survive pi v0.74.0.
_DONE_PROMPT = (
    "Output the following single line verbatim as your entire response, "
    "with no preamble, no code fence, and nothing before or after it:\n\n"
    "[[engteam:done]]"
)
_TERMINAL = {"done", "failed", "cancelled"}


def test_orchestrator_drives_real_pi_to_done(tmp_path: Path) -> None:
    """Phase 2: a real pi session, driven by the production loop, closes
    the run cleanly as ``done`` with run_started..run_ended events."""

    async def scenario() -> None:
        core = RelayCore(Settings(data_dir=tmp_path / ".relay"))
        await core.start()
        try:
            pid = await core.register_project(tmp_path, "pi-e2e")
            run_id = await core.start_run(
                pid, _DONE_PROMPT, max_iters=1, iter_timeout=120
            )
            result = await asyncio.wait_for(
                core.wait_for_run(run_id), timeout=150
            )
            assert result.status == "done", (
                f"status={result.status} reason={result.reason}"
            )
            run = await core.get_run(run_id)
            assert run is not None and run.status == "done"
            kinds = [e.kind for e in await core.list_events(run_id)]
            assert kinds[0] == "run_started"
            assert kinds[-1] == "run_ended"
        finally:
            await core.aclose()

    asyncio.run(scenario())


@asynccontextmanager
async def _rest_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """Production app — NO harness injection, so the run spawns real
    pi through the full REST → RelayCore → run_loop → PiHarness path."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac


def test_rest_start_run_real_pi_completes(tmp_path: Path) -> None:
    """Phase 3: POST /api/runs with the production PiHarness reaches
    ``done``; the events endpoint shows the run bracketed correctly."""

    async def scenario() -> None:
        settings = Settings(data_dir=tmp_path / ".relay")
        async with _rest_client(settings) as ac:
            r = await ac.post(
                "/api/projects",
                json={"root_path": str(tmp_path), "name": "pi-rest"},
            )
            assert r.status_code == 201, r.text
            pid = r.json()["id"]

            r = await ac.post(
                "/api/runs",
                json={
                    "project_id": pid,
                    "prompt_body": _DONE_PROMPT,
                    "max_iters": 1,
                    "iter_timeout": 120,
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["id"]

            async def poll() -> str:
                while True:
                    g = await ac.get(f"/api/runs/{run_id}")
                    assert g.status_code == 200, g.text
                    status = g.json()["status"]
                    if status in _TERMINAL:
                        return status
                    await asyncio.sleep(2)

            status = await asyncio.wait_for(poll(), timeout=150)
            assert status == "done", f"final status={status}"

            ev = await ac.get(f"/api/runs/{run_id}/events")
            assert ev.status_code == 200, ev.text
            events = ev.json()["events"]
            kinds = [e["kind"] for e in events]
            assert kinds[0] == "run_started"
            assert kinds[-1] == "run_ended"
            seqs = [e["seq"] for e in events]
            assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))

    asyncio.run(scenario())
