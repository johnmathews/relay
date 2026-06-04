"""Recovery-iter regression coverage (WU3 — resilient-iter-close arc).

When a task-mode iter ends with a clean stop_reason and no terminal
sentinel, the loop should issue exactly ONE corrective recovery iter
carrying a RELAY_RECOVERY_NOTICE body asking the agent to re-emit a
closing sentinel. If the recovery iter itself produces a clean
terminal signal (e.g. ``done``), the run finalises normally. If the
recovery iter also ends with no signal, WU4's auto-pause kicks in
(covered in test_autopause_fallback.py — landed in the next WU).

A marker-contract violation (handoff with no prompt-start/prompt-end)
is NOT recoverable — it is a real bug, not a missing-sentinel
omission. That path still returns ``failed``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from relay.core import RelayCore
from relay.db.models import Event, Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript
from tests.orchestrator.test_loop import (
    DONE_BLOCK,
    FENCED_NO_SIGNAL,
    HANDOFF_NO_MARKERS,
    _read,
    _run,
    _settings,
)


def test_clean_no_signal_triggers_recovery_iter(tmp_path: Path) -> None:
    """Iter 1 ends clean with no sentinel → recovery iter (iter 2) carries
    a RELAY_RECOVERY_NOTICE prompt; if iter 2 emits ``done``, the run
    finalises as ``done``."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(DONE_BLOCK)]
    )

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
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        assert [it.seq for it in iters] == [1, 2]
        assert iters[0].signal_kind is None
        assert iters[0].exit_reason == "agent_end_no_signal"
        assert "RELAY_RECOVERY_NOTICE" in iters[1].prompt
        assert iters[1].signal_kind == "done"
        # The recovery iter's `iter_ended` event carries `recovery_iter: true`.
        ended_evs = list(
            s.scalars(
                select(Event)
                .where(Event.run_id == run_id, Event.kind == "iter_ended")
                .order_by(Event.seq)
            )
        )
        assert ended_evs[1].payload.get("recovery_iter") is True
        assert "recovery_iter" not in ended_evs[0].payload


def test_marker_violation_skips_recovery_iter(tmp_path: Path) -> None:
    """A marker-contract violation (handoff with no prompt markers) is
    NOT a missing-sentinel case — relay should NOT spend a recovery
    iter on it; current ``failed`` behaviour stands."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_NO_MARKERS)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "agent_end_no_signal"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        # Exactly one iter — no recovery iter spawned.
        assert len(iters) == 1


def test_recovery_iter_does_not_consume_max_iters(tmp_path: Path) -> None:
    """A run with max_iters=1 that no-signals on iter 1 should still get
    its recovery iter — the recovery shot is a +1 extension, not a
    consumption of the user-budgeted iter count."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(DONE_BLOCK)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", max_iters=1)
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        assert [it.seq for it in iters] == [1, 2]
