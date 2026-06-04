"""Auto-pause fallback (WU4 — resilient-iter-close arc, ADR-53).

When the WU3 recovery iter itself ends clean with no terminal
sentinel, the run auto-pauses with a synthesised pause_requested
event instead of failing. The dashboard's existing PauseAnswerForm
picks it up unchanged.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from relay.core import RelayCore
from relay.db.models import Event, Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript
from tests.orchestrator.test_loop import (
    FENCED_NO_SIGNAL,
    _read,
    _run,
    _settings,
)


def test_recovery_iter_also_no_signal_auto_pauses(tmp_path: Path) -> None:
    """Two consecutive clean+no-signal iters: the run lands paused, not
    failed; a pause_requested event carries the recovery question."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(FENCED_NO_SIGNAL)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "paused"
        assert result.reason == "agent_end_no_signal_autopause"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "paused"
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        assert [it.seq for it in iters] == [1, 2]
        assert all(it.exit_reason == "agent_end_no_signal" for it in iters)
        # The closing event is pause_requested with the autopause question.
        last = s.scalars(
            select(Event).where(Event.run_id == run_id)
            .order_by(Event.seq.desc()).limit(1)
        ).one()
        assert last.kind == "pause_requested"
        assert "auto-paused" in last.payload["question"].lower()
        # No run_ended event — the run is not terminal.
        ended = list(
            s.scalars(
                select(Event).where(
                    Event.run_id == run_id, Event.kind == "run_ended"
                )
            )
        )
        assert ended == []


def test_autopause_iter_carries_synth_signal_args(tmp_path: Path) -> None:
    """The auto-paused (recovery) iter's row stores signal_kind=pause and
    signal_args containing the synth pause_id and question, so dashboard
    timeline rendering does not special-case the autopause variant."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(FENCED_NO_SIGNAL)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        recovery_iter = s.scalars(
            select(Iter)
            .where(Iter.run_id == run_id, Iter.seq == 2)
        ).one()
        assert recovery_iter.signal_kind == "pause"
        args = recovery_iter.signal_args or {}
        assert args["id"].startswith(f"autopause-{run_id}-")
        assert "auto-paused" in args["question"].lower()
        assert args["next_prompt"] == ""
        assert args["review_paths"] == []
