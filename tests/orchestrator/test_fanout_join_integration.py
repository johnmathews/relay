"""Phase 9c end-to-end — synthesizer iter sees RELAY_CHILD_RESULTS.

Scripted-harness integration test for the full fanout/join round-trip:
parent fanout → 2 children done → synthesizer iter prompt carries the
join_prompt and the YAML-ish RELAY_CHILD_RESULTS trailer with one entry
per child → synthesizer emits done.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay.config import Settings
from relay.core import RelayCore
from relay.db.models import Event, Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_TWO = (
    "Dispatching.\n\n"
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
CHILD_A_DONE = "Frontend audit complete.\n\n[[engteam:done]]"
CHILD_B_DONE = "Backend audit complete.\n\n[[engteam:done]]"
SYNTH_DONE = "Synthesis complete.\n\n[[engteam:done]]"


def test_synthesizer_iter_prompt_carries_child_results_trailer(
    tmp_path: Path,
) -> None:
    # Git-init the project root so child runs get real worktrees with
    # ``relay/<run_id>`` branches — the trailer's ``branch:`` field is
    # only populated when ``provision_workspace`` succeeds.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path,
                   check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    settings = Settings(data_dir=tmp_path / ".relay")
    harness = ScriptedHarness([
        TextScript(FANOUT_TWO),
        TextScript(CHILD_A_DONE),
        TextScript(CHILD_B_DONE),
        TextScript(SYNTH_DONE),
    ])

    async def _run() -> str:
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            pid = await core.register_project(tmp_path, "p")
            parent_id = await core.start_run(pid, "Investigate.")
            # First settle: awaiting_children. Children then drain.
            assert (await core.wait_for_run(parent_id)).status == \
                "awaiting_children"
            engine = create_engine(settings.db_url)
            try:
                with Session(engine) as s:
                    children = list(
                        s.scalars(
                            select(Run).where(Run.parent_run_id == parent_id)
                        )
                    )
            finally:
                engine.dispose()
            for c in children:
                await core.wait_for_run(c.id)
            # Second settle: synthesizer iter completes.
            second = await core.wait_for_run(parent_id)
            assert second.status == "done"
            return parent_id
        finally:
            await core.aclose()

    parent_id = asyncio.run(_run())

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            iters = list(
                s.scalars(
                    select(Iter).where(Iter.run_id == parent_id)
                    .order_by(Iter.seq.asc())
                )
            )
            assert len(iters) == 2
            synth = iters[1]
            assert synth.signal_kind == "done"
            # The synthesizer iter's prompt is preamble + body. Body
            # starts with join_prompt; the trailer is in the body.
            prompt = synth.prompt
            assert "Synthesize the two audits." in prompt
            assert "RELAY_CHILD_RESULTS:" in prompt
            assert "- id: " in prompt
            assert prompt.count("- id: ") == 2
            assert "  role: explorer-frontend" in prompt
            assert "  role: explorer-backend" in prompt
            assert "  status: done" in prompt
            assert "  summary: " in prompt
            assert "  branch: relay/" in prompt

            # And the subagent_return events carry the same summaries.
            returns = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "subagent_return",
                    )
                )
            )
            assert len(returns) == 2
            statuses = {r.payload["status"] for r in returns}
            assert statuses == {"done"}
    finally:
        engine.dispose()
