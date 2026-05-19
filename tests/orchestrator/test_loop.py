"""Phase 2 end-to-end verification (plan.md Phase 2 criteria).

Every test drives the real :class:`RelayCore` + ``run_loop`` against the
scripted harness double — no pi, fully offline (pi e2e stays gated
behind ``PI_INTEGRATION=1``). Reads use a throwaway sync engine on the
same SQLite file, which is the orchestrator-independent way to assert
the event log is the source of truth (ADR-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Event, Iter, Run
from tests.orchestrator.scripted_harness import (
    HangScript,
    Script,
    ScriptedHarness,
    TextScript,
)

HANDOFF_ITER1 = (
    '[[engteam:phase-start phase="planning"]]\n\n'
    "Did the planning work.\n\n"
    "[[engteam:prompt-start]]\n"
    "Now implement W2.\n"
    "[[engteam:prompt-end]]\n\n"
    "[[engteam:handoff]]"
)
DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"
PAUSE_BLOCK = (
    "I need a decision.\n\n"
    "[[engteam:prompt-start]]\n"
    "Proceed with the chosen option.\n"
    "[[engteam:prompt-end]]\n\n"
    '[[engteam:pause-for-input id="P1" question="Use A or B?"]]'
)
# Indented sentinel inside a fence + NO real column-0 closing sentinel.
FENCED_NO_SIGNAL = (
    "Here is the contract, for reference:\n\n"
    "```text\n"
    "    [[engteam:handoff]]\n"
    "```\n\n"
    "That was just an example; I never emit a real one."
)
# Real closing sentinel but the marker pair is missing → MarkerError.
HANDOFF_NO_MARKERS = "I think I'm done here.\n\n[[engteam:handoff]]"
# A well-formed handoff that never terminates — drives the max_iters cap.
HANDOFF_FOREVER = (
    "Still going.\n\n"
    "[[engteam:prompt-start]]\nKeep going.\n[[engteam:prompt-end]]\n\n"
    "[[engteam:handoff]]"
)


def _run[T](coro: Callable[[RelayCore], Awaitable[T]], settings: Settings,
            harness: ScriptedHarness) -> T:
    async def _main() -> T:
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()

    return asyncio.run(_main())


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _read(settings: Settings) -> Session:
    engine = create_engine(settings.db_url)
    return Session(engine)


def test_handoff_iterates_then_done(tmp_path: Path) -> None:
    """phase-start + handoff → iter 1 closes, iter 2 starts with the
    extracted next-prompt and the carried RELAY_PHASE; done terminates."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_ITER1),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Kick off.", max_iters=5)
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)

    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "done"
        assert run.ended_at is not None
        iters = list(
            s.scalars(select(Iter).where(Iter.run_id == run_id)
                      .order_by(Iter.seq))
        )
        assert [i.seq for i in iters] == [1, 2]
        assert iters[0].signal_kind == "handoff"
        assert iters[0].exit_reason == "signal"
        # iter 2's next-prompt came from iter 1's extracted handoff body.
        assert "Now implement W2." in iters[1].prompt
        # phase-start carried forward into iter 2's preamble.
        assert "RELAY_PHASE: planning" in iters[1].preamble
        assert "RELAY_PHASE:" not in iters[0].preamble
        kinds = [
            e.kind for e in s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        ]
        assert kinds[0] == "run_started"
        assert kinds[-1] == "run_ended"
        assert "iter_started" in kinds and "iter_ended" in kinds
        assert "signal_emit" in kinds
    # The carried phase was persisted to $RELAY_RUN_DIR/phase.
    phase_file = settings.data_dir / "runs" / run_id / "phase"
    assert phase_file.read_text().strip() == "planning"


def test_done_terminates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "done"
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        assert len(iters) == 1 and iters[0].signal_kind == "done"


def test_pause_then_resume(tmp_path: Path) -> None:
    """pause terminates status=paused, persists the next-prompt; resume
    composes the saved prompt + answer and continues to completion."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(PAUSE_BLOCK),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Start.")
        first = await core.wait_for_run(run_id)
        assert first.status == "paused"
        run = await core.get_run(run_id)
        assert run is not None and run.status == "paused"
        await core.resume_run(run_id, "Use option A.")
        second = await core.wait_for_run(run_id)
        assert second.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "done"
        iters = list(
            s.scalars(select(Iter).where(Iter.run_id == run_id)
                      .order_by(Iter.seq))
        )
        assert iters[0].signal_kind == "pause"
        assert iters[0].signal_args is not None
        assert "Proceed with the chosen option." in (
            iters[0].signal_args["next_prompt"]
        )
        # Resumed iter's body = saved next-prompt + the answer block.
        assert "Proceed with the chosen option." in iters[1].prompt
        assert "Use option A." in iters[1].prompt
        kinds = [
            e.kind for e in s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        ]
        assert "pause_requested" in kinds
        assert "pause_resolved" in kinds


def test_fenced_sentinel_no_real_signal_fails_cleanly(
    tmp_path: Path,
) -> None:
    """A handoff sentinel only inside a fenced/indented block — never at
    column 0 — yields no signal: exit_reason=agent_end_no_signal, run
    fails cleanly (plan.md Phase 2)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(FENCED_NO_SIGNAL)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "agent_end_no_signal"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "failed"
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        assert iters[0].signal_kind is None
        assert iters[0].exit_reason == "agent_end_no_signal"
        last = s.scalars(
            select(Event).where(Event.run_id == run_id)
            .order_by(Event.seq.desc()).limit(1)
        ).one()
        assert last.kind == "run_ended"
        assert last.payload["status"] == "failed"


def test_marker_violation_fails_cleanly(tmp_path: Path) -> None:
    """A real handoff sentinel with no prompt-marker pair is a contract
    violation: classified agent_end_no_signal, headline preserved."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_NO_MARKERS)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "agent_end_no_signal"
        assert result.summary and "extract_handoff_prompt" in result.summary
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        it = s.scalars(select(Iter).where(Iter.run_id == run_id)).one()
        assert it.signal_args is not None
        assert "marker_error" in it.signal_args


def test_iter_timeout_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([HangScript()])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", iter_timeout=1)
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "timeout"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        it = s.scalars(select(Iter).where(Iter.run_id == run_id)).one()
        assert it.exit_reason == "timeout"


def test_cancel_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([HangScript()])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", iter_timeout=30)
        await asyncio.sleep(0.2)  # let the iter spawn + start hanging
        await core.cancel_run(run_id)
        result = await core.wait_for_run(run_id)
        assert result.status == "cancelled"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "cancelled"
        it = s.scalars(select(Iter).where(Iter.run_id == run_id)).one()
        assert it.exit_reason == "cancelled"


def test_max_iters_exhaustion_fails(tmp_path: Path) -> None:
    """Every iter hands off but never terminates → the loop stops at the
    cap (spec.md §6 ``while seq < max_iters``) and fails the run."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_FOREVER)] * 5)

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", max_iters=3)
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "max_iters"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "failed"
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        assert len(iters) == 3  # seq 1,2,3 then seq < 3 is false
        last = s.scalars(
            select(Event).where(Event.run_id == run_id)
            .order_by(Event.seq.desc()).limit(1)
        ).one()
        assert last.kind == "run_ended"
        assert last.payload["status"] == "failed"


@pytest.mark.parametrize("script", [TextScript(DONE_BLOCK)])
def test_list_and_get_run(tmp_path: Path, script: Script) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([script])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)
        runs = await core.list_runs(pid)
        assert [r.id for r in runs] == [run_id]
        one = await core.get_run(run_id)
        assert one is not None and one.id == run_id

    _run(scenario, settings, harness)
