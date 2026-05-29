"""Loop-level fanout signal tests (9b). Scripted harness, no pi."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay.config import Settings
from relay.core import RelayCore
from relay.db.models import Iter, Run
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
    """Loop's terminal-signal for fanout: parent's first settle yields
    LoopResult(awaiting_children, fanout_payload=…). The closing iter
    of that first run-task carries signal_kind='fanout'. (9c then resumes
    the parent into a synthesizer iter; the loop result for that first
    settle is independent of the join.)
    """
    settings = _settings(tmp_path)
    # Scripts: parent fanout, 2 children done, parent synthesizer done.
    harness = ScriptedHarness(
        [TextScript(FANOUT_BLOCK), TextScript(DONE), TextScript(DONE),
         TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "awaiting_children"
        assert result.fanout_payload is not None
        assert len(result.fanout_payload["children"]) == 2
        # Drain children + synthesizer so aclose doesn't race the watcher.
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                children = list(
                    s.scalars(select(Run).where(Run.parent_run_id == run_id))
                )
        finally:
            engine.dispose()
        for c in children:
            await core.wait_for_run(c.id)
        # Second settle: synthesizer iter completes.
        synth_result = await core.wait_for_run(run_id)
        assert synth_result.status == "done"
        return run_id

    run_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            run = s.get(Run, run_id)
            assert run is not None
            # After 9c, parent reaches done via the synthesizer.
            assert run.status == "done"
            assert run.ended_at is not None
            # Closing fanout iter still present (now first of two iters).
            fanout_iter = s.scalar(
                select(Iter)
                .where(Iter.run_id == run_id, Iter.signal_kind == "fanout")
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            assert fanout_iter is not None
            assert fanout_iter.exit_reason == "signal"
            # Synthesizer iter exists with signal_kind=done.
            synth_iter = s.scalar(
                select(Iter)
                .where(Iter.run_id == run_id, Iter.signal_kind == "done")
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            assert synth_iter is not None
            assert synth_iter.seq > fanout_iter.seq
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
