"""Phase 2 end-to-end verification (plan.md Phase 2 criteria).

Every test drives the real :class:`RelayCore` + ``run_loop`` against the
scripted harness double — no pi, fully offline (pi e2e stays gated
behind ``PI_INTEGRATION=1``). Reads use a throwaway sync engine on the
same SQLite file, which is the orchestrator-independent way to assert
the event log is the source of truth (ADR-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db, make_async_engine, make_async_sessionmaker
from relay_v2.db.models import Event, Iter, Run
from relay_v2.events import EventStore
from relay_v2.observability import OtelInstrumentation
from relay_v2.orchestrator.lifecycle import RunContext, create_run, register_project
from relay_v2.orchestrator.loop import LoopResult, SessionHandle, run_loop
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


@contextmanager
def _read(settings: Settings) -> Iterator[Session]:
    """Throwaway sync engine for read-back assertions. Context-managed so
    the engine is disposed (not just the Session closed) — otherwise the
    pooled sqlite connection leaks as a ResourceWarning."""
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            yield s
    finally:
        engine.dispose()


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


def test_resume_at_max_iters_boundary(tmp_path: Path) -> None:
    """A run that pauses on its last budgeted iter (paused.seq ==
    max_iters) must still make forward progress when resumed: the
    effective cap is max(max_iters, paused_seq+1), so the answer iter
    runs and the run completes (ADR-22). Regression for the boundary
    bug where ``while seq < max_iters`` was immediately false on
    resume and the run ended failed/max_iters."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(PAUSE_BLOCK),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Start.", max_iters=1)
        first = await core.wait_for_run(run_id)
        assert first.status == "paused"
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
        # Paused at seq 1 (== max_iters); resume runs a seq-2 answer iter.
        assert [it.seq for it in iters] == [1, 2]
        assert iters[0].signal_kind == "pause"
        assert iters[1].signal_kind == "done"


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
        # Deterministic: wait for the exact moment the iter is hung,
        # not a wall-clock sleep (no scheduling race).
        await asyncio.wait_for(harness.blocked.wait(), timeout=5)
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


# ── W6: RelayCore error-guards + concurrency (Phase 3 leans on these) ──


def test_start_run_unknown_project_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        with pytest.raises(ValueError, match="unknown project_id"):
            await core.start_run(999, "Go.")

    _run(scenario, settings, harness)


def test_cancel_run_unknown_run_id_is_noop(tmp_path: Path) -> None:
    """cancel_run on an unknown id returns silently (Phase 3 DELETE
    must not 500 on a missing/typo'd run)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        await core.cancel_run("does-not-exist")  # no exception

    _run(scenario, settings, harness)


def test_resume_run_not_paused_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        with pytest.raises(ValueError, match="is not paused"):
            await core.resume_run(run_id, "answer")

    _run(scenario, settings, harness)


def test_resume_run_duplicate_guard(tmp_path: Path) -> None:
    """A second resume of an already-resumed run is rejected — the
    guard that prevents two loops racing to UNIQUE(run_id, seq)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(PAUSE_BLOCK),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "paused"
        await core.resume_run(run_id, "A")  # flips status -> running
        with pytest.raises(ValueError, match=run_id):
            await core.resume_run(run_id, "A again")
        assert (await core.wait_for_run(run_id)).status == "done"

    _run(scenario, settings, harness)


def test_wait_for_run_unknown_id_raises_key_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        with pytest.raises(KeyError):
            await core.wait_for_run("nope")

    _run(scenario, settings, harness)


def test_two_concurrent_runs_keep_isolated_seqs(tmp_path: Path) -> None:
    """Two runs in flight at once: both complete and each run's event
    seq is an independent, gap-free 1..N (EventStore lock invariant)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> tuple[str, str]:
        pid = await core.register_project(tmp_path, "p")
        r1 = await core.start_run(pid, "Go 1.")
        r2 = await core.start_run(pid, "Go 2.")
        assert (await core.wait_for_run(r1)).status == "done"
        assert (await core.wait_for_run(r2)).status == "done"
        return r1, r2

    r1, r2 = _run(scenario, settings, harness)
    with _read(settings) as s:
        for rid in (r1, r2):
            seqs = [
                e.seq for e in s.scalars(
                    select(Event).where(Event.run_id == rid)
                    .order_by(Event.seq)
                )
            ]
            assert seqs == list(range(1, len(seqs) + 1))
            assert len(seqs) >= 3  # run_started .. run_ended at minimum


def test_phase_start_event_emitted_on_handoff_turn(tmp_path: Path) -> None:
    """W8: a turn that carries phase-start *and* a terminal signal
    (HANDOFF_ITER1) must still record a signal_emit{kind:phase_start} —
    otherwise the Phase 4 timeline/replay misses the phase transition."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_ITER1),
                               TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        events = list(
            s.scalars(select(Event).where(Event.run_id == run_id)
                      .order_by(Event.seq))
        )
        phase_emits = [
            e for e in events
            if e.kind == "signal_emit"
            and e.payload.get("kind") == "phase_start"
        ]
        assert len(phase_emits) == 1
        assert phase_emits[0].payload["args"]["phase"] == "planning"
        # Exactly one, not duplicated by the carry-forward path.
        handoff_emits = [
            e for e in events
            if e.kind == "signal_emit"
            and e.payload.get("kind") == "handoff"
        ]
        assert len(handoff_emits) == 1


def test_resume_missing_project_raises(tmp_path: Path) -> None:
    """W8: resuming a run whose project row is gone must raise, not
    silently fall back to running pi in the process CWD."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(PAUSE_BLOCK)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "paused"
        # Delete the project row out from under the paused run.
        with _read(settings) as s:
            s.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": pid}
            )
            s.commit()
        with pytest.raises(ValueError, match="project"):
            await core.resume_run(run_id, "answer")

    _run(scenario, settings, harness)


def test_start_finalises_orphaned_running_rows(tmp_path: Path) -> None:
    """ADR-31 follow-up: a 'running' row whose owning process is gone
    (server restart, crash) cannot be resumed and must not be left
    stuck. ``RelayCore.start()`` sweeps any pre-existing 'running' row
    not in ``self._runs`` and finalises it as 'cancelled' with summary
    'orphaned: server restart' + a closing run_ended event.

    Reproduces the user-reported scenario: a run got stuck before
    ADR-31 shipped; the user restarted the server; cancel from the UI
    did nothing because ``self._runs[run_id]`` no longer existed.
    """
    settings = _settings(tmp_path)

    async def scenario() -> str:
        # First "process": create a stuck 'running' row directly via
        # the DB, simulating a previous process that crashed mid-run.
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        # Insert the row directly (the orchestrator never knows about it).
        with _read(settings) as s:
            s.execute(
                text(
                    "INSERT INTO runs (id, project_id, prompt_body, "
                    "user_id, status, max_iters, iter_timeout) VALUES "
                    "(:id, :pid, :body, 1, 'running', 5, 600)"
                ),
                {"id": "orphan-1", "pid": pid, "body": "stuck"},
            )
            s.commit()
        await core1.aclose()

        # Second "process": fresh RelayCore opens against the same DB.
        # The startup sweep should finalise the orphaned run.
        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core2.start()
        await core2.aclose()
        return "orphan-1"

    run_id = asyncio.run(scenario())

    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.ended_at is not None
        events = list(
            s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        )
        assert [e.kind for e in events] == ["run_ended"]
        assert events[0].payload["status"] == "cancelled"
        assert "orphaned" in events[0].payload["summary"]


def test_cancel_orphaned_run_finalises_db(tmp_path: Path) -> None:
    """Safety net for ADR-31 startup sweep: if the user clicks Cancel
    on a run whose in-memory state has been lost (e.g., between the
    sweep and the click — shouldn't happen in practice, but the
    button must do *something*), cancel_run finalises the DB row.
    """
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core.start()
        pid = await core.register_project(tmp_path, "p")
        with _read(settings) as s:
            s.execute(
                text(
                    "INSERT INTO runs (id, project_id, prompt_body, "
                    "user_id, status, max_iters, iter_timeout) VALUES "
                    "(:id, :pid, :body, 1, 'running', 5, 600)"
                ),
                {"id": "orphan-2", "pid": pid, "body": "stuck"},
            )
            s.commit()
        # Bypass the startup sweep by deleting in-memory state if any.
        core._runs.pop("orphan-2", None)  # noqa: SLF001
        await core.cancel_run("orphan-2")
        await core.aclose()
        return "orphan-2"

    run_id = asyncio.run(scenario())

    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.ended_at is not None
        kinds = [
            e.kind
            for e in s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        ]
        assert "run_ended" in kinds


def test_internal_error_finalises_run_as_failed(tmp_path: Path) -> None:
    """ADR-31: an exception out of harness.spawn (or anywhere inside the
    loop) must not leave the run as ``running`` with no closing event.
    The run is finalised ``failed`` with a ``run_ended`` carrying an
    ``internal_error:`` summary, and ``wait_for_run`` returns rather
    than hanging.

    Reproducer mirrors the field bug: a project root that does not
    exist on disk causes the harness subprocess spawn to raise
    ``FileNotFoundError``. The double below raises the same exception
    so the test stays offline."""
    settings = _settings(tmp_path)

    class RaisingHarness:
        name = "raising"

        async def spawn(
            self,
            prompt: str,
            cwd: Path,
            env: dict[str, str],
            signal_config: object,
            resume_from: str | None = None,
        ) -> object:
            raise FileNotFoundError(
                "[Errno 2] No such file or directory: '/bogus'"
            )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await asyncio.wait_for(
            core.wait_for_run(run_id), timeout=5
        )
        assert result.status == "failed"
        assert result.reason == "internal_error"
        return run_id

    run_id = _run(scenario, settings, RaisingHarness())  # type: ignore[arg-type]

    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.ended_at is not None
        events = list(
            s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        )
        kinds = [e.kind for e in events]
        assert kinds[0] == "run_started"
        assert kinds[-1] == "run_ended"
        assert events[-1].payload["status"] == "failed"
        assert "internal_error" in events[-1].payload["summary"]


def test_aclose_cancels_in_flight_run(tmp_path: Path) -> None:
    """aclose() while a run is hung returns without deadlock and the
    run is finalised (not left 'running')."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([HangScript()])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", iter_timeout=30)
        await asyncio.wait_for(harness.blocked.wait(), timeout=5)
        return run_id  # _run's finally calls aclose() -> cancels it

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "cancelled"


# ── ADR-34 / 9a — awaiting_children orphan recovery + cascade ─────────


def _seed_run(
    settings: Settings,
    *,
    run_id: str,
    project_id: int,
    status: str,
    parent_run_id: str | None = None,
    body: str = "stuck",
) -> None:
    """Insert a ``runs`` row directly via SQL — the same pattern the
    pre-existing orphan tests use, lifted into a helper because the
    cascade tests need to seed parent + several children per run."""
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            s.execute(
                text(
                    "INSERT INTO runs (id, project_id, prompt_body, "
                    "user_id, status, max_iters, iter_timeout, "
                    "parent_run_id) VALUES "
                    "(:id, :pid, :body, 1, :status, 5, 600, :parent)"
                ),
                {
                    "id": run_id,
                    "pid": project_id,
                    "body": body,
                    "status": status,
                    "parent": parent_run_id,
                },
            )
            s.commit()
    finally:
        engine.dispose()


def test_recover_orphans_sweeps_awaiting_children(tmp_path: Path) -> None:
    """ADR-34 / 9a: an `awaiting_children` row from a prior process is
    finalised the same way a `running` orphan is — `cancelled` + a
    `run_ended` summary `"orphaned: server restart"`."""
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        await core1.aclose()
        _seed_run(
            settings,
            run_id="parent-aw",
            project_id=pid,
            status="awaiting_children",
        )

        # Fresh process: the sweep must finalise the awaiting parent.
        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core2.start()
        await core2.aclose()
        return "parent-aw"

    run_id = asyncio.run(scenario())
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.ended_at is not None
        events = list(
            s.scalars(
                select(Event).where(Event.run_id == run_id)
                .order_by(Event.seq)
            )
        )
        assert [e.kind for e in events] == ["run_ended"]
        assert events[0].payload["status"] == "cancelled"
        assert events[0].payload["summary"] == "orphaned: server restart"


def test_recover_orphans_cascades_to_children(tmp_path: Path) -> None:
    """ADR-34 / 9a: a parent in `awaiting_children` is cancelled AND
    each running child gets its own `run_ended` carrying the cascade
    summary `"orphaned: parent interrupted during fanout"`."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, str, str]:
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        await core1.aclose()
        _seed_run(
            settings,
            run_id="parent-aw",
            project_id=pid,
            status="awaiting_children",
        )
        _seed_run(
            settings,
            run_id="child-a",
            project_id=pid,
            status="running",
            parent_run_id="parent-aw",
        )
        _seed_run(
            settings,
            run_id="child-b",
            project_id=pid,
            status="running",
            parent_run_id="parent-aw",
        )

        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core2.start()
        await core2.aclose()
        return ("parent-aw", "child-a", "child-b")

    parent_id, a_id, b_id = asyncio.run(scenario())
    with _read(settings) as s:
        for rid in (parent_id, a_id, b_id):
            row = s.get(Run, rid)
            assert row is not None
            assert row.status == "cancelled"
            assert row.ended_at is not None
        # Children carry the cascade summary; parent carries the
        # startup-sweep summary (distinct strings so an operator can
        # tell the boundary apart in the timeline).
        for rid in (a_id, b_id):
            ev = list(
                s.scalars(
                    select(Event).where(Event.run_id == rid)
                    .order_by(Event.seq)
                )
            )
            assert [e.kind for e in ev] == ["run_ended"]
            assert ev[0].payload["status"] == "cancelled"
            assert (
                ev[0].payload["summary"]
                == "orphaned: parent interrupted during fanout"
            )
        pev = list(
            s.scalars(
                select(Event).where(Event.run_id == parent_id)
                .order_by(Event.seq)
            )
        )
        assert pev[-1].payload["summary"] == "orphaned: server restart"


def test_recover_orphans_cascades_recursively(tmp_path: Path) -> None:
    """ADR-34 / 9a: the cascade is depth-first. A parent →
    awaiting-child → running-grandchild chain finalises the
    grandchild before the child, and the child before the parent
    (event-store ordering preserves the depth-first invariant)."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, str, str]:
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        await core1.aclose()
        _seed_run(
            settings,
            run_id="gp",
            project_id=pid,
            status="awaiting_children",
        )
        _seed_run(
            settings,
            run_id="p",
            project_id=pid,
            status="awaiting_children",
            parent_run_id="gp",
        )
        _seed_run(
            settings,
            run_id="c",
            project_id=pid,
            status="running",
            parent_run_id="p",
        )

        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core2.start()
        await core2.aclose()
        return ("gp", "p", "c")

    gp, p, c = asyncio.run(scenario())
    with _read(settings) as s:
        # All three finalised cancelled.
        for rid in (gp, p, c):
            row = s.get(Run, rid)
            assert row is not None and row.status == "cancelled"
        # Depth-first: the grandchild's run_ended must have been
        # appended before the child's, and the child's before the
        # grandparent's. The events.id column is a monotonic
        # AUTOINCREMENT across the whole table — comparing the
        # closing-event ids captures emission order globally.
        def _ended_id(rid: str) -> int:
            ev = s.scalar(
                select(Event.id).where(
                    Event.run_id == rid, Event.kind == "run_ended"
                )
            )
            assert ev is not None
            return int(ev)

        assert _ended_id(c) < _ended_id(p) < _ended_id(gp)


def test_cascade_skips_already_terminal_children(tmp_path: Path) -> None:
    """ADR-34 / 9a: a child already in a terminal status (done /
    failed / cancelled) is left alone — no second ``run_ended`` is
    appended. Only the live siblings + the parent are finalised."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, str, str]:
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        await core1.aclose()
        _seed_run(
            settings,
            run_id="parent-aw",
            project_id=pid,
            status="awaiting_children",
        )
        _seed_run(
            settings,
            run_id="child-done",
            project_id=pid,
            status="done",
            parent_run_id="parent-aw",
        )
        _seed_run(
            settings,
            run_id="child-live",
            project_id=pid,
            status="running",
            parent_run_id="parent-aw",
        )
        # Pretend the done child already wrote its own run_ended.
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                s.execute(
                    text(
                        "INSERT INTO events "
                        "(run_id, seq, kind, payload) VALUES "
                        "('child-done', 1, 'run_ended', "
                        "'{\"status\": \"done\", "
                        "\"summary\": \"all good\"}')"
                    )
                )
                s.commit()
        finally:
            engine.dispose()

        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core2.start()
        await core2.aclose()
        return ("parent-aw", "child-done", "child-live")

    parent_id, done_id, live_id = asyncio.run(scenario())
    with _read(settings) as s:
        # Done child: status preserved, no second run_ended appended.
        done = s.get(Run, done_id)
        assert done is not None and done.status == "done"
        done_events = list(
            s.scalars(
                select(Event).where(Event.run_id == done_id)
                .order_by(Event.seq)
            )
        )
        assert [e.kind for e in done_events] == ["run_ended"]
        assert done_events[0].payload["status"] == "done"
        # Live child: cancelled with the cascade summary.
        live = s.get(Run, live_id)
        assert live is not None and live.status == "cancelled"
        live_events = list(
            s.scalars(
                select(Event).where(Event.run_id == live_id)
                .order_by(Event.seq)
            )
        )
        assert [e.kind for e in live_events] == ["run_ended"]
        assert (
            live_events[0].payload["summary"]
            == "orphaned: parent interrupted during fanout"
        )
        # Parent finalised.
        parent = s.get(Run, parent_id)
        assert parent is not None and parent.status == "cancelled"


def test_cascade_handles_cycle_safely(tmp_path: Path) -> None:
    """ADR-34 / 9a: a self-referential or cyclic parent_run_id (only
    reachable via malformed DB seeding) must not loop forever. The
    early-return on terminal status terminates the recursion as soon
    as the first node is finalised."""
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core1 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core1.start()
        pid = await core1.register_project(tmp_path, "p")
        await core1.aclose()
        # Self-cycle: an awaiting_children row that names itself as
        # its own parent. The sweep visits it once (via the status
        # query), recurses, finds the same row, finds it (eventually)
        # terminal after the first cancel append, and stops.
        _seed_run(
            settings,
            run_id="loop",
            project_id=pid,
            status="awaiting_children",
            parent_run_id="loop",
        )

        core2 = RelayCore(
            settings, harness=ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        # If the recursion did not terminate this hangs the event loop
        # — wait_for is the load-bearing assertion.
        await asyncio.wait_for(core2.start(), timeout=5)
        await core2.aclose()
        return "loop"

    run_id = asyncio.run(scenario())
    with _read(settings) as s:
        row = s.get(Run, run_id)
        assert row is not None and row.status == "cancelled"


# ── ADR-38 / 9f Task 3 — fanout_parent_ctx on LoopResult ──────────────────

FANOUT_BLOCK = (
    "Dispatching.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "a", "prompt": "A."}, {"role": "b", "prompt": "B."}],'
    ' "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
DONE_BLOCK_9F = "All done.\n\n[[engteam:done]]"



@pytest.mark.parametrize(
    "block,expected_status",
    [
        # done → result.status == "done"
        (DONE_BLOCK_9F, "done"),
        # pause → result.status == "paused"
        (
            "I need a decision.\n\n"
            "[[engteam:prompt-start]]\n"
            "Proceed with the chosen option.\n"
            "[[engteam:prompt-end]]\n\n"
            '[[engteam:pause-for-input id="P2" question="Use A or B?"]]',
            "paused",
        ),
        # failed (no signal) → result.status == "failed"
        (
            "Here is the contract:\n\n"
            "```text\n"
            "    [[engteam:handoff]]\n"
            "```\n\n"
            "That was just an example.",
            "failed",
        ),
    ],
)
def test_loop_result_fanout_parent_ctx_default_none(
    tmp_path: Path, block: str, expected_status: str
) -> None:
    """ADR-38 / 9f: every non-fanout LoopResult terminal path returns
    a result with fanout_parent_ctx is None. Parameterised over done,
    paused, and failed (no-signal)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(block)])

    async def scenario(core: RelayCore) -> LoopResult:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == expected_status
        return result

    result = _run(scenario, settings, harness)
    assert result.fanout_parent_ctx is None


def test_loop_result_fanout_parent_ctx_default_none_cancelled(
    tmp_path: Path,
) -> None:
    """ADR-38 / 9f: cancelled path (external cancel_event) also returns
    fanout_parent_ctx is None."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([HangScript()])

    async def scenario(core: RelayCore) -> LoopResult:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", iter_timeout=30)
        await asyncio.wait_for(harness.blocked.wait(), timeout=5)
        await core.cancel_run(run_id)
        result = await core.wait_for_run(run_id)
        assert result.status == "cancelled"
        return result

    result = _run(scenario, settings, harness)
    assert result.fanout_parent_ctx is None


def test_loop_captures_iter_context_on_fanout_terminal(
    tmp_path: Path,
) -> None:
    """ADR-38 / 9f Task 3: run_loop captures iter_span.context on the fanout
    branch and stores it as LoopResult.fanout_parent_ctx.

    Drives run_loop directly (no RelayCore / supervisor / join watcher).
    Two assertions:
    1. result.fanout_parent_ctx is non-None after run_loop returns with
       status == "awaiting_children".
    2. A probe span opened with context=result.fanout_parent_ctx parents
       under the dispatching iter span — the cross-run parenting invariant
       (ADR-38).
    """
    async def scenario() -> LoopResult:
        settings = _settings(tmp_path)
        init_db(settings).dispose()
        engine = make_async_engine(settings.async_db_url)
        try:
            sm = make_async_sessionmaker(engine)
            store = EventStore(sm)
            run_id = "test-fanout-ctx-01"
            pid = await register_project(sm, tmp_path, "p")
            run_dir = settings.data_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            await create_run(
                sm, run_id=run_id, project_id=pid, prompt_body="Go.",
                max_iters=4, iter_timeout=60,
                worktree_path=None, branch=None,
            )
            ctx = RunContext(
                run_id=run_id,
                project_root=tmp_path,
                worktree_path=None,
                run_dir=run_dir,
                max_iters=4,
                iter_timeout=60,
                start_seq=0,
                phase=None,
                body="Go.",
            )
            harness = ScriptedHarness([TextScript(FANOUT_BLOCK)])
            exporter = InMemorySpanExporter()
            otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

            with otel.run_span(run_id) as run_span:
                result = await run_loop(
                    ctx,
                    harness=harness,
                    store=store,
                    cancel_event=asyncio.Event(),
                    session_handle=SessionHandle(),
                    otel_run=run_span,
                )

            # ── Assertion 1: fanout_parent_ctx is set ─────────────────────
            assert result.status == "awaiting_children"
            assert result.fanout_parent_ctx is not None

            # ── Assertion 2: probe span parents under dispatching iter ─────
            iter_spans = [
                s for s in exporter.get_finished_spans()
                if s.name == "relay.iter"
            ]
            probe = otel._tracer.start_span(  # type: ignore[attr-defined]
                "probe", context=result.fanout_parent_ctx
            )
            probe.end()
            probe_span = next(
                s for s in exporter.get_finished_spans() if s.name == "probe"
            )
            assert probe_span.parent is not None
            # The one relay.iter span (seq=1, the fanout iter) must be the
            # probe's parent — that is the cross-run parenting invariant.
            assert len(iter_spans) == 1
            assert iter_spans[0].context.span_id == probe_span.parent.span_id
            return result
        finally:
            await engine.dispose()

    result = asyncio.run(scenario())
    assert result.fanout_parent_ctx is not None
