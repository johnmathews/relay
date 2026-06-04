"""Route tests for POST /api/runs/{id}/reopen (WU5 — resilient-iter-close).

Reopens a failed+no-signal run as paused so the operator can resume it
with guidance. 404 unknown; 409 not failed; 409 last iter not no-signal.
On success: status flips to paused, ended_at cleared, pause_requested
event appended with a recovery question.

Uses the same _client_with_core context manager pattern as
tests/api/test_runs.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from relay.app import create_app
from relay.config import Settings
from relay.core import RelayCore
from relay.db.models import Event
from relay.harness.protocol import Harness
from tests.orchestrator.scripted_harness import (
    HangScript,
    ScriptedHarness,
    TextScript,
)
from tests.orchestrator.test_loop import HANDOFF_NO_MARKERS

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


@asynccontextmanager
async def _client_with_core(
    settings: Settings,
    harness: Harness | None = None,
) -> AsyncIterator[tuple[AsyncClient, RelayCore]]:
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


async def _register_project(ac: AsyncClient, root: Path) -> int:
    r = await ac.post(
        "/api/projects", json={"root_path": str(root), "name": "p"}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _start_run(ac: AsyncClient, project_id: int, prompt: str) -> str:
    r = await ac.post(
        "/api/runs",
        json={"project_id": project_id, "prompt_body": prompt},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _wait_terminal(core: RelayCore, run_id: str) -> None:
    await core.wait_for_run(run_id)


# ──────────────────────────────────────────────────────────────────────


def test_reopen_failed_no_signal_run_lands_paused(tmp_path: Path) -> None:
    """A failed run whose last iter is no-signal can be reopened as
    paused; a pause_requested event with the recovery question lands
    and ended_at is cleared."""

    async def scenario() -> None:
        settings = _settings(tmp_path)
        # HANDOFF_NO_MARKERS: real handoff sentinel but no marker pair →
        # sub-case (3) marker-contract violation → exit_reason=
        # "agent_end_no_signal" + run lands "failed".
        harness = ScriptedHarness([TextScript(HANDOFF_NO_MARKERS)])
        async with _client_with_core(settings, harness) as (ac, core):
            pid = await _register_project(ac, tmp_path)
            run_id = await _start_run(ac, pid, "Go.")
            await _wait_terminal(core, run_id)

            # Sanity: it landed failed + no-signal.
            r = await ac.get(f"/api/runs/{run_id}")
            assert r.status_code == 200
            assert r.json()["status"] == "failed"

            # Reopen.
            r = await ac.post(f"/api/runs/{run_id}/reopen")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "paused"
            assert body["ended_at"] is None

            # pause_requested event landed with recovery question.
            sm = core._sm
            async with sm() as s:
                last_pause = (
                    await s.scalars(
                        select(Event)
                        .where(
                            Event.run_id == run_id,
                            Event.kind == "pause_requested",
                        )
                        .order_by(Event.seq.desc())
                        .limit(1)
                    )
                ).one()
                assert "auto-paused" in last_pause.payload["question"].lower()

    asyncio.run(scenario())


def test_reopen_unknown_run_404(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        async with _client_with_core(settings) as (ac, _):
            r = await ac.post("/api/runs/does-not-exist/reopen")
            assert r.status_code == 404

    asyncio.run(scenario())


def test_reopen_done_run_409(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        # default harness = single DONE_BLOCK → run lands done.
        async with _client_with_core(settings) as (ac, core):
            pid = await _register_project(ac, tmp_path)
            run_id = await _start_run(ac, pid, "Go.")
            await _wait_terminal(core, run_id)
            r = await ac.get(f"/api/runs/{run_id}")
            assert r.json()["status"] == "done"

            r = await ac.post(f"/api/runs/{run_id}/reopen")
            assert r.status_code == 409
            assert "not failed" in r.text.lower()

    asyncio.run(scenario())


def test_reopen_failed_timeout_run_409(tmp_path: Path) -> None:
    """A failed run whose last iter timed out (NOT a no-signal close)
    cannot be reopened — timeout is a real bug, not a missed sentinel."""

    async def scenario() -> None:
        settings = Settings(data_dir=tmp_path / ".relay", iter_timeout=1)
        harness = ScriptedHarness([HangScript()])
        async with _client_with_core(settings, harness) as (ac, core):
            pid = await _register_project(ac, tmp_path)
            run_id = await _start_run(ac, pid, "Go.")
            await _wait_terminal(core, run_id)

            r = await ac.get(f"/api/runs/{run_id}")
            assert r.json()["status"] == "failed"

            r = await ac.post(f"/api/runs/{run_id}/reopen")
            assert r.status_code == 409
            assert "exit_reason" in r.text.lower()

    asyncio.run(scenario())


def test_reopen_then_resume_runs_next_iter(tmp_path: Path) -> None:
    """A reopened run can be resumed: the operator's answer becomes the
    next iter's body. Gates the critical fix landed in this commit —
    without it ``resume_run`` would return 409 because no paused iter
    exists for ``latest_paused_iter`` to find."""

    async def scenario() -> None:
        settings = _settings(tmp_path)
        # Drive a failed-no-signal run (one HANDOFF_NO_MARKERS), then
        # script a clean DONE_BLOCK for the post-resume iter.
        harness = ScriptedHarness(
            [TextScript(HANDOFF_NO_MARKERS), TextScript(DONE_BLOCK)]
        )
        async with _client_with_core(settings, harness) as (ac, core):
            pid = await _register_project(ac, tmp_path)
            run_id = await _start_run(ac, pid, "Go.")
            await _wait_terminal(core, run_id)

            # Reopen → paused.
            r = await ac.post(f"/api/runs/{run_id}/reopen")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "paused"

            # Resume → should run iter 2, lands done.
            r = await ac.post(
                f"/api/runs/{run_id}/resume",
                json={"answer": "please continue, here is guidance"},
            )
            assert r.status_code == 200, r.text
            await _wait_terminal(core, run_id)
            r = await ac.get(f"/api/runs/{run_id}")
            assert r.json()["status"] == "done"

    asyncio.run(scenario())
