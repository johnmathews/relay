"""Phase 9b/9c integration test - scripted fanout-to-2-children + OCQ-2 cascade.

Scenario 1 (fanout-to-2-children, proposal §9b dispatch + §9c join):
- Parent iter emits [[engteam:fanout]] with 2 children.
- Both children execute independently and reach done.
- Watcher then transitions parent awaiting_children → running and runs a
  synthesizer iter; parent reaches done.

Scenario 2 (OCQ-2 - restart-with-awaiting-children cascade):
- Parent in awaiting_children + 2 children still 'running' are seeded,
  then _recover_orphans is called directly.
- All three must finalise to 'cancelled' with the cascade summary on
  children and the server-restart summary on the parent.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay.config import Settings
from relay.core import RelayCore
from relay.db import init_db
from relay.db.models import Event, Iter, Run
from relay.orchestrator.lifecycle import create_run, set_run_status
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_TWO = (
    "Dispatching two explorers.\n\n"
    "[[engteam:fanout-start]]\n"
    "{"
    '"children": ['
    '{"role": "explorer-frontend", "prompt": "Audit frontend."},'
    '{"role": "explorer-backend", "prompt": "Audit backend."}'
    "],"
    '"join_prompt": "Synthesize the two audits."'
    "}\n"
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
DONE = "Audit complete.\n\n[[engteam:done]]"


def test_fanout_to_two_children_full_scenario(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    # Scripts: parent fanout, child A done, child B done, parent synthesizer done.
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE),
         TextScript(DONE)]
    )

    async def _run() -> dict[str, object]:
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            pid = await core.register_project(tmp_path, "p")
            parent_id = await core.start_run(pid, "Investigate the system.")
            # First settle: awaiting_children.
            first = await core.wait_for_run(parent_id)
            assert first.status == "awaiting_children"
            engine = create_engine(settings.db_url)
            try:
                with Session(engine) as s:
                    children = list(
                        s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                    )
            finally:
                engine.dispose()
            child_ids = [c.id for c in children]
            assert len(child_ids) == 2
            for cid in child_ids:
                cr = await core.wait_for_run(cid)
                assert cr.status == "done", f"child {cid}: {cr.status}"
            # Second settle: the synthesizer iter (a fresh _RunState).
            second = await core.wait_for_run(parent_id)
            assert second.status == "done", (
                f"synthesizer expected done, got {second.status}"
            )
            return {"parent_id": parent_id, "child_ids": child_ids}
        finally:
            await core.aclose()

    result = asyncio.run(_run())
    parent_id = result["parent_id"]
    assert isinstance(parent_id, str)
    child_ids = result["child_ids"]
    assert isinstance(child_ids, list)

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "done"
            assert parent.ended_at is not None

            parent_events = list(
                s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                    .order_by(Event.seq.asc())
                )
            )
            parent_kinds = [e.kind for e in parent_events]
            assert parent_kinds[0] == "run_started"
            assert parent_kinds[-1] == "run_ended"
            assert parent_kinds.count("subagent_dispatch") == 2
            assert parent_kinds.count("subagent_return") == 2
            assert parent_kinds.count("child_runs_resolved") == 1

            # Ordering invariant: dispatch < return < resolved < final run_ended.
            first_dispatch = parent_kinds.index("subagent_dispatch")
            last_return = max(
                i for i, k in enumerate(parent_kinds) if k == "subagent_return"
            )
            resolved_idx = parent_kinds.index("child_runs_resolved")
            run_ended_idx = parent_kinds.index("run_ended")
            assert first_dispatch < last_return < resolved_idx < run_ended_idx

            # Closing fanout iter still present (now seq=1, synthesizer is seq=2).
            iters = list(
                s.scalars(
                    select(Iter).where(Iter.run_id == parent_id)
                    .order_by(Iter.seq.asc())
                )
            )
            assert len(iters) == 2
            assert iters[0].signal_kind == "fanout"
            assert iters[1].signal_kind == "done"

            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.parent_run_id == parent_id
                assert child.status == "done"
    finally:
        engine.dispose()


def test_restart_with_awaiting_children_cascades_descendants(
    tmp_path: Path,
) -> None:
    """OCQ-2 regression: a parent in awaiting_children at startup must be
    cascade-cancelled together with its running children.

    Simulates the "process died mid-fanout, server restart" scenario by
    seeding the DB directly (parent=awaiting_children, 2 children=running),
    then calling _recover_orphans without starting the supervisor.
    """
    settings = Settings(data_dir=tmp_path / ".relay")

    async def _run() -> tuple[str, list[str]]:
        # Build a core without calling start() so _recover_orphans
        # does not run yet (we'll call it explicitly).
        core = RelayCore(settings, harness=ScriptedHarness([]))
        # Schema setup (idempotent - also done by start()).
        init_db(settings).dispose()
        try:
            project_id = await core.register_project(tmp_path, "p")
            parent_id = core._new_run_id()
            child_a = core._new_run_id()
            child_b = core._new_run_id()
            for rid, prompt, parent in (
                (parent_id, "parent", None),
                (child_a, "child-a", parent_id),
                (child_b, "child-b", parent_id),
            ):
                await create_run(
                    core._sm,
                    run_id=rid,
                    project_id=project_id,
                    prompt_body=prompt,
                    max_iters=settings.max_iters,
                    iter_timeout=settings.iter_timeout,
                    worktree_path=None,
                    branch=None,
                    parent_run_id=parent,
                )
            # Flip the parent to awaiting_children (children stay 'running').
            await set_run_status(
                core._sm, parent_id, "awaiting_children", ended=False
            )
            # Now trigger the recovery sweep directly.
            await core._recover_orphans()
            return parent_id, [child_a, child_b]
        finally:
            await core._engine.dispose()

    parent_id, child_ids = asyncio.run(_run())

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "cancelled"
            assert parent.ended_at is not None
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.status == "cancelled"
                assert child.ended_at is not None
            # Cascade emitted run_ended for each.
            for rid in [parent_id, *child_ids]:
                ended = list(
                    s.scalars(
                        select(Event).where(
                            Event.run_id == rid,
                            Event.kind == "run_ended",
                        )
                    )
                )
                assert len(ended) == 1, (
                    f"expected 1 run_ended for {rid}, got {len(ended)}"
                )
            # Children get the cascade summary; parent gets server-restart.
            for cid in child_ids:
                ev = s.scalar(
                    select(Event).where(
                        Event.run_id == cid,
                        Event.kind == "run_ended",
                    )
                )
                assert ev is not None
                assert "parent interrupted during fanout" in ev.payload["summary"]
            parent_ev = s.scalar(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "run_ended",
                )
            )
            assert parent_ev is not None
            assert "server restart" in parent_ev.payload["summary"]
    finally:
        engine.dispose()
