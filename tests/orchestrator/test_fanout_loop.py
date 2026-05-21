"""Loop-level fanout signal tests (9b). Scripted harness, no pi."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_BLOCK = (
    "Dispatching.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "a", "prompt": "A."}, {"role": "b", "prompt": "B."}],'
    ' "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
DONE = "Done.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _run_sync(coro, settings, harness):
    async def _main():
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()
    return asyncio.run(_main())


def test_fanout_signal_transitions_parent_to_awaiting_children(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    # parent + 2 children
    harness = ScriptedHarness(
        [TextScript(FANOUT_BLOCK), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "awaiting_children"
        assert result.fanout_payload is not None
        assert len(result.fanout_payload["children"]) == 2
        return run_id

    run_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            run = s.get(Run, run_id)
            assert run is not None
            assert run.status == "awaiting_children"
            assert run.ended_at is None
            closing_iter = s.scalar(
                select(Iter)
                .where(Iter.run_id == run_id)
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            assert closing_iter is not None
            assert closing_iter.signal_kind == "fanout"
            assert closing_iter.exit_reason == "signal"
    finally:
        engine.dispose()


def test_fanout_bad_json_fails_run(tmp_path: Path) -> None:
    """Malformed fanout JSON propagates as FanoutParseError → run fails."""
    bad_fanout = (
        "[[engteam:fanout-start]]\n"
        "{not valid json}\n"
        "[[engteam:fanout-end]]\n\n"
        "[[engteam:fanout]]"
    )
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(bad_fanout)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"

    _run_sync(scenario, settings, harness)
