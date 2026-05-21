"""Phase 9d — runtime cancel-cascade tests.

Covers the runtime path where ``cancel_run(parent)`` on an
``awaiting_children`` parent must (a) flip the parent to ``cancelled``,
(b) signal every in-flight descendant via the cancel event + session,
(c) DB-finalise any descendant without an in-memory state, (d) do so
under ``_enqueue_lock`` so the join watcher cannot race a resume.

All scripted, no pi.
"""
from __future__ import annotations

from pathlib import Path

from relay_v2.config import Settings
from relay_v2.core import RelayCore, _RunState
from relay_v2.orchestrator.lifecycle import (
    close_iter,
    create_run,
    open_iter,
    set_run_status,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


async def _seed_awaiting_parent(
    core: RelayCore,
    project_root: Path,
    *,
    n_children: int = 2,
    child_statuses: list[str] | None = None,
    install_in_memory_state: bool = True,
) -> tuple[str, list[str]]:
    """Seed an awaiting_children parent + N children.

    ``child_statuses`` defaults to all 'running' (the live in-flight case).
    When ``install_in_memory_state`` is True, also install a
    ``_RunState`` in ``core._runs`` for each running child — emulating
    the runtime state a real ``_dispatch_children`` would have left.
    """
    project_id = await core.register_project(project_root, "p")
    parent_id = core._new_run_id()
    await create_run(
        core._sm, run_id=parent_id, project_id=project_id,
        prompt_body="parent", max_iters=4, iter_timeout=60,
        worktree_path=str(project_root), branch=None,
    )
    await core._store.append(
        parent_id, "run_started",
        {"project_id": project_id, "prompt_body": "parent", "max_iters": 4},
    )
    iter_id = await open_iter(
        core._sm, run_id=parent_id, seq=1, phase=None,
        prompt="parent", preamble="",
    )
    await close_iter(
        core._sm, iter_id, signal_kind="fanout",
        signal_args={"payload": {
            "children": [
                {"role": f"r-{i}", "prompt": f"do {i}"}
                for i in range(n_children)
            ],
            "join_prompt": "Synthesize.",
        }},
        exit_reason="signal",
    )
    await set_run_status(core._sm, parent_id, "awaiting_children",
                        ended=False)

    statuses = child_statuses or ["running"] * n_children
    assert len(statuses) == n_children, (
        f"child_statuses length {len(statuses)} != n_children {n_children}"
    )
    child_ids: list[str] = []
    for i, status in enumerate(statuses):
        cid = core._new_run_id()
        await create_run(
            core._sm, run_id=cid, project_id=project_id,
            prompt_body=f"do {i}", max_iters=4, iter_timeout=60,
            worktree_path=str(project_root / f"wt-{i}"),
            branch=f"relay/{cid}", parent_run_id=parent_id,
        )
        await core._store.append(
            cid, "run_started",
            {"project_id": project_id, "prompt_body": f"do {i}",
             "max_iters": 4},
        )
        if status != "running":
            await set_run_status(core._sm, cid, status, ended=True)
            await core._store.append(
                cid, "run_ended", {"status": status, "summary": f"c{i}"},
            )
        elif install_in_memory_state:
            core._runs[cid] = _RunState()
        child_ids.append(cid)
    return parent_id, child_ids
