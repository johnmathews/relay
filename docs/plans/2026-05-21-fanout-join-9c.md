# Plan — Phase 9c (fanout-join: synthesizer iter + parent resume)

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9c)
**Predecessor:** `docs/plans/2026-05-21-fanout-join-9b.md` (merged as 381c147)
**Successor:** 9d (runtime cancel-cascade), 9e (dashboard "Children" pane), 9f (OTel span parenting across runs)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal

Close the fanout-join loop. When the last child of an `awaiting_children` parent reaches a terminal status, the orchestrator must:

1. Append one `subagent_return` event per child on the **parent's** stream, each carrying `{child_run_id, status, summary}`.
2. Append a single `child_runs_resolved` event (taxonomy slot reserved in 9a) summarising the cohort.
3. Read `join_prompt` from the closing fanout iter's `iters.signal_args["payload"]["join_prompt"]` (the implicit channel 9b chose under OCQ-1).
4. Transition the parent run `awaiting_children` → `running`.
5. Re-enqueue the parent with a `RunContext` whose `body` composes the `join_prompt` with a structured `RELAY_CHILD_RESULTS` trailer.
6. The supervisor picks the parent up and runs **one synthesizer iter** in a fresh harness session on the parent's existing worktree. That iter emits a normal terminal signal (`done`/`handoff`/`pause`/`fanout` — recursive fanout up to `max_fanout_depth` is permitted).

No new schema. No new sentinel grammar. No new harness/MCP/REST contract.

## Architecture

**Child-completion watcher (OCQ-3, ADR-36).** The watcher is a direct call from `_run` immediately after the child's `state.settled.set()` — the same task that already owns the child's terminal write. If `ctx.parent_run_id is not None`, the task calls `core._maybe_resume_parent(ctx.parent_run_id)`. That helper takes `_enqueue_lock`, re-reads the parent under the lock (skip if not `awaiting_children`), queries siblings, and resumes if all are terminal. Single-process MVP (ADR-12) makes the existing `_enqueue_lock` an acceptable serialiser — `resume_run` already uses it for the same shape of "look + decide + enqueue" race.

Rejected: a hook on `EventStore.append` (too generic — fires for every event, requires extra filtering and a post-commit reentrancy story); a background polling task (wastes CPU and lags); the `Broadcaster` (read-only/UI-facing, ADR-23). The chosen approach piggybacks on the existing run-task lifecycle, costs zero new threads/tasks, and aborts cleanly if the parent is no longer awaiting (e.g. already cascade-cancelled by 9d or a restart). See ADR-36.

**Synthesizer body shape (OCQ-5).** Distinct from `compose_resume_prompt`'s text shape — that helper is single-question/single-answer. Fanout-join is N children, structured. The synthesizer body is `join_prompt`, a separator, and a YAML-ish `RELAY_CHILD_RESULTS:` block (one entry per child, fields `id` / `role` / `status` / `summary` / `branch` / `worktree_path`). YAML-ish — not literal YAML — because the orchestrator hand-renders it without a YAML library; the skill reads it the same way it reads `RELAY_RUN_DIR` / `RELAY_PHASE` (line-based, key: value). The block is part of the body, NOT part of the `RELAY_*` preamble — the preamble is reserved for the two canonical fields (ADR-14 / `preamble.py`) and we don't bend that contract for a one-iter-per-run feature.

**Partial-failure semantics (OCQ-6).** The proposal (§cancellation-semantics) is explicit: *"cancelled child counts as resolved with status='cancelled'"*. The synthesizer always runs once all N children settle, regardless of mix. Each child's `status` ∈ `{done, failed, cancelled}` appears in the trailer; the agent decides whether a partial result is workable. The orchestrator never auto-fails the parent because a child failed — that's a policy decision better made by the join-iter agent armed with the trailer.

**OCQ-1 re-evaluation (OCQ-4).** 9b put `join_prompt` in `iters.signal_args["payload"]["join_prompt"]` (Option a, status-quo). With 9c's read now concrete (one `select(Iter)` ordered by `seq desc` inside `_maybe_resume_parent`), the implicit channel is no harder to use than a dedicated column would be. **Stay with (a).** Adding `iters.fanout_payload JSON` would mean a schema bump, model edit, and migration story for what is a single read-write pair within `core.py`. Re-evaluate again only if 9d/9e need to read the payload from a non-orchestrator surface — and even then, a `RelayCore.get_fanout_payload(run_id) -> dict | None` accessor is cheaper than a column.

**Tech stack.** No new runtime dependencies. The Pydantic `FanoutPayload` validator (added in 9b) is re-used to typecheck the payload on read.

## File map

| file | action | one-line responsibility |
|---|---|---|
| `src/relay_v2/orchestrator/lifecycle.py` | modify | add `compose_join_prompt(join_prompt, child_results) -> str`; add `latest_fanout_iter(sm, run_id) -> Iter | None`; export both |
| `src/relay_v2/core.py` | modify | add `_collect_child_results(parent_run_id) -> list[dict]`; add `_maybe_resume_parent(parent_run_id) -> None`; call from `_run` finally when `ctx.parent_run_id` set |
| `docs/spec.md` | modify | §3.2 finalise `subagent_return` payload (`summary`, not `result`); §6 add a "Join (9c)" subsection describing the synthesizer body and event ordering |
| `docs/decisions.md` | modify | append ADR-36 (watcher placement + synthesizer body shape) |
| `tests/orchestrator/test_lifecycle_join.py` | create | `compose_join_prompt` pure unit tests; `latest_fanout_iter` unit test |
| `tests/orchestrator/test_join_watcher.py` | create | `_maybe_resume_parent` unit tests: skip-when-not-awaiting; skip-when-some-still-running; emit subagent_return+child_runs_resolved+resume when all terminal; partial-failure (one failed/cancelled child still resumes); double-fire idempotency |
| `tests/orchestrator/test_fanout_join_integration.py` | create | end-to-end scripted fanout → 2 children done → synthesizer iter sees `RELAY_CHILD_RESULTS` → parent reaches `done` |

No frontend changes in 9c. The dashboard's Children pane is 9e; the existing RunDetail timeline shows the new events automatically (they go through the same `EventStore.append` → `Broadcaster.publish` path).

## ADR claim

**ADR-36** — next free number (`docs/decisions.md` ends at ADR-35 as of 381c147; grep confirms). Records OCQ-3 (watcher placement: in-`_run` direct call, not broadcaster hook) and OCQ-5 (synthesizer body shape: YAML-ish trailer in body, NOT preamble). OCQ-4 (join_prompt stays in `signal_args`) is recorded as a rationale paragraph in the same ADR.

## Open contract questions — resolved

- **OCQ-3 (watcher placement):** in-`_run` direct call to `_maybe_resume_parent` after `state.settled.set()`. Lock via existing `_enqueue_lock`. See ADR-36.
- **OCQ-4 (join_prompt channel):** stays in `iters.signal_args["payload"]["join_prompt"]`. See ADR-36 rationale.
- **OCQ-5 (synthesizer body shape):** YAML-ish `RELAY_CHILD_RESULTS:` trailer in body (not preamble); fields `id` / `role` / `status` / `summary` / `branch` / `worktree_path`. See ADR-36 + `compose_join_prompt` docstring.
- **OCQ-6 (partial-failure):** synthesizer always runs once all children settle; the orchestrator never auto-fails the parent on a child's failure. Per-child status appears in the trailer; the agent decides. See proposal §cancellation-semantics, codified in `test_join_watcher.py::test_resumes_with_mixed_child_outcomes`.

---

## Tasks (TDD-ordered)

---

### Task 1 — `compose_join_prompt` pure helper

**~15 min**

**Files:**
- Modify: `src/relay_v2/orchestrator/lifecycle.py`
- Test: `tests/orchestrator/test_lifecycle_join.py`

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_lifecycle_join.py`:

```python
"""Unit tests for the join-prompt composition helper (9c).

All offline, all pure-function — no DB, no pi.
"""
from __future__ import annotations

from relay_v2.orchestrator.lifecycle import compose_join_prompt


def test_compose_join_prompt_two_children_done() -> None:
    body = compose_join_prompt(
        "Synthesize the two audits and propose a unified fix list.",
        [
            {
                "id": "20260521-100000-aa",
                "role": "explorer-frontend",
                "status": "done",
                "summary": "Found 3 router bugs.",
                "branch": "relay/20260521-100000-aa",
                "worktree_path": "/tmp/.relay/worktrees/20260521-100000-aa",
            },
            {
                "id": "20260521-100000-bb",
                "role": "explorer-backend",
                "status": "done",
                "summary": "Found 2 schema drift issues.",
                "branch": "relay/20260521-100000-bb",
                "worktree_path": "/tmp/.relay/worktrees/20260521-100000-bb",
            },
        ],
    )
    assert body.startswith(
        "Synthesize the two audits and propose a unified fix list."
    )
    assert "RELAY_CHILD_RESULTS:" in body
    assert "- id: 20260521-100000-aa" in body
    assert "  role: explorer-frontend" in body
    assert "  status: done" in body
    assert "  summary: Found 3 router bugs." in body
    assert "  branch: relay/20260521-100000-aa" in body
    assert "  worktree_path: /tmp/.relay/worktrees/20260521-100000-aa" in body
    assert "- id: 20260521-100000-bb" in body
    assert "  role: explorer-backend" in body


def test_compose_join_prompt_preserves_join_prompt_first() -> None:
    body = compose_join_prompt(
        "Custom join instructions.",
        [{"id": "x", "role": "r", "status": "done", "summary": "s",
          "branch": "b", "worktree_path": "/p"}],
    )
    lines = body.split("\n")
    assert lines[0] == "Custom join instructions."
    # Separator + trailer header come after the join prompt.
    sep_idx = lines.index("---")
    trailer_idx = lines.index("RELAY_CHILD_RESULTS:")
    assert sep_idx < trailer_idx


def test_compose_join_prompt_one_child_mixed_status() -> None:
    body = compose_join_prompt(
        "Decide what to do.",
        [
            {"id": "a", "role": "r-a", "status": "done", "summary": "ok",
             "branch": "relay/a", "worktree_path": "/wt/a"},
            {"id": "b", "role": "r-b", "status": "cancelled",
             "summary": "user cancelled", "branch": "relay/b",
             "worktree_path": "/wt/b"},
            {"id": "c", "role": "r-c", "status": "failed",
             "summary": "timeout", "branch": "relay/c",
             "worktree_path": "/wt/c"},
        ],
    )
    assert "  status: done" in body
    assert "  status: cancelled" in body
    assert "  status: failed" in body
    # All three children rendered.
    assert body.count("- id: ") == 3


def test_compose_join_prompt_empty_summary_renders_as_empty_string() -> None:
    body = compose_join_prompt(
        "j",
        [{"id": "a", "role": "r", "status": "done", "summary": "",
          "branch": "relay/a", "worktree_path": "/wt/a"}],
    )
    # Empty summary still appears as 'summary:' — never omitted, to keep
    # the YAML-ish block uniform for the skill reader.
    assert "  summary: " in body


def test_compose_join_prompt_multiline_summary_indented() -> None:
    body = compose_join_prompt(
        "j",
        [{"id": "a", "role": "r", "status": "done",
          "summary": "line one\nline two", "branch": "relay/a",
          "worktree_path": "/wt/a"}],
    )
    # Multi-line summary uses YAML literal block to preserve newlines
    # without forcing the skill to handle ad-hoc escapes.
    assert "  summary: |" in body
    assert "    line one" in body
    assert "    line two" in body
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/orchestrator/test_lifecycle_join.py::test_compose_join_prompt_two_children_done -x
```

Expected: `ImportError` (no `compose_join_prompt` symbol yet).

- [ ] **Step 3: Implement**

In `src/relay_v2/orchestrator/lifecycle.py`, add to `__all__`:

```python
__all__ = [
    "RunContext",
    "compose_join_prompt",
    "compose_resume_prompt",
    "create_run",
    "latest_fanout_iter",
    "latest_paused_iter",
    "load_run",
    "open_iter",
    "close_iter",
    "provision_workspace",
    "register_project",
    "set_iter_session",
    "set_run_status",
]
```

Add `compose_join_prompt` near `compose_resume_prompt`:

```python
def compose_join_prompt(
    join_prompt: str, child_results: list[dict[str, str]]
) -> str:
    """The synthesizer iter's body: ``join_prompt`` followed by a
    structured ``RELAY_CHILD_RESULTS`` trailer (one entry per child).

    The trailer is YAML-ish (line-based ``key: value``, hand-rendered, no
    YAML library) so the engineering-team skill can read it the same way
    it reads the ``RELAY_*`` preamble lines. It lives in the body, not
    the preamble (ADR-14 — preamble is reserved for ``RELAY_RUN_DIR`` and
    ``RELAY_PHASE``). Multi-line summaries use YAML literal block
    (``summary: |``) so newlines survive untouched.

    Schema of each ``child_results`` entry (all ``str``):
    ``id``, ``role``, ``status``, ``summary``, ``branch``,
    ``worktree_path``. Empty values render as ``key:`` with a trailing
    space (never omitted) so the block is structurally uniform.
    """
    lines: list[str] = [join_prompt, "", "---", "RELAY_CHILD_RESULTS:"]
    for r in child_results:
        lines.append(f"  - id: {r['id']}")
        lines.append(f"    role: {r['role']}")
        lines.append(f"    status: {r['status']}")
        summary = r.get("summary", "")
        if "\n" in summary:
            lines.append("    summary: |")
            for sub in summary.split("\n"):
                lines.append(f"      {sub}")
        else:
            lines.append(f"    summary: {summary}")
        lines.append(f"    branch: {r['branch']}")
        lines.append(f"    worktree_path: {r['worktree_path']}")
    return "\n".join(lines) + "\n"
```

(Note the indentation: list items get a single leading space — `  - id: …` — so the YAML reader pattern is `  - id:` / `    role:` / etc. The tests expect this exact shape.)

Wait — re-read the test: it asserts `"- id: 20260521-100000-aa" in body` (no leading spaces). Adjust to **no leading spaces on the `- id:` line**, with two-space indent on subsequent fields:

```python
    for r in child_results:
        lines.append(f"- id: {r['id']}")
        lines.append(f"  role: {r['role']}")
        lines.append(f"  status: {r['status']}")
        summary = r.get("summary", "")
        if "\n" in summary:
            lines.append("  summary: |")
            for sub in summary.split("\n"):
                lines.append(f"    {sub}")
        else:
            lines.append(f"  summary: {summary}")
        lines.append(f"  branch: {r['branch']}")
        lines.append(f"  worktree_path: {r['worktree_path']}")
```

The literal-block indent uses 4 spaces (`    line one`) per the test assertion.

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/orchestrator/test_lifecycle_join.py -x
```

Expected: 5 pass.

```bash
uv run mypy src/relay_v2/orchestrator/lifecycle.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/orchestrator/lifecycle.py tests/orchestrator/test_lifecycle_join.py
git commit -m "$(cat <<'EOF'
feat(lifecycle): compose_join_prompt synthesizer body builder (9c)

Pure helper that renders the fanout-join synthesizer iter's body —
join_prompt followed by a YAML-ish RELAY_CHILD_RESULTS trailer with one
entry per child. Multi-line summaries use literal block. Lives in body,
not preamble (ADR-14 reserves the preamble for RELAY_RUN_DIR/PHASE).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2 — `latest_fanout_iter` accessor

**~10 min**

**Files:**
- Modify: `src/relay_v2/orchestrator/lifecycle.py`
- Test: `tests/orchestrator/test_lifecycle_join.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/orchestrator/test_lifecycle_join.py`:

```python
import asyncio
from pathlib import Path

import pytest

from relay_v2.config import Settings
from relay_v2.db import init_db, make_async_engine, make_async_sessionmaker
from relay_v2.db.models import Iter, Run
from relay_v2.orchestrator.lifecycle import (
    close_iter,
    create_run,
    latest_fanout_iter,
    open_iter,
)


def test_latest_fanout_iter_returns_most_recent_fanout_iter(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()

    async def scenario() -> None:
        engine = make_async_engine(settings.async_db_url)
        sm = make_async_sessionmaker(engine)
        try:
            # Project + run row (project FK satisfied by direct insert).
            async with sm() as s:
                from relay_v2.db.models import Project
                s.add(Project(root_path=str(tmp_path), name="p"))
                await s.commit()
                project = (await s.scalars(
                    __import__("sqlalchemy").select(Project)
                )).one()
            await create_run(
                sm, run_id="r-1", project_id=project.id,
                prompt_body="p", max_iters=4, iter_timeout=60,
                worktree_path=None, branch=None,
            )
            # Iter 1: handoff (not fanout).
            i1 = await open_iter(sm, run_id="r-1", seq=1, phase=None,
                                 prompt="x", preamble="")
            await close_iter(sm, i1, signal_kind="handoff",
                             signal_args={"next_prompt": "y"},
                             exit_reason="signal")
            # Iter 2: fanout — this one should win.
            i2 = await open_iter(sm, run_id="r-1", seq=2, phase=None,
                                 prompt="x", preamble="")
            await close_iter(sm, i2, signal_kind="fanout",
                             signal_args={"payload": {
                                 "children": [
                                     {"role": "a", "prompt": "do a"}
                                 ],
                                 "join_prompt": "merge",
                             }},
                             exit_reason="signal")

            row = await latest_fanout_iter(sm, "r-1")
            assert row is not None
            assert row.seq == 2
            assert row.signal_kind == "fanout"
            assert row.signal_args is not None
            assert row.signal_args["payload"]["join_prompt"] == "merge"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_latest_fanout_iter_none_when_no_fanout(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()

    async def scenario() -> None:
        engine = make_async_engine(settings.async_db_url)
        sm = make_async_sessionmaker(engine)
        try:
            row = await latest_fanout_iter(sm, "nonexistent-run")
            assert row is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/orchestrator/test_lifecycle_join.py::test_latest_fanout_iter_returns_most_recent_fanout_iter -x
```

Expected: ImportError on `latest_fanout_iter`.

- [ ] **Step 3: Implement**

In `src/relay_v2/orchestrator/lifecycle.py`, add after `latest_paused_iter`:

```python
async def latest_fanout_iter(
    sm: async_sessionmaker[AsyncSession], run_id: str
) -> Iter | None:
    """The most recent ``signal_kind='fanout'`` iter for ``run_id``.

    Read by ``RelayCore._maybe_resume_parent`` (9c) to recover the
    ``join_prompt`` from ``signal_args["payload"]["join_prompt"]``.
    Mirrors :func:`latest_paused_iter` for the resume path.
    """
    async with sm() as s:
        row: Iter | None = await s.scalar(
            select(Iter)
            .where(Iter.run_id == run_id, Iter.signal_kind == "fanout")
            .order_by(Iter.seq.desc())
            .limit(1)
        )
        return row
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/orchestrator/test_lifecycle_join.py -x
```

Expected: all 7 pass.

```bash
uv run mypy src/relay_v2/orchestrator/lifecycle.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/orchestrator/lifecycle.py tests/orchestrator/test_lifecycle_join.py
git commit -m "$(cat <<'EOF'
feat(lifecycle): latest_fanout_iter accessor (9c)

Mirrors latest_paused_iter for the fanout resume path — locates the
closing fanout iter so RelayCore._maybe_resume_parent can recover
join_prompt from signal_args["payload"].

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3 — `_collect_child_results` helper on `RelayCore`

**~25 min**

Gathers the per-child rows needed for `subagent_return` events + the synthesizer trailer. Reads from `runs` + the closing `run_ended` event per child.

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_join_watcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_join_watcher.py`:

```python
"""Unit tests for RelayCore's fanout-join watcher (9c).

Covers:
- _collect_child_results: shape + ordering by started_at.
- _maybe_resume_parent: skip-when-not-awaiting; skip-when-some-running;
  emits subagent_return/child_runs_resolved and re-enqueues when all
  children terminal; mixed-status (partial-failure) still resumes;
  double-fire idempotency.

All scripted (no pi). Direct DB seeding for the watcher's preconditions.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db
from relay_v2.db.models import Event, Iter, Project, Run
from relay_v2.orchestrator.lifecycle import (
    close_iter,
    create_run,
    open_iter,
    set_run_status,
)
from tests.orchestrator.scripted_harness import ScriptedHarness


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


async def _seed_fanout_state(
    core: RelayCore,
    project_root: Path,
    *,
    child_statuses: list[str],
    child_summaries: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Seed a parent in awaiting_children with N child rows + their
    closing run_ended events.

    Returns (parent_run_id, [child_run_id, ...]).
    """
    project_id = await core.register_project(project_root, "p")
    parent_id = core._new_run_id()
    await create_run(
        core._sm, run_id=parent_id, project_id=project_id,
        prompt_body="parent", max_iters=4, iter_timeout=60,
        worktree_path=str(project_root), branch=None,
    )
    # Parent run_started + closing fanout iter with the payload 9b would
    # have written. The synthesizer needs join_prompt from here.
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
                for i in range(len(child_statuses))
            ],
            "join_prompt": "Synthesize.",
        }},
        exit_reason="signal",
    )
    await set_run_status(core._sm, parent_id, "awaiting_children",
                        ended=False)

    child_ids: list[str] = []
    summaries = child_summaries or [
        f"child {i} ok" for i in range(len(child_statuses))
    ]
    for i, (status, summary) in enumerate(
        zip(child_statuses, summaries, strict=True)
    ):
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
        await set_run_status(core._sm, cid, status, ended=True)
        await core._store.append(
            cid, "run_ended", {"status": status, "summary": summary},
        )
        child_ids.append(cid)
    return parent_id, child_ids


def test_collect_child_results_returns_one_per_child(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario() -> list[dict[str, Any]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["frontend ok", "backend ok"],
            )
            return await core._collect_child_results(parent_id)
        finally:
            await core._engine.dispose()

    results = asyncio.run(scenario())
    assert len(results) == 2
    statuses = {r["status"] for r in results}
    summaries = {r["summary"] for r in results}
    assert statuses == {"done"}
    assert summaries == {"frontend ok", "backend ok"}
    for r in results:
        assert set(r.keys()) >= {
            "id", "role", "status", "summary", "branch", "worktree_path"
        }
        assert r["branch"].startswith("relay/")


def test_collect_child_results_uses_subagent_dispatch_role(
    tmp_path: Path,
) -> None:
    """Role comes from the parent's subagent_dispatch event payload, not
    a column on the child run (we don't store it there).
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[list[dict[str, Any]], list[str]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path, child_statuses=["done", "done"],
            )
            # Emit subagent_dispatch events on the parent — the watcher
            # joins these to children by child_run_id to recover role.
            for i, cid in enumerate(child_ids):
                await core._store.append(
                    parent_id, "subagent_dispatch",
                    {"child_run_id": cid, "role": f"role-{i}",
                     "prompt": f"p-{i}"},
                )
            return await core._collect_child_results(parent_id), child_ids
        finally:
            await core._engine.dispose()

    results, child_ids = asyncio.run(scenario())
    by_id = {r["id"]: r for r in results}
    assert by_id[child_ids[0]]["role"] == "role-0"
    assert by_id[child_ids[1]]["role"] == "role-1"


def test_collect_child_results_includes_all_children(tmp_path: Path) -> None:
    """Three children dispatched → trailer has three entries, regardless
    of within-second ordering. (Strict dispatch-order is not asserted
    here — SQLite current_timestamp is second-precision so two
    same-second inserts have identical ``started_at``; the secondary
    ``id`` tiebreaker is timestamp-prefixed + random hex, non-deterministic
    within a second. The trailer's stability for the agent is "all N
    children present, status field correct"; the strict dispatch order
    is enforced indirectly through the parent's ``subagent_dispatch``
    event seqs which downstream tooling can correlate.)
    """
    settings = _settings(tmp_path)

    async def scenario() -> list[str]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done", "done"],
            )
            results = await core._collect_child_results(parent_id)
            return [r["id"] for r in results]
        finally:
            await core._engine.dispose()

    returned_ids = asyncio.run(scenario())
    assert len(returned_ids) == 3
    assert len(set(returned_ids)) == 3  # no duplicates
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py::test_collect_child_results_returns_one_per_child -x
```

Expected: `AttributeError: '_collect_child_results'`.

- [ ] **Step 3: Implement**

In `src/relay_v2/core.py`, add after `_dispatch_children` (before `aclose`):

```python
    async def _collect_child_results(
        self, parent_run_id: str
    ) -> list[dict[str, str]]:
        """Gather per-child result dicts for the synthesizer trailer (9c).

        Ordering: children sorted by ``started_at`` asc so the trailer is
        deterministic and matches dispatch order. Role is recovered from
        the parent's ``subagent_dispatch`` events (we don't store role
        on the child run row — single source of truth is the dispatch
        event, ADR-10). Summary is the closing ``run_ended`` event's
        ``summary`` field (empty string if absent).

        Schema of each entry (all ``str``):
        ``id`` / ``role`` / ``status`` / ``summary`` / ``branch`` /
        ``worktree_path``. Empty branch / worktree_path become empty
        strings, never None — the trailer formatter expects strings.
        """
        async with self._sm() as s:
            children = list(
                await s.scalars(
                    select(Run)
                    .where(Run.parent_run_id == parent_run_id)
                    .order_by(Run.started_at.asc())
                )
            )
            # role lookup from subagent_dispatch events on the parent.
            dispatches = list(
                await s.scalars(
                    select(Event).where(
                        Event.run_id == parent_run_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
        role_by_child: dict[str, str] = {}
        for ev in dispatches:
            cid = ev.payload.get("child_run_id")
            role = ev.payload.get("role", "")
            if isinstance(cid, str):
                role_by_child[cid] = role if isinstance(role, str) else ""

        results: list[dict[str, str]] = []
        for child in children:
            async with self._sm() as s:
                ended = await s.scalar(
                    select(Event)
                    .where(
                        Event.run_id == child.id,
                        Event.kind == "run_ended",
                    )
                    .order_by(Event.seq.desc())
                    .limit(1)
                )
            summary = ""
            if ended is not None:
                raw = ended.payload.get("summary", "")
                summary = raw if isinstance(raw, str) else ""
            results.append({
                "id": child.id,
                "role": role_by_child.get(child.id, ""),
                "status": child.status,
                "summary": summary,
                "branch": child.branch or "",
                "worktree_path": child.worktree_path or "",
            })
        return results
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x -k collect
```

Expected: 3 collect tests pass.

```bash
uv run mypy src/relay_v2/core.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_join_watcher.py
git commit -m "$(cat <<'EOF'
feat(core): _collect_child_results gathers synthesizer trailer rows (9c)

One dict per child run, ordered by started_at, role joined from the
parent's subagent_dispatch events (single source of truth — we don't
store role on the child run row). Summary from the closing run_ended
event payload. Feeds compose_join_prompt + the subagent_return events
9c will emit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4 — `_maybe_resume_parent` watcher (skip cases)

**~30 min**

Three skip cases come first (TDD: easier-to-test guards before the happy path).

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_join_watcher.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/orchestrator/test_join_watcher.py`:

```python
def test_maybe_resume_parent_no_op_when_parent_not_awaiting(
    tmp_path: Path,
) -> None:
    """Cascade-cancelled / already-resumed parent: watcher returns
    silently, emits no events, enqueues nothing.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            # Flip parent off awaiting_children (simulate cascade-cancel
            # or an already-fired watcher).
            await set_run_status(core._sm, parent_id, "cancelled",
                                 ended=True)
            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            qsize_after = core._queue.qsize()
            return parent_id, qsize_after - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 0

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            ]
            assert "subagent_return" not in kinds
            assert "child_runs_resolved" not in kinds
    finally:
        engine.dispose()


def test_maybe_resume_parent_no_op_when_some_children_still_running(
    tmp_path: Path,
) -> None:
    """Watcher must NOT resume if any sibling is still non-terminal."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "running"],
            )
            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            return parent_id, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 0

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "awaiting_children"
            assert "subagent_return" not in {
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            }
    finally:
        engine.dispose()


def test_maybe_resume_parent_no_op_when_parent_unknown(
    tmp_path: Path,
) -> None:
    """Unknown parent id — never raises."""
    settings = _settings(tmp_path)

    async def scenario() -> None:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            # Should be a silent no-op, not a raise.
            await core._maybe_resume_parent("does-not-exist")
        finally:
            await core._engine.dispose()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x -k maybe_resume
```

Expected: `AttributeError: '_maybe_resume_parent'`.

- [ ] **Step 3: Implement (skeleton + skip cases only)**

In `src/relay_v2/core.py`, add after `_collect_child_results`:

```python
    async def _maybe_resume_parent(self, parent_run_id: str) -> None:
        """If ``parent_run_id`` is in ``awaiting_children`` AND all its
        children have reached a terminal status, emit one
        ``subagent_return`` per child + one ``child_runs_resolved``,
        transition the parent to ``running``, and re-enqueue it with a
        synthesizer ``RunContext`` (9c).

        No-op (silent) when:
        - the parent row is unknown,
        - the parent status is not ``awaiting_children`` (already
          resumed, cascade-cancelled by 9d/restart, or never awaiting),
        - any child is still non-terminal.

        The single-user MVP ``_enqueue_lock`` (already serialising
        ``resume_run``'s look-then-decide-then-enqueue) is reused so
        two near-simultaneous child terminals can't both resume the
        parent.
        """
        terminal = ("done", "failed", "cancelled")
        async with self._enqueue_lock:
            async with self._sm() as s:
                parent = await s.get(Run, parent_run_id)
                if parent is None or parent.status != "awaiting_children":
                    return
                children = list(
                    await s.scalars(
                        select(Run).where(
                            Run.parent_run_id == parent_run_id
                        )
                    )
                )
            if not children:
                return
            if any(c.status not in terminal for c in children):
                return
            # Happy path lands in Task 5.
            return
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x -k maybe_resume
```

Expected: 3 skip-case tests pass.

```bash
uv run mypy src/relay_v2/core.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_join_watcher.py
git commit -m "$(cat <<'EOF'
feat(core): _maybe_resume_parent skeleton + skip cases (9c)

Skips when parent unknown, not awaiting_children, or any sibling still
non-terminal. _enqueue_lock-guarded so the look-then-decide-then-
enqueue race resume_run already handles is also covered here. Happy
path arrives in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5 — `_maybe_resume_parent` happy path (resume + events)

**~50 min**

This is the load-bearing change: emit `subagent_return` events, the `child_runs_resolved` event, transition the parent, and re-enqueue with a synthesizer `RunContext`.

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_join_watcher.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/orchestrator/test_join_watcher.py`:

```python
def test_maybe_resume_parent_emits_events_and_enqueues(
    tmp_path: Path,
) -> None:
    """Happy path: two children done → two subagent_return + one
    child_runs_resolved + parent status running + one new queue entry.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, list[str], int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["frontend ok", "backend ok"],
            )
            # Need a _RunState entry so resume can install a fresh one;
            # simulate "parent's first task settled" exactly like _run
            # would have left it.
            from relay_v2.core import _RunState
            from relay_v2.orchestrator.loop import LoopResult
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].result = LoopResult(
                "awaiting_children", reason="signal",
            )
            core._runs[parent_id].settled.set()

            qsize_before = core._queue.qsize()
            await core._maybe_resume_parent(parent_id)
            return parent_id, child_ids, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, child_ids, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 1, "synthesizer RunContext should be enqueued"

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "running"
            assert parent.ended_at is None

            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                    .order_by(Event.seq.asc())
                )
            ]
            assert kinds.count("subagent_return") == 2
            assert kinds.count("child_runs_resolved") == 1
            # Ordering: all subagent_return events precede child_runs_resolved.
            first_resolved = kinds.index("child_runs_resolved")
            last_return = max(
                i for i, k in enumerate(kinds) if k == "subagent_return"
            )
            assert last_return < first_resolved

            returns = list(s.scalars(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "subagent_return",
                ).order_by(Event.seq.asc())
            ))
            return_ids = {e.payload["child_run_id"] for e in returns}
            assert return_ids == set(child_ids)
            for r in returns:
                assert r.payload["status"] == "done"
                assert r.payload["summary"] in {"frontend ok", "backend ok"}

            resolved = s.scalar(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "child_runs_resolved",
                )
            )
            assert resolved is not None
            assert resolved.payload["children_count"] == 2
            assert set(resolved.payload["terminal_statuses"].keys()) == set(
                child_ids
            )
            assert all(
                v == "done"
                for v in resolved.payload["terminal_statuses"].values()
            )
    finally:
        engine.dispose()


def test_maybe_resume_parent_synthesizer_runcontext_body(
    tmp_path: Path,
) -> None:
    """The enqueued RunContext.body must start with join_prompt and
    contain a RELAY_CHILD_RESULTS trailer with one entry per child.
    """
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
                child_summaries=["a ok", "b ok"],
            )
            from relay_v2.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()

            await core._maybe_resume_parent(parent_id)
            # Peek the queue without blocking the supervisor.
            ctx = await core._queue.get()
            core._queue.task_done()
            return ctx.body
        finally:
            await core._engine.dispose()

    body = asyncio.run(scenario())
    assert body.startswith("Synthesize.")
    assert "RELAY_CHILD_RESULTS:" in body
    assert body.count("- id: ") == 2


def test_maybe_resume_parent_continues_iter_seq(tmp_path: Path) -> None:
    """The synthesizer iter must continue from the closing fanout iter's
    seq + 1 (the loop does seq += 1 on entry, so start_seq is the
    closing iter's seq).
    """
    settings = _settings(tmp_path)

    async def scenario() -> int:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            from relay_v2.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()
            await core._maybe_resume_parent(parent_id)
            ctx = await core._queue.get()
            core._queue.task_done()
            return ctx.start_seq
        finally:
            await core._engine.dispose()

    start_seq = asyncio.run(scenario())
    # Closing fanout iter was seq=1; synthesizer continues from there.
    assert start_seq == 1


def test_maybe_resume_parent_partial_failure_still_resumes(
    tmp_path: Path,
) -> None:
    """Mixed child outcomes — one done, one failed, one cancelled —
    still resumes the parent. OCQ-6: orchestrator never auto-fails the
    parent on a child's failure; the agent decides via the trailer.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, str]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "failed", "cancelled"],
                child_summaries=["ok", "timed out", "user cancelled"],
            )
            from relay_v2.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()
            await core._maybe_resume_parent(parent_id)
            ctx = await core._queue.get()
            core._queue.task_done()
            return parent_id, ctx.body
        finally:
            await core._engine.dispose()

    parent_id, body = asyncio.run(scenario())
    assert "  status: done" in body
    assert "  status: failed" in body
    assert "  status: cancelled" in body

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "running"  # parent resumed, NOT failed
    finally:
        engine.dispose()


def test_maybe_resume_parent_idempotent_under_double_fire(
    tmp_path: Path,
) -> None:
    """Two concurrent watcher calls (last two children settle near-
    simultaneously) must not double-resume — exactly one enqueue, one
    set of return events, one child_runs_resolved.
    """
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, int]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_fanout_state(
                core, tmp_path,
                child_statuses=["done", "done"],
            )
            from relay_v2.core import _RunState
            core._runs[parent_id] = _RunState()
            core._runs[parent_id].settled.set()

            qsize_before = core._queue.qsize()
            # Fire twice concurrently.
            await asyncio.gather(
                core._maybe_resume_parent(parent_id),
                core._maybe_resume_parent(parent_id),
            )
            return parent_id, core._queue.qsize() - qsize_before
        finally:
            await core._engine.dispose()

    parent_id, qsize_delta = asyncio.run(scenario())
    assert qsize_delta == 1, "exactly one synthesizer should be enqueued"

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id)
                )
            ]
            assert kinds.count("subagent_return") == 2
            assert kinds.count("child_runs_resolved") == 1
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x -k emits_events
```

Expected: queue delta is 0, status not `running`, no `subagent_return` events.

- [ ] **Step 3: Implement happy path**

First extend the existing top-of-module import in `src/relay_v2/core.py` (around lines 49–58) so the lifecycle imports include the two new symbols. Find this block:

```python
from relay_v2.orchestrator.lifecycle import (
    RunContext,
    compose_resume_prompt,
    create_run,
    latest_paused_iter,
    load_run,
    provision_workspace,
    register_project,
    set_run_status,
)
```

Replace with:

```python
from relay_v2.orchestrator.lifecycle import (
    RunContext,
    compose_join_prompt,
    compose_resume_prompt,
    create_run,
    latest_fanout_iter,
    latest_paused_iter,
    load_run,
    provision_workspace,
    register_project,
    set_run_status,
)
```

Then replace the `# Happy path lands in Task 5.` block in `_maybe_resume_parent` with:

```python
            # All children terminal: emit returns + resolved, transition,
            # enqueue synthesizer.
            results = await self._collect_child_results(parent_run_id)
            for r in results:
                await self._store.append(
                    parent_run_id,
                    "subagent_return",
                    {
                        "child_run_id": r["id"],
                        "status": r["status"],
                        "summary": r["summary"],
                    },
                )
            await self._store.append(
                parent_run_id,
                "child_runs_resolved",
                {
                    "children_count": len(results),
                    "terminal_statuses": {
                        r["id"]: r["status"] for r in results
                    },
                },
            )

            # Recover join_prompt from the closing fanout iter; refuse
            # to resume if it's missing (defensive — a structurally
            # impossible state given 9b's writes, but we'd rather leave
            # the parent in awaiting_children than enqueue a
            # synthesizer with an empty prompt).
            closing = await latest_fanout_iter(self._sm, parent_run_id)
            if closing is None or closing.signal_args is None:
                logger.error(
                    "fanout-join: parent %s has no closing fanout iter; "
                    "leaving in awaiting_children",
                    parent_run_id,
                )
                return
            payload = closing.signal_args.get("payload") or {}
            if not isinstance(payload, dict):
                logger.error(
                    "fanout-join: parent %s closing iter payload is not "
                    "a dict (got %r); leaving in awaiting_children",
                    parent_run_id, type(payload).__name__,
                )
                return
            join_prompt = payload.get("join_prompt", "")
            if not isinstance(join_prompt, str) or not join_prompt:
                logger.error(
                    "fanout-join: parent %s has empty join_prompt; "
                    "leaving in awaiting_children",
                    parent_run_id,
                )
                return

            body = compose_join_prompt(join_prompt, results)

            # Transition projection first, then enqueue (mirrors
            # resume_run's order so SSE consumers see the status flip
            # before the next iter_started lands).
            await set_run_status(
                self._sm, parent_run_id, "running", ended=False
            )

            # Resolve the project + parent worktree for the new ctx.
            async with self._sm() as s:
                parent_row = await s.get(Run, parent_run_id)
                if parent_row is None:
                    return
                project = await s.get(Project, parent_row.project_id)
            if project is None:
                logger.error(
                    "fanout-join: parent %s project missing; cannot resume",
                    parent_run_id,
                )
                return

            run_dir = self._settings.data_dir / "runs" / parent_run_id
            phase_file = run_dir / "phase"
            phase = (
                phase_file.read_text().strip()
                if phase_file.exists() else None
            )
            self._runs[parent_run_id] = _RunState()
            await self._queue.put(
                RunContext(
                    run_id=parent_run_id,
                    project_root=Path(project.root_path),
                    worktree_path=Path(parent_row.worktree_path)
                    if parent_row.worktree_path else None,
                    run_dir=run_dir,
                    max_iters=parent_row.max_iters,
                    iter_timeout=parent_row.iter_timeout,
                    start_seq=closing.seq,  # loop does seq += 1 on entry
                    phase=phase,
                    body=body,
                    parent_run_id=parent_row.parent_run_id,
                )
            )
```

Drop the trailing `return` placeholder.

(`_RunState` is defined at module top in `core.py`; `RunContext` and the lifecycle helpers are now imported at the top of the file from the block edited above. No function-local imports.)

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x
```

Expected: all 8 tests pass.

```bash
uv run mypy src/relay_v2/core.py
```

Expected: clean.

```bash
uv run ruff check src/relay_v2/core.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_join_watcher.py
git commit -m "$(cat <<'EOF'
feat(core): _maybe_resume_parent emits returns + resumes synthesizer (9c)

Happy path of the fanout-join watcher. When all children of an
awaiting_children parent are terminal: emit one subagent_return per
child, one child_runs_resolved, transition the parent to running, and
enqueue a synthesizer RunContext whose body is join_prompt +
compose_join_prompt's RELAY_CHILD_RESULTS trailer. Partial-failure
(mixed done/failed/cancelled) still resumes; the agent decides via the
trailer (OCQ-6). _enqueue_lock keeps double-fires idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6 — Wire watcher into `_run` finally

**~15 min**

Trigger `_maybe_resume_parent` after a child run's task settles.

**Files:**
- Modify: `src/relay_v2/core.py`

- [ ] **Step 1: Inspect existing `_run` finally block**

Open `src/relay_v2/core.py` and locate the `_run` method's `finally` clause (around lines 462–469):

```python
            finally:
                # Guarantee waiters wake even if _apply_result raised — a
                # never-set settled would hang wait_for_run forever.
                if state.result is None:
                    state.result = LoopResult(
                        "failed", reason="internal_error"
                    )
                state.settled.set()
```

- [ ] **Step 2: Extend finally with watcher dispatch**

Replace the `finally` block with:

```python
            finally:
                # Guarantee waiters wake even if _apply_result raised — a
                # never-set settled would hang wait_for_run forever.
                if state.result is None:
                    state.result = LoopResult(
                        "failed", reason="internal_error"
                    )
                state.settled.set()
                # Fanout-join (9c): when a child run settles, give its
                # parent a chance to resume. Idempotent + lock-guarded
                # in _maybe_resume_parent; a no-op when the parent is
                # not awaiting_children (cascade-cancelled, already
                # resumed by a sibling, or this run isn't a child at
                # all). Best-effort — a watcher failure must not leak
                # back into the run task's shutdown.
                if ctx.parent_run_id is not None:
                    with contextlib.suppress(Exception):
                        await self._maybe_resume_parent(ctx.parent_run_id)
```

- [ ] **Step 3: Re-run the watcher tests (regression)**

```bash
uv run pytest tests/orchestrator/test_join_watcher.py -x
```

Expected: still all 8 pass.

```bash
uv run pytest tests/orchestrator/test_fanout_dispatch.py tests/orchestrator/test_fanout_loop.py tests/orchestrator/test_fanout_integration.py -x
```

Expected: all existing 9b tests still pass (no regression). Note that `test_fanout_to_two_children_full_scenario` from 9b previously asserted the parent stays in `awaiting_children` with no `run_ended` event — it will now resume and proceed to a synthesizer iter that the scripted harness's `DONE` script handles. **This test needs to be updated**: see Task 7.

- [ ] **Step 4: Commit**

```bash
git add src/relay_v2/core.py
git commit -m "$(cat <<'EOF'
feat(core): wire _maybe_resume_parent into _run finally (9c)

Child run task settles → if it has a parent, call _maybe_resume_parent
in the finally. Lock + skip-checks inside the helper handle the no-op
cases; the suppress() guards the run-task shutdown path from any
watcher failure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7 — Update the 9b integration test for the new resume behaviour

**~20 min**

`test_fanout_to_two_children_full_scenario` (in `tests/orchestrator/test_fanout_integration.py`) was written under 9b's contract: parent stays in `awaiting_children`, no `run_ended`. Now the parent resumes and runs a synthesizer iter. The scripted harness in that test has THREE `TextScript` entries — parent fanout, child A done, child B done. It does NOT have a fourth script for the synthesizer iter; once the synthesizer iter spawns, the harness raises (scripts exhausted).

**Files:**
- Modify: `tests/orchestrator/test_fanout_integration.py`

- [ ] **Step 1: Inspect the scripted harness**

```bash
sed -n '1,60p' tests/orchestrator/scripted_harness.py
```

Confirm it raises (or returns crash) when scripts are exhausted. If it raises, the parent will end up `failed` post-9c. The 9b test then needs either:

- **(a)** an added fourth script `DONE` for the synthesizer iter, plus assertions that the parent ends in `done`, OR
- **(b)** the test is replaced wholesale by a 9c integration test in a new file, with the 9b file kept as a regression for the dispatch + iter-emission shape pre-resume.

Choose **(a)**: the test name already says "full scenario", and a four-script run is a tighter end-to-end. The previous assertions about `parent.status == "awaiting_children"` and "no `run_ended`" become incorrect post-9c — replace them.

- [ ] **Step 2: Edit `test_fanout_to_two_children_full_scenario`**

```python
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
```

- [ ] **Step 3: Run the existing 9b suite (with the patched test)**

```bash
uv run pytest tests/orchestrator/test_fanout_integration.py -x
```

Expected: both tests in the file pass (the OCQ-2 cascade test from 9b is unaffected).

- [ ] **Step 4: Commit**

```bash
git add tests/orchestrator/test_fanout_integration.py
git commit -m "$(cat <<'EOF'
test(integration): update 9b fanout integration for 9c resume (9c)

The 9b test was written against "parent stays in awaiting_children";
9c lands the synthesizer iter, so the parent now reaches done. Adds a
fourth scripted DONE for the synthesizer, asserts the full event
sequence (dispatch < return < resolved < run_ended) and that the
parent ends terminal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8 — Dedicated 9c integration test (synthesizer reads trailer)

**~30 min**

The Task 7 update covers the event-ordering / status invariants. This task adds a separate test that asserts the **synthesizer iter receives the `RELAY_CHILD_RESULTS` trailer in its prompt** — the contract the engineering-team skill will read on.

**Files:**
- Create: `tests/orchestrator/test_fanout_join_integration.py`

- [ ] **Step 1: Write the test**

Create `tests/orchestrator/test_fanout_join_integration.py`:

```python
"""Phase 9c end-to-end — synthesizer iter sees RELAY_CHILD_RESULTS.

Scripted-harness integration test for the full fanout/join round-trip:
parent fanout → 2 children done → synthesizer iter prompt carries the
join_prompt and the YAML-ish RELAY_CHILD_RESULTS trailer with one entry
per child → synthesizer emits done.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Event, Iter, Run
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
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/orchestrator/test_fanout_join_integration.py -x -v
```

Expected: PASS.

- [ ] **Step 3: Full orchestrator regression**

```bash
uv run pytest tests/orchestrator/ -x
```

Expected: all green (existing 9b tests + new 9c tests).

- [ ] **Step 4: Commit**

```bash
git add tests/orchestrator/test_fanout_join_integration.py
git commit -m "$(cat <<'EOF'
test(integration): synthesizer iter prompt carries child results (9c)

End-to-end scripted run: parent fanout → 2 children done → synthesizer
iter. Asserts the synthesizer's iter.prompt contains join_prompt + the
YAML-ish RELAY_CHILD_RESULTS trailer with one - id: per child, plus
role/status/summary/branch. The contract the engineering-team skill
reads on.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9 — Spec updates

**~20 min**

**Files:**
- Modify: `docs/spec.md`

- [ ] **Step 1: Update §3.2 `subagent_return` payload**

The current spec.md §3.2 row says `{child_run_id, status, result}`. Implementation writes `{child_run_id, status, summary}`. Align spec → implementation (we never used a structured `result` field):

```bash
grep -n "subagent_return" docs/spec.md
```

Open the row and change the payload column to `{child_run_id, status, summary}`. Update any surrounding prose that mentions `result`.

- [ ] **Step 2: Add §6 "Join (9c)" subsection**

After the existing "Subagent dispatch" paragraph in §6 (search for "Subagent dispatch.** If `signal.kind"), insert:

```markdown
**Join (9c).** When all children of an `awaiting_children` parent reach
a terminal status (`done`, `failed`, or `cancelled`), the orchestrator:

1. Appends one `subagent_return` event per child on the parent's stream
   (`{child_run_id, status, summary}` — `summary` is the child's
   closing `run_ended` payload `summary`, empty string when absent).
2. Appends one `child_runs_resolved` event
   (`{children_count, terminal_statuses}` — `terminal_statuses` is a
   `dict[run_id, status]`).
3. Transitions the parent run `awaiting_children` → `running`.
4. Re-enqueues the parent with a synthesizer `RunContext`. The
   synthesizer iter's body is `join_prompt` (recovered from the closing
   fanout iter's `iters.signal_args["payload"]["join_prompt"]`) followed
   by a `---` separator and a YAML-ish `RELAY_CHILD_RESULTS:` trailer
   listing the per-child `id` / `role` / `status` / `summary` /
   `branch` / `worktree_path`. Multi-line summaries use YAML literal
   block (`summary: |`).

The trailer lives in the body, not the `RELAY_*` preamble (ADR-14 reserves
the preamble for `RELAY_RUN_DIR` and `RELAY_PHASE`). The synthesizer iter
runs on the **parent's existing worktree** (no new worktree provisioned —
the join is supposed to see the parent's pre-fanout state, not a sibling).
Recursive fanout from the synthesizer is permitted up to `max_fanout_depth`.

Partial-failure semantics: the synthesizer ALWAYS runs once all children
settle, regardless of how many failed or were cancelled. The orchestrator
does not auto-fail the parent on a child's failure — the agent decides via
the trailer (ADR-36).

A child-completion watcher (ADR-36) is an in-process direct call from the
child's `_run` task, lock-guarded by `RelayCore._enqueue_lock`. The watcher
is a no-op when the parent is not `awaiting_children` (already resumed by
a sibling, or cascade-cancelled).
```

- [ ] **Step 3: Verify**

```bash
grep -n "Join (9c)" docs/spec.md
```

Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add docs/spec.md
git commit -m "$(cat <<'EOF'
docs(spec): join semantics + subagent_return payload (9c)

§3.2 — subagent_return payload aligned with implementation
({child_run_id, status, summary}; we never wrote a structured `result`
field). §6 — new "Join (9c)" subsection: event ordering
(subagent_return → child_runs_resolved → run_ended), synthesizer body
shape (join_prompt + YAML-ish RELAY_CHILD_RESULTS trailer), worktree
reuse (no new worktree for the synthesizer), partial-failure semantics
(synthesizer always runs; orchestrator never auto-fails parent on
child failure).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10 — ADR-36

**~20 min**

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Confirm ADR-36 is next free**

```bash
grep -n "^## ADR-" docs/decisions.md | tail -3
```

Expected: ADR-35 is the last existing entry. If ADR-36 already exists (e.g. landed concurrently from skill-variants), bump to the next free number — but as of 381c147 it's 36.

- [ ] **Step 2: Append ADR-36 to `docs/decisions.md`**

At the bottom of the file:

```markdown
## ADR-36 — Fanout-join watcher placement + synthesizer body shape

**Status:** accepted (2026-05-21)
**Phase:** 9c (fanout-join: synthesizer iter + parent resume)

**Context.** Phase 9b lands fanout dispatch — a parent emits
`[[engteam:fanout]]`, children spawn, parent enters `awaiting_children`.
Phase 9c closes the loop: when all children settle, append the
synthesizer iter on the parent's stream. Three design questions
resolved in this ADR:

- **OCQ-3 — where does the child-completion watcher live?**
- **OCQ-5 — what is the shape of the synthesizer iter's prompt body?**
- **OCQ-4 — does `join_prompt` move out of `iters.signal_args` into a
  dedicated column now that 9c has to read it?**

### Decision (OCQ-3: watcher placement)

**In-process direct call from the child's `_run` task, lock-guarded by
the existing `RelayCore._enqueue_lock`.** After `state.settled.set()`
in `_run`'s `finally`, if `ctx.parent_run_id is not None`, call
`core._maybe_resume_parent(ctx.parent_run_id)`. The helper takes
`_enqueue_lock`, re-reads the parent under the lock, returns silently
when the parent is no longer `awaiting_children` (cascade-cancel,
already resumed by a sibling, or never awaiting). When all siblings
are terminal: emit `subagent_return` × N + `child_runs_resolved`,
transition parent → `running`, enqueue synthesizer `RunContext`.

**Rationale.** The child's `_run` task already owns the child's
terminal write — it is the natural notification point with full local
context (`ctx.parent_run_id`) and zero new task plumbing. The existing
`_enqueue_lock` is the right serialiser: it is the same lock
`resume_run` uses for the look-then-decide-then-enqueue race, which is
exactly the race a near-simultaneous "last two children settle"
introduces. Single-user MVP (ADR-12) makes the lock's coarse scope
acceptable.

**Rejected — `EventStore.append` post-commit hook.** Fires on every
event, requires kind/status filtering, and introduces reentrancy
concerns (the watcher itself appends events through the same store).
Strictly more code and more failure modes for the same observable
behaviour.

**Rejected — background polling task.** Wastes CPU; lags by the poll
interval; conflicts with the "everything routes through `RelayCore`"
invariant (ADR-07).

**Rejected — `Broadcaster` post-publish hook.** The broadcaster is a
read-only/UI-facing observer (ADR-23) — never the right place to land
orchestrator state transitions.

### Decision (OCQ-5: synthesizer body shape)

**`join_prompt` followed by a `---` separator and a YAML-ish
`RELAY_CHILD_RESULTS:` trailer (one `- id: …` entry per child, with
`role` / `status` / `summary` / `branch` / `worktree_path` indented
underneath). Multi-line summaries use YAML literal block
(`summary: |`).** Hand-rendered, no YAML library. Lives in the body,
NOT the preamble.

**Rationale.** Distinct from `compose_resume_prompt`'s text shape —
that helper is one question/one answer; fanout-join is N children,
structured. The skill reads it the same way it reads the `RELAY_*`
preamble lines (line-based `key: value`). Keeping it in the body
preserves ADR-14's invariant that the preamble carries exactly
`RELAY_RUN_DIR` and `RELAY_PHASE` and nothing else — bending that for
a one-iter-per-fanout-event feature would compromise the canonical
contract.

**Rejected — JSON in a fenced code block.** Heavier to read for the
skill (it'd need a JSON parser); the YAML-ish trailer is line-readable
with the same patterns the skill already uses.

**Rejected — extending the preamble with a third reserved field.**
Violates ADR-14. The synthesizer trailer is per-iter content, not
per-run frame.

### Decision (OCQ-4: join_prompt channel)

**Stays in `iters.signal_args["payload"]["join_prompt"]` (9b's
status-quo Option a).** Re-evaluated with the 9c read concrete, and
the implicit channel is no harder to use than a dedicated column —
one `select(Iter)` filtered by `signal_kind='fanout'`, ordered by
`seq desc`, exactly mirroring `latest_paused_iter` for the resume
path.

**Rationale.** A dedicated `iters.fanout_payload JSON` column would
require a schema bump (hand-rolled `create_all`, ADR-17), a model
edit, and a migration story for a single read-write pair both inside
`core.py`. The orchestrator owns both ends; the implicit dependency is
guarded by `test_fanout_loop.py::test_closing_iter_signal_args_contains_payload`
(9b) and the new `test_fanout_join_integration.py` (9c). Promote only
if 9d/9e need to read the payload from a non-orchestrator surface —
and even then, a `RelayCore.get_fanout_payload(run_id)` accessor is
cheaper than a column.

### Decision (OCQ-6: partial-failure)

**Synthesizer always runs once all children settle; the orchestrator
never auto-fails the parent on a child's failure.** Each child's
status appears in the trailer; the agent decides whether the partial
result is workable.

**Rationale.** Codifies the proposal §cancellation-semantics decision
("cancelled child counts as resolved with status=`cancelled`"). The
orchestrator does not have the domain context to decide whether a
single failed explorer makes the join unworkable — the agent that
wrote the `join_prompt` does. Honest separation of concerns.

### Consequences

- A child run task that crashes uncleanly (raises into `_run`'s outer
  except) still calls `_maybe_resume_parent` from the `finally` — the
  watcher's lock + status re-read make this safe (the parent observes
  the crashed child as `failed` via ADR-31's safety-net writes).
- Two children settling near-simultaneously may both invoke the watcher;
  the lock + re-read keeps exactly one of them through the happy-path
  branch (`test_maybe_resume_parent_idempotent_under_double_fire`).
- The synthesizer iter runs on the parent's existing worktree (no new
  worktree provisioned). Child branches survive in the data dir for the
  agent's perusal — the orchestrator never auto-merges (proposal
  §tradeoffs).

**Related:** ADR-07/15 (RelayCore single chokepoint), ADR-14 (preamble
reserved fields), ADR-20 (pause/resume — the mechanism this resume
mirrors), ADR-23 (broadcaster scope: read-only/UI-facing),
ADR-31/32/34 (run finalisation + orphan/cascade — the safety net the
watcher relies on for crashed children), ADR-35 (fanout concurrency
cap — 9b sibling), proposal `docs/proposals/parallel-iters-fanout-join.md`.
```

- [ ] **Step 3: Verify**

```bash
grep -n "^## ADR-36" docs/decisions.md
```

Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions.md
git commit -m "$(cat <<'EOF'
docs(adr): ADR-36 fanout-join watcher + synthesizer body (9c)

Records OCQ-3 (watcher placement: in-_run direct call, _enqueue_lock-
guarded), OCQ-5 (synthesizer body shape: YAML-ish RELAY_CHILD_RESULTS
trailer in body, NOT preamble), OCQ-4 rationale for keeping
join_prompt in signal_args, and OCQ-6 partial-failure semantics
(synthesizer always runs; orchestrator never auto-fails parent on
child failure).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11 — Full gate

**~10 min**

- [ ] **Step 1: Backend full gate**

```bash
uv run pytest
```

Expected: ~252–255 passed (237 from 9b baseline + ~15 new from 9c: 5 lifecycle_join + 8 join_watcher + 1 new integration; minus 0 deletions). 3 pi-e2e still gated behind `PI_INTEGRATION=1`.

```bash
uv run ruff check .
```

Expected: clean.

```bash
uv run mypy
```

Expected: clean (strict, 39 source files — no new source files in 9c, all changes are edits to existing files).

- [ ] **Step 2: Frontend gate**

```bash
cd frontend && npm run check
```

Expected: 142 passed (no frontend changes in 9c).

- [ ] **Step 3: If anything fails, fix and re-run**

Do NOT proceed past this gate until everything is green. A failure here means a sequencing error in the plan — fix the underlying issue rather than skipping the check.

- [ ] **Step 4: No commit yet**

The CLAUDE.md update lands in Task 12 as a single commit; the gate is the precondition for that commit.

---

### Task 12 — CLAUDE.md update

**~15 min**

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the "Current state" paragraph that records Phase 9b**

```bash
grep -n "Phase 9b" CLAUDE.md
```

Locate the closing sentences of the existing 9b paragraph (the final paragraph of "Current state"). The 9c addition appends to that paragraph, matching the existing density / phrasing / format.

- [ ] **Step 2: Append 9c paragraph**

Add a new paragraph immediately after the existing 9b paragraph:

```
**Phase 9c** then closes the fanout-join loop (`docs/plans/2026-05-21-
fanout-join-9c.md`): a new `RelayCore._maybe_resume_parent` watcher
fired from each child's `_run` `finally` block (ADR-36, OCQ-3) — when
all siblings of an `awaiting_children` parent reach a terminal status,
the watcher emits one `subagent_return` per child + one
`child_runs_resolved`, transitions the parent `awaiting_children →
running`, and re-enqueues it with a synthesizer `RunContext` whose
body is `compose_join_prompt(join_prompt, child_results)` — the
`join_prompt` recovered from the closing fanout iter's
`signal_args["payload"]["join_prompt"]` (OCQ-1's 9b channel kept,
OCQ-4 evaluated and held), the trailer a YAML-ish `RELAY_CHILD_RESULTS:`
block listing each child's `id`/`role`/`status`/`summary`/`branch`/
`worktree_path` (OCQ-5: body, NOT preamble — ADR-14's
`RELAY_RUN_DIR`/`RELAY_PHASE` invariant unchanged). The synthesizer
iter runs on the parent's existing worktree (no new worktree for the
join); recursive fanout from the synthesizer is permitted up to
`max_fanout_depth`. Partial-failure semantics: the synthesizer always
runs once all children settle regardless of mix; the orchestrator
never auto-fails the parent on a child's failure — the agent decides
via the trailer (OCQ-6, proposal §cancellation-semantics). The
existing `_enqueue_lock` serialises the watcher so two children
settling near-simultaneously cannot both resume the parent. New
ADR-36 records the watcher-placement + body-shape decisions. 9d
lands the runtime cancel-cascade (the `_cascade_cancel_descendants`
helper from 9a is wired into `cancel_run`); 9e the dashboard
"Children" pane; 9f OTel span parenting across runs.
```

Also update the test count + source-file count notes if they appear in the same paragraph (`uv run pytest` count goes from "237" to whatever the new full-gate run reported; source-file count stays 39 — no new modules).

- [ ] **Step 3: Final gate sanity check**

```bash
uv run pytest 2>&1 | tail -3
```

Confirm the count + status before committing.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE.md): record Phase 9c under Current state

Phase 9c paragraph appended: _maybe_resume_parent watcher (OCQ-3),
compose_join_prompt synthesizer body shape (OCQ-5), join_prompt
channel held in signal_args (OCQ-4), partial-failure semantics
(OCQ-6), ADR-36 added. 9d/9e/9f noted as still open follow-ups.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Verification commands (post-task summary)

```bash
# Per-task verification (run after each task)
uv run pytest tests/orchestrator/test_lifecycle_join.py -v
uv run pytest tests/orchestrator/test_join_watcher.py -v
uv run pytest tests/orchestrator/test_fanout_integration.py -v
uv run pytest tests/orchestrator/test_fanout_join_integration.py -v

# Final full gate (Task 11)
uv run pytest
uv run ruff check .
uv run mypy
cd frontend && npm run check
```

---

## Out of scope (deferred to later sub-phases)

- **Runtime cancel-cascade (9d).** `cancel_run` does not yet walk the
  child tree at runtime; the helper exists (`_cascade_cancel_descendants`
  from 9a) but is only wired into the startup orphan sweep. 9d wires it
  into the runtime path so cancelling an `awaiting_children` parent
  cancels its in-flight children too.
- **Dashboard "Children" pane (9e).** The new events (`subagent_return`,
  `child_runs_resolved`) flow through the existing SSE pipeline and
  render in the timeline automatically; a dedicated parent-side
  "Children" panel listing child run-ids with click-through is 9e's job.
- **OTel span parenting across runs (9f).** Child runs spawn under
  their own `relay.run` spans; 9f wires them as children of the parent
  iter's span so a Langfuse trace shows the full tree.
- **REST `POST /api/runs` `parent_run_id` exposure.** Internal
  `start_run(parent_run_id=…)` parameter is still not surfaced over
  HTTP; only `_dispatch_children` uses it. Deferred until a real
  external need arises (9e or later).
- **Worktree merge.** The synthesizer iter has shell access to the
  parent worktree AND to each child's worktree path (via the trailer);
  it decides whether/how to merge. The orchestrator never auto-merges
  (proposal §tradeoffs — "honest about the complexity").
- **Skill-side guidance (`skills/engineering-team/pi/`).** Adding a
  `references/fanout.md` and phase-specific guidance is a separate
  workstream — the orchestrator side is fully usable without it (a
  skill-emitted fanout sentinel still drives the round-trip), but the
  skill won't *emit* fanout until that doc exists. Tracked as part of
  9e's "dashboard + skill" bundle in the proposal.

---

## Risks and what could go wrong

- **`_run` finally calls `_maybe_resume_parent` even when the run task
  is in `aclose()` shutdown.** During `aclose()`, the supervisor cancels
  all tasks; each task's finally runs, including this new watcher call.
  The watcher acquires `_enqueue_lock` (still alive during `aclose`'s
  task wait), reads from `self._sm` (still alive — `_engine.dispose()`
  is *after* the task wait in `aclose`), and may try to `_queue.put` a
  synthesizer ctx that no one will pick up (the supervisor is already
  cancelled). The orphan-recovery sweep on the next startup catches
  any `running` parent left over. Acceptable for V1; document.

- **Mixed-status child summaries with multi-line text.** If a `failed`
  child's `run_ended` summary is a multi-line stack trace, the YAML
  literal block (`summary: |`) keeps the trailer well-formed but the
  body grows. Bound by the existing `TOOL_RESULT_CAP` only indirectly
  (run_ended summaries are not capped today). If a future child writes
  a 10 KB summary, the synthesizer's prompt grows by 10 KB. Tracked
  but not addressed in 9c — bound the run_ended summary length only
  if it bites.

- **Closing fanout iter missing `signal_args` payload.** The defensive
  early-returns in `_maybe_resume_parent` log an error and leave the
  parent in `awaiting_children`. This is structurally impossible given
  9b's writes (Task 8 of 9b unconditionally writes
  `signal_args={"payload": …}` for fanout), but the defensive log keeps
  a future schema change from silently breaking the join.

- **Two-`wait_for_run` test pattern.** The synthesizer-iter
  integration tests await `wait_for_run(parent_id)` *twice* — once for
  the `awaiting_children` settle, once for the synthesizer `done`
  settle. This works because `_maybe_resume_parent` installs a fresh
  `_RunState` in `self._runs[parent_id]` before enqueuing, and the
  second `wait_for_run` call reads the fresh state. If a future
  refactor changes the dict-replace timing, these tests are the
  canary.

- **`subagent_return` payload field name.** Spec.md §3.2 originally
  documented `result`; we use `summary` to match the closing
  `run_ended` event payload (single shape for "what did this run
  produce"). Task 9 aligns the spec to the implementation. If anything
  was reading the (never-emitted) `result` field, that consumer
  doesn't exist yet — 9c is the first writer.

- **Compose helper indentation.** `compose_join_prompt` uses
  literal-string indentation (no leading spaces on `- id:`, two-space
  indent on subsequent fields, four-space indent inside `summary: |`
  literal blocks). A formatter regression here breaks every
  downstream parse — guarded by five distinct unit tests in
  `test_lifecycle_join.py`.

---

## Effort estimate

- Task 1 (compose_join_prompt + tests): 30 min.
- Task 2 (latest_fanout_iter + tests): 20 min.
- Task 3 (_collect_child_results + tests): 45 min.
- Task 4 (_maybe_resume_parent skeleton + skip tests): 45 min.
- Task 5 (_maybe_resume_parent happy path + tests): 60 min.
- Task 6 (wire into _run finally): 15 min.
- Task 7 (update 9b integration test): 30 min.
- Task 8 (dedicated 9c integration test): 30 min.
- Task 9 (spec updates): 20 min.
- Task 10 (ADR-36): 30 min.
- Task 11 (full gate): 10 min (assuming green).
- Task 12 (CLAUDE.md): 15 min.

Total ~5–6 hours focused. One commit per task = 12 commits, all in one PR.
