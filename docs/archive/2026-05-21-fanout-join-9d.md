# Plan — Phase 9d (runtime cancel-cascade)

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9d)
**Predecessors:** 9a (cascade helper + `awaiting_children`, PR #2 / 4ebb1f8), 9b (dispatch, PR #3 / 381c147), 9c (join watcher, PR #4 / 37b8cb7)
**Successors:** 9e (dashboard "Children" pane), 9f (OTel span parenting across runs)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal

Wire `_cascade_cancel_descendants` (added in 9a, currently called only from the startup orphan sweep) into the **runtime** `cancel_run` path. Today, clicking Cancel on an `awaiting_children` parent sets the parent's `cancel_event` — which nobody listens to (the parent has no `_run` task; it's waiting for the watcher). Children continue running. The user-visible UX is "Cancel does nothing".

After 9d:

1. `cancel_run(parent)` where `parent.status == "awaiting_children"` flips the parent to `cancelled` + writes `run_ended`, then **signals every in-flight descendant** (sets each `cancel_event` + cancels the harness session) and **DB-finalises any descendant whose `_RunState` is gone** (queued-but-not-started, or transient state loss).
2. The cancel order — parent first, then descendants — is load-bearing: it stops the join watcher (running concurrently in a child's `_run` finally) from racing back to `running` mid-cancel.
3. The cancel uses the existing `_enqueue_lock` so it can't interleave with `_maybe_resume_parent` (same chokepoint the watcher and `resume_run` already serialise on).

No new schema. No new event kinds. No new sentinel grammar. No new harness/MCP/REST contract — `cancel_run` is already exposed via `POST /api/runs/{id}/cancel` and the MCP `relay__cancel_run` tool; 9d strengthens its semantics without changing its signature.

## Architecture

**Cancel order (load-bearing).** Flip the parent OUT of `awaiting_children` *before* touching descendants. The join watcher (`_maybe_resume_parent`) acquires `_enqueue_lock`, re-reads the parent under the lock, and bails if `parent.status != "awaiting_children"`. As long as `cancel_run` also takes the lock and the parent flip happens inside it, no watcher invocation can resume the parent mid-cancel. If we cancelled children first and the parent last, a child's terminal write → watcher invocation could see "parent still awaiting + all children terminal" and try to enqueue a synthesizer — exactly the race ADR-36 guards against in the happy path.

**In-flight vs DB-only descendants.** Two cases per descendant:
- **In-flight** (`self._runs[id]` exists and `not settled.is_set()`): fire-and-forget signal — set `cancel_event` + cancel `session_handle.session` if non-None. The child's `_run` `CancelledError` branch then writes its own `run_ended` (the path already exercised by `tests/orchestrator/test_loop.py::test_cancel_run`). Do *not* pre-write the DB here — that would race with the child's own finalisation and could double-emit `run_ended`.
- **DB-only** (no in-memory state, or state already settled): reuse the 9a `_cascade_cancel_descendants` helper logic — write `set_run_status(cancelled, ended=True)` + `run_ended` event. This catches queued-but-not-started children, plus the rare case where `_RunState` was lost (a programmer bug worth surviving).

A new helper `_cascade_cancel_runtime(parent_run_id, *, summary)` does the depth-first walk and applies the right strategy per descendant. The 9a `_cascade_cancel_descendants` stays — it's still right for the startup sweep (no in-memory state exists post-restart by definition).

**Fire-and-forget for in-flight children.** `cancel_run` does *not* `await` each descendant's `_run` task. Cancel is a UX signal: the user clicks, the row flips, the in-flight children stop on their own schedule (next loop tick). Waiting would block the route handler on potentially many concurrent harness sessions; the existing `cancel_run` for a single running run uses the same fire-and-forget pattern.

**Watcher race window (closed by the lock).** Without the lock, this sequence could leak:
```
t0: cancel_run reads parent.status = awaiting_children
t1: last child settles, watcher runs, transitions parent → running, enqueues synthesizer
t2: cancel_run writes parent.status = cancelled
t3: supervisor picks up synthesizer ctx, runs synthesizer iter on a now-cancelled parent
```
With both `cancel_run` and `_maybe_resume_parent` inside `async with self._enqueue_lock:`, only one is in the critical section at a time. If the watcher wins, parent is `running` when `cancel_run` re-reads — and `cancel_run` falls through to the existing in-flight-cancel path (sets `cancel_event` on the synthesizer's new `_RunState`). If `cancel_run` wins, the watcher's re-read sees `cancelled` and no-ops.

**Cancel during fanout dispatch.** `_dispatch_children` runs inside `_apply_result` (called from `_run`), which doesn't hold `_enqueue_lock`. A user cancel during dispatch could land between "child rows created" and "child rows enqueued" (the 9c two-pass split). `_cascade_cancel_runtime` queries the DB for descendants and signals all that exist — a queued-but-not-yet-started child has no `_RunState`, so it gets DB-finalised. When the supervisor later picks up the ctx, it creates an `_RunState`, the `_run` starts the loop, the loop checks `cancel_event` (unset because we DB-finalised before the state existed) — but the loop's first action is `open_iter` which doesn't check the cancel event. **Mitigation:** in `_run`, before entering `run_loop`, check the DB status; if `cancelled`, exit immediately. This is a pre-existing latent issue (a user could cancel right after `start_run` returns the queued ctx but before supervisor pickup) — making `_run` defensive about it is a 9d task because 9d makes it provably reachable.

**Tech stack.** No new runtime deps. Reuses `_enqueue_lock`, `_cascade_cancel_descendants` (DB-finalise body), `set_run_status`, `EventStore.append`, `SessionHandle.cancel`.

## File map

| file | action | one-line responsibility |
|---|---|---|
| `src/relay_v2/core.py` | modify | add `_cascade_cancel_runtime`; extend `cancel_run` with the awaiting_children branch (lock-guarded); add a "cancelled-before-start" guard in `_run` |
| `docs/spec.md` | modify | §6 "Cancellation" — add a 9d subsection: cancel cascade semantics, parent-first ordering, fire-and-forget for in-flight descendants |
| `docs/decisions.md` | modify | append ADR-37 (runtime cancel-cascade: parent-first order, in-flight-vs-DB-only split, `_enqueue_lock` reuse) |
| `tests/orchestrator/test_cancel_cascade.py` | create | unit + integration tests for the runtime cascade |

No frontend changes in 9d (the existing dashboard cancel button already calls `POST /api/runs/{id}/cancel`; the 9d behaviour change is server-side only).

## ADR claim

**ADR-37** — next free number (`docs/decisions.md` ends at ADR-36 as of 9c; grep confirms). Records:
- Parent-first cancel order (the watcher race).
- In-flight signal vs DB-only finalise split.
- `_enqueue_lock` reuse for serialisation against `_maybe_resume_parent`.
- Fire-and-forget rationale (UX vs await-each-descendant).
- The `_run` cancelled-before-start guard.

## Open contract questions — none

All design decisions are settled by the 9a/9c context. The cancel-cascade is a natural extension of the watcher's serialisation pattern; no new mechanism needed.

---

## Tasks (TDD-ordered)

---

### Task 1 — Test scaffolding + fixture helpers

**~15 min**

**Files:**
- Create: `tests/orchestrator/test_cancel_cascade.py`

- [ ] **Step 1: Create the file with a seeded-fanout helper**

The 9c `_seed_fanout_state` helper in `test_join_watcher.py` is the right shape but lives under that module. Don't import private test helpers across files — duplicate the small helper here, scoped to the cancel scenarios:

```python
"""Phase 9d — runtime cancel-cascade tests.

Covers the runtime path where ``cancel_run(parent)`` on an
``awaiting_children`` parent must (a) flip the parent to ``cancelled``,
(b) signal every in-flight descendant via the cancel event + session,
(c) DB-finalise any descendant without an in-memory state, (d) do so
under ``_enqueue_lock`` so the join watcher cannot race a resume.

All scripted, no pi.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore, _RunState
from relay_v2.db import init_db
from relay_v2.db.models import Event, Run
from relay_v2.orchestrator.lifecycle import (
    close_iter,
    create_run,
    open_iter,
    set_run_status,
)
from tests.orchestrator.scripted_harness import ScriptedHarness


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
```

- [ ] **Step 2: Commit**

```bash
git add tests/orchestrator/test_cancel_cascade.py
git commit -m "$(cat <<'EOF'
test(cancel_cascade): scaffold + seed helper (9d)

Adds the test module + a _seed_awaiting_parent helper that builds a
realistic awaiting_children parent + N children DB state, optionally
installing in-memory _RunState entries for running children to mirror
what _dispatch_children would have left.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2 — `_cascade_cancel_runtime` helper (in-flight signal)

**~30 min**

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_cancel_cascade.py`

- [ ] **Step 1: Append failing test**

```python
def test_cascade_cancel_runtime_signals_in_flight_children(
    tmp_path: Path,
) -> None:
    """Two running children with in-memory _RunState: cascade sets each
    cancel_event but does NOT pre-write the DB (let the _run task's
    CancelledError branch own that)."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, list[str]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
            )
            await core._cascade_cancel_runtime(
                parent_id, summary="parent cancelled"
            )
            return parent_id, child_ids
        finally:
            await core._engine.dispose()

    parent_id, child_ids = asyncio.run(scenario())
    # The helper has no return; assertions read the post-state.

    # Each in-flight child should have had its cancel_event set.
    # Re-run a quick scenario to inspect _RunState (which is gc'd after
    # engine.dispose); we cannot inspect it post-asyncio.run.
    # Instead, observe the contract: DB state is unchanged for in-flight
    # children (they finalise themselves via _run.CancelledError).
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.status == "running", (
                    f"in-flight child {cid} should NOT be DB-finalised "
                    f"by the cascade; got {child.status}"
                )
                kinds = [
                    e.kind for e in s.scalars(
                        select(Event).where(Event.run_id == cid)
                    )
                ]
                assert "run_ended" not in kinds
    finally:
        engine.dispose()


def test_cascade_cancel_runtime_db_finalises_orphan_children(
    tmp_path: Path,
) -> None:
    """Children with no in-memory state get DB-finalised by the cascade
    (the queued-but-not-started case, plus the lost-_RunState case)."""
    settings = _settings(tmp_path)

    async def scenario() -> tuple[str, list[str]]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
                install_in_memory_state=False,  # DB-only
            )
            await core._cascade_cancel_runtime(
                parent_id, summary="parent cancelled"
            )
            return parent_id, child_ids
        finally:
            await core._engine.dispose()

    parent_id, child_ids = asyncio.run(scenario())
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.status == "cancelled"
                assert child.ended_at is not None
                ended = list(
                    s.scalars(
                        select(Event).where(
                            Event.run_id == cid,
                            Event.kind == "run_ended",
                        )
                    )
                )
                assert len(ended) == 1
                assert ended[0].payload["summary"] == "parent cancelled"
    finally:
        engine.dispose()


def test_cascade_cancel_runtime_signals_in_memory_state(
    tmp_path: Path,
) -> None:
    """White-box: confirm the cancel_event is actually set on each
    in-memory child state."""
    settings = _settings(tmp_path)

    cancel_events_set: list[bool] = []

    async def scenario() -> None:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=3,
            )
            await core._cascade_cancel_runtime(
                parent_id, summary="parent cancelled"
            )
            for cid in child_ids:
                state = core._runs[cid]
                cancel_events_set.append(state.cancel_event.is_set())
        finally:
            await core._engine.dispose()

    asyncio.run(scenario())
    assert cancel_events_set == [True, True, True]
```

- [ ] **Step 2: Verify failure**
```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x -k cascade_cancel_runtime
```
Expect `AttributeError: '_cascade_cancel_runtime'`.

- [ ] **Step 3: Implement** in `src/relay_v2/core.py`, immediately after `_cascade_cancel_descendants` (around line 247):

```python
    async def _cascade_cancel_runtime(
        self, parent_run_id: str, *, summary: str,
        _visited: set[str] | None = None,
    ) -> None:
        """Runtime cancel-cascade: signal in-flight descendants and
        DB-finalise the rest (9d).

        Sibling of :meth:`_cascade_cancel_descendants` (the DB-only
        startup variant, ADR-34) — kept distinct because at runtime we
        must NOT pre-finalise a row whose ``_run`` task is alive (that
        would race the task's own CancelledError finalisation and
        double-emit ``run_ended``). Per-descendant strategy:

        - ``self._runs[id]`` exists and ``not settled.is_set()``: set
          ``cancel_event`` + cancel the harness session. The ``_run``
          task's CancelledError branch owns the DB write.
        - otherwise (no in-memory state, or state already settled):
          write ``set_run_status(cancelled, ended=True)`` + ``run_ended``
          via the same path as :meth:`_cascade_cancel_descendants`.

        Depth-first: a grandchild settles before its parent, so the
        intermediate parent observes a fully-cancelled subtree.
        """
        terminal = ("done", "failed", "cancelled")
        visited = _visited if _visited is not None else set()
        async with self._sm() as s:
            children = list(
                await s.scalars(
                    select(Run).where(Run.parent_run_id == parent_run_id)
                )
            )
        for child in children:
            if child.id in visited:
                continue
            if child.status in terminal:
                continue
            visited.add(child.id)
            # Recurse first so grandchildren settle before the child.
            await self._cascade_cancel_runtime(
                child.id, summary=summary, _visited=visited
            )
            state = self._runs.get(child.id)
            if state is not None and not state.settled.is_set():
                # In-flight: signal and let _run finalise.
                state.cancel_event.set()
                session = state.session_handle.session
                if session is not None:
                    await session.cancel()
            else:
                # DB-only: queued, lost state, or already settled.
                await set_run_status(
                    self._sm, child.id, "cancelled", ended=True
                )
                await self._store.append(
                    child.id,
                    "run_ended",
                    {"status": "cancelled", "summary": summary},
                )
```

- [ ] **Step 4: Verify pass**
```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x -k cascade_cancel_runtime
uv run mypy src/relay_v2/core.py
uv run ruff check src/relay_v2/core.py tests/orchestrator/test_cancel_cascade.py
```
3 tests pass, mypy + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_cancel_cascade.py
git commit -m "$(cat <<'EOF'
feat(core): _cascade_cancel_runtime helper (9d)

Runtime sibling of _cascade_cancel_descendants (the startup DB-only
variant, ADR-34): signals in-flight descendants via cancel_event +
session.cancel and DB-finalises the rest. Distinct from the startup
helper because at runtime we must not pre-finalise a row whose _run
task is alive — that would race the task's CancelledError branch and
double-emit run_ended. Depth-first so grandchildren settle before
their parent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3 — Extend `cancel_run` for the `awaiting_children` branch

**~30 min**

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_cancel_cascade.py`

- [ ] **Step 1: Append failing tests**

```python
def test_cancel_run_on_awaiting_parent_flips_parent_first(
    tmp_path: Path,
) -> None:
    """Order invariant: parent is flipped to cancelled BEFORE the
    cascade signals descendants. Verified by observing the parent's DB
    state is already 'cancelled' when the test code regains control.
    """
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
            )
            await core.cancel_run(parent_id)
            return parent_id
        finally:
            await core._engine.dispose()

    parent_id = asyncio.run(scenario())
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "cancelled"
            assert parent.ended_at is not None
            run_ended = s.scalar(
                select(Event).where(
                    Event.run_id == parent_id,
                    Event.kind == "run_ended",
                )
            )
            assert run_ended is not None
            assert run_ended.payload["status"] == "cancelled"
            assert "user cancel" in run_ended.payload["summary"].lower()
    finally:
        engine.dispose()


def test_cancel_run_on_awaiting_parent_signals_in_flight_children(
    tmp_path: Path,
) -> None:
    """End-to-end: cancel_run on awaiting parent → each in-flight child
    has cancel_event set."""
    settings = _settings(tmp_path)
    cancel_events: list[bool] = []

    async def scenario() -> None:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=3,
            )
            await core.cancel_run(parent_id)
            for cid in child_ids:
                cancel_events.append(core._runs[cid].cancel_event.is_set())
        finally:
            await core._engine.dispose()

    asyncio.run(scenario())
    assert cancel_events == [True, True, True]


def test_cancel_run_on_running_parent_unchanged(tmp_path: Path) -> None:
    """Cancel on a normal running parent (no fanout) still goes through
    the existing cancel_event signal path — no DB pre-write here either."""
    settings = _settings(tmp_path)
    cancel_event_set: list[bool] = []

    async def scenario() -> None:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            project_id = await core.register_project(tmp_path, "p")
            run_id = core._new_run_id()
            await create_run(
                core._sm, run_id=run_id, project_id=project_id,
                prompt_body="x", max_iters=4, iter_timeout=60,
                worktree_path=None, branch=None,
            )
            core._runs[run_id] = _RunState()
            await core.cancel_run(run_id)
            cancel_event_set.append(core._runs[run_id].cancel_event.is_set())
            engine = create_engine(settings.db_url)
            try:
                with Session(engine) as s:
                    run = s.get(Run, run_id)
                    assert run is not None
                    # Status NOT flipped by cancel_run — _run.finally owns it.
                    assert run.status == "running"
            finally:
                engine.dispose()
        finally:
            await core._engine.dispose()

    asyncio.run(scenario())
    assert cancel_event_set == [True]


def test_cancel_run_on_already_cancelled_awaiting_parent_idempotent(
    tmp_path: Path,
) -> None:
    """Cancelling twice is safe — second call sees parent already
    cancelled and returns silently with no duplicate run_ended event."""
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, _ = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
            )
            await core.cancel_run(parent_id)
            await core.cancel_run(parent_id)
            return parent_id
        finally:
            await core._engine.dispose()

    parent_id = asyncio.run(scenario())
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            run_endeds = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "run_ended",
                    )
                )
            )
            assert len(run_endeds) == 1
    finally:
        engine.dispose()


def test_cancel_run_serialises_with_watcher(tmp_path: Path) -> None:
    """Concurrent cancel + watcher: exactly one of them transitions the
    parent. If watcher wins, parent ends 'running' (synthesizer enqueued)
    and cancel_run falls through to the in-flight-cancel branch on the
    new _RunState. If cancel_run wins, watcher sees parent 'cancelled'
    and no-ops. Either way: exactly one run_ended on the parent in the
    end, status terminal."""
    settings = _settings(tmp_path)

    async def scenario() -> str:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
                child_statuses=["done", "done"],  # all settled → watcher eligible
                install_in_memory_state=False,
            )
            # Race cancel against a direct watcher invocation.
            await asyncio.gather(
                core.cancel_run(parent_id),
                core._maybe_resume_parent(parent_id),
            )
            return parent_id
        finally:
            await core._engine.dispose()

    parent_id = asyncio.run(scenario())
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status in ("cancelled", "running"), (
                f"unexpected post-race status: {parent.status}"
            )
            run_endeds = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "run_ended",
                    )
                )
            )
            # If cancel wins: 1 run_ended (cancelled).
            # If watcher wins: 0 run_ended (synthesizer enqueued but no
            # supervisor pickup in this test → run remains in 'running'
            # with no run_ended). Both are acceptable; no duplicate.
            assert len(run_endeds) <= 1
    finally:
        engine.dispose()
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x
```
Expected: the new tests fail (parent not flipped, children's events not signalled). The existing `cancel_run`'s `awaiting_children` path is the no-op that 9d fixes.

- [ ] **Step 3: Implement.** In `src/relay_v2/core.py`, replace `cancel_run` (currently around line 807) with:

```python
    async def cancel_run(self, run_id: str) -> None:
        """Cancel ``run_id``.

        Three branches:

        1. **Awaiting children** (9d): the run has fanned out and has no
           ``_run`` task of its own. Acquire ``_enqueue_lock``, flip
           parent to ``cancelled`` (so the join watcher cannot race a
           resume), then cascade-cancel descendants via
           :meth:`_cascade_cancel_runtime`. Fire-and-forget: in-flight
           descendants finalise themselves via their own
           ``CancelledError`` branch.
        2. **In-flight** (normal case): set ``state.cancel_event`` and
           cancel the harness session. ``_run.finally`` writes the DB.
        3. **No in-memory state + DB row stuck** (orphan, ADR-31 safety
           net): finalise the DB row directly so the user sees a
           visible status flip.
        """
        async with self._enqueue_lock:
            run = await load_run(self._sm, run_id)
            if run is None:
                return
            if run.status == "awaiting_children":
                # Parent first (ordering invariant — see ADR-37 + the
                # watcher race comment in _maybe_resume_parent).
                await set_run_status(
                    self._sm, run_id, "cancelled", ended=True
                )
                await self._store.append(
                    run_id,
                    "run_ended",
                    {"status": "cancelled", "summary": "user cancelled"},
                )
                await self._cascade_cancel_runtime(
                    run_id,
                    summary="parent cancelled by user",
                )
                return
            if run.status in ("done", "failed", "cancelled"):
                # Already terminal — idempotent no-op.
                return

        # Outside the lock: the existing in-flight signal path. Holding
        # the lock here would deadlock the loop (the loop's own writes
        # don't take this lock, but session.cancel() may await pi I/O).
        state = self._runs.get(run_id)
        if state is None:
            # ADR-31 safety net.
            await set_run_status(
                self._sm, run_id, "cancelled", ended=True
            )
            await self._store.append(
                run_id,
                "run_ended",
                {"status": "cancelled",
                 "summary": "orphaned: process state lost"},
            )
            return
        state.cancel_event.set()
        session = state.session_handle.session
        if session is not None:
            await session.cancel()
```

(Note: the existing `cancel_run` reads `state` before checking the DB. The new structure reads the DB first inside the lock — this is fine for the in-flight path because `load_run` is a single read and the in-memory state lookup is unchanged.)

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x
uv run pytest tests/orchestrator/test_loop.py::test_cancel_run -x
uv run pytest tests/orchestrator/ 2>&1 | tail -1
uv run mypy src/relay_v2/core.py
uv run ruff check src/relay_v2/core.py
```
All clean. The existing `test_loop.py::test_cancel_run` must still pass — that's the in-flight branch.

Stability check:
```bash
for i in 1 2 3; do uv run pytest tests/orchestrator/ 2>&1 | tail -1; done
```

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_cancel_cascade.py
git commit -m "$(cat <<'EOF'
feat(core): cancel_run cascades through awaiting_children (9d)

cancel_run on an awaiting_children parent now: acquires _enqueue_lock,
flips parent to cancelled FIRST (parent-first ordering — the watcher's
re-read inside the same lock then sees cancelled and no-ops), then
cascades via _cascade_cancel_runtime to signal in-flight descendants +
DB-finalise queued/orphaned ones. The existing in-flight cancel path
(set cancel_event + cancel session) is preserved for normal running
runs; the orphan DB safety net (ADR-31) is preserved for no-in-memory-
state cases.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4 — `_run` cancelled-before-start guard

**~15 min**

A queued-but-not-started run whose DB row was flipped to `cancelled` by `_cascade_cancel_runtime` (the orphan branch) will still be picked up by the supervisor. Without a guard, the loop starts, opens an iter, and the DB ends up with a `cancelled` run that has a fresh `iter_started` event from after the cancel — confusing and arguably a contract violation.

**Files:**
- Modify: `src/relay_v2/core.py`
- Test: `tests/orchestrator/test_cancel_cascade.py`

- [ ] **Step 1: Append failing test**

```python
def test_cancelled_before_start_no_iter(tmp_path: Path) -> None:
    """A run pre-flipped to cancelled (DB-only cascade case) that the
    supervisor picks up must exit immediately without opening an iter."""
    settings = _settings(tmp_path)

    async def scenario() -> str:
        # Use a fresh-context harness that would happily emit DONE if
        # the loop ran — if the guard is broken, we'd see an iter.
        from tests.orchestrator.scripted_harness import TextScript
        harness = ScriptedHarness([TextScript("ok\n\n[[engteam:done]]")])
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            pid = await core.register_project(tmp_path, "p")
            run_id = await core.start_run(pid, "Go.")
            # Race-flip the DB to cancelled before the supervisor picks
            # it up. (Single-process MVP: the supervisor task and the
            # test code share the event loop; the supervisor is at
            # ``await self._queue.get()``; flipping the DB here is safe.)
            await set_run_status(core._sm, run_id, "cancelled", ended=True)
            await core._store.append(
                run_id, "run_ended",
                {"status": "cancelled", "summary": "pre-start cancel"},
            )
            # Now let the supervisor proceed and pick up the queued ctx.
            await core.wait_for_run(run_id)
            return run_id
        finally:
            await core.aclose()

    run_id = asyncio.run(scenario())
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            run = s.get(Run, run_id)
            assert run is not None
            assert run.status == "cancelled"
            # No iter rows — the loop never started.
            from relay_v2.db.models import Iter
            iters = list(
                s.scalars(select(Iter).where(Iter.run_id == run_id))
            )
            assert iters == [], (
                f"unexpected iter(s) after pre-start cancel: {iters}"
            )
            # No duplicate run_ended.
            run_endeds = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == run_id,
                        Event.kind == "run_ended",
                    )
                )
            )
            assert len(run_endeds) == 1
    finally:
        engine.dispose()
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py::test_cancelled_before_start_no_iter -x
```
Expected: the assertion `iters == []` fails (the loop ran and emitted one iter).

- [ ] **Step 3: Implement guard.** In `src/relay_v2/core.py`, near the top of `_run` (currently around line 399, immediately after `state = self._runs[ctx.run_id]` and before the `with self._otel.run_span(...)` block), add:

```python
        # 9d guard: a row pre-flipped to cancelled (cascade DB-only
        # branch) that the supervisor picks up should not enter the
        # loop. _RunState's settled.set() is still required so any
        # caller awaiting wait_for_run() does not hang.
        run_row = await load_run(self._sm, ctx.run_id)
        if run_row is not None and run_row.status in (
            "done", "failed", "cancelled"
        ):
            state.result = LoopResult(
                run_row.status,
                reason="cancelled_before_start",
            )
            state.settled.set()
            return
```

(`load_run` is already imported at the top of core.py.)

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x
uv run pytest tests/orchestrator/ 2>&1 | tail -1
uv run mypy src/relay_v2/core.py
```

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_cancel_cascade.py
git commit -m "$(cat <<'EOF'
feat(core): _run guards against cancelled-before-start (9d)

When _cascade_cancel_runtime DB-finalises a queued descendant before
the supervisor has created its _run task, the row exits in 'cancelled'
state. Without this guard, the supervisor still picks up the queued
ctx and runs the loop, producing a stray iter_started on an already-
terminal run. The guard checks the DB status on entry and short-
circuits if the run is already terminal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5 — Deep tree cascade integration test

**~25 min**

**Files:**
- Test: `tests/orchestrator/test_cancel_cascade.py`

Verifies cascading through grandchildren — the depth-first traversal of `_cascade_cancel_runtime`.

- [ ] **Step 1: Append test**

```python
def test_cancel_run_cascades_through_grandchildren(tmp_path: Path) -> None:
    """parent → child A (awaiting_children) → 2 grandchildren (running)
    + child B (running). Cancelling the root cascades all four
    descendants depth-first."""
    settings = _settings(tmp_path)
    cancel_events: dict[str, bool] = {}

    async def scenario() -> tuple[str, str, str, list[str], str]:
        core = RelayCore(settings, harness=ScriptedHarness([]))
        init_db(settings).dispose()
        try:
            # Root parent
            parent_id, child_ids = await _seed_awaiting_parent(
                core, tmp_path, n_children=2,
                child_statuses=["awaiting_children", "running"],
                install_in_memory_state=False,  # child A is awaiting, not running
            )
            child_a, child_b = child_ids
            # Promote child B to in-memory (running with a _RunState).
            core._runs[child_b] = _RunState()
            # Spawn 2 grandchildren under child A.
            project_id = (await core.list_projects())[0].id
            grandchild_ids: list[str] = []
            for i in range(2):
                gid = core._new_run_id()
                await create_run(
                    core._sm, run_id=gid, project_id=project_id,
                    prompt_body=f"g{i}", max_iters=4, iter_timeout=60,
                    worktree_path=str(tmp_path / f"gwt-{i}"),
                    branch=f"relay/{gid}", parent_run_id=child_a,
                )
                await core._store.append(
                    gid, "run_started",
                    {"project_id": project_id, "prompt_body": f"g{i}",
                     "max_iters": 4},
                )
                core._runs[gid] = _RunState()
                grandchild_ids.append(gid)

            await core.cancel_run(parent_id)
            cancel_events[child_b] = core._runs[child_b].cancel_event.is_set()
            for gid in grandchild_ids:
                cancel_events[gid] = core._runs[gid].cancel_event.is_set()
            return parent_id, child_a, child_b, grandchild_ids, project_id
        finally:
            await core._engine.dispose()

    parent_id, child_a, child_b, grandchild_ids, _ = asyncio.run(scenario())

    # In-flight: child B + 2 grandchildren had cancel_event set.
    assert cancel_events[child_b] is True
    for gid in grandchild_ids:
        assert cancel_events[gid] is True, (
            f"grandchild {gid} cancel_event not set"
        )

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None and parent.status == "cancelled"
            # Child A had no in-memory state → DB-finalised.
            ca = s.get(Run, child_a)
            assert ca is not None and ca.status == "cancelled"
            # Child B was in-flight → not DB-finalised by cascade
            # (would be by its own _run.CancelledError in a real run).
            cb = s.get(Run, child_b)
            assert cb is not None and cb.status == "running"
            # Grandchildren were in-flight → not DB-finalised by cascade.
            for gid in grandchild_ids:
                g = s.get(Run, gid)
                assert g is not None and g.status == "running"
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -x
git add tests/orchestrator/test_cancel_cascade.py
git commit -m "$(cat <<'EOF'
test(cancel_cascade): deep-tree grandchildren cascade (9d)

parent → child A (awaiting_children) → 2 grandchildren + child B (in-
flight). Cancelling root cascades depth-first: child A and any DB-only
descendants get DB-finalised; in-flight grandchildren and child B get
cancel_event set (their _run.CancelledError owns the DB write).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6 — Spec update §6 (Cancellation)

**~15 min**

**Files:**
- Modify: `docs/spec.md`

- [ ] **Step 1: Find the cancellation paragraph**

```bash
grep -n "Cancellation\." docs/spec.md
```

There is one in `### 6.1 Runtime model`: "**Cancellation.** `cancel_run` sets a per-run flag and cancels the …". Insert the 9d note immediately after that paragraph (or extend it).

- [ ] **Step 2: Add cascade subsection**

Add a paragraph (or new subsection) describing:

- Three cancel branches in `cancel_run`: awaiting_children (cascade), in-flight (signal cancel_event + session), orphan (DB safety net).
- Parent-first ordering inside `_enqueue_lock` — references ADR-37.
- Fire-and-forget for in-flight descendants — their own `_run.CancelledError` writes the run_ended.
- `_run`'s cancelled-before-start guard so a DB-flipped queued run does not run.

Keep it terse — ~10 lines of prose; the ADR carries the full rationale.

- [ ] **Step 3: Commit**

```bash
git add docs/spec.md
git commit -m "$(cat <<'EOF'
docs(spec): runtime cancel-cascade semantics (9d)

§6.1 — cancel_run now has three branches (awaiting_children cascade,
in-flight signal, orphan DB safety net), the parent-first ordering
under _enqueue_lock prevents the join watcher from racing a resume
mid-cancel, and _run's cancelled-before-start guard catches the
DB-finalised-queued case.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7 — ADR-37

**~20 min**

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Confirm next ADR number**

```bash
grep -n "^## ADR-" docs/decisions.md | tail -3
```
Expected: ADR-36 last. If a parallel ADR landed concurrently, bump.

- [ ] **Step 2: Append ADR-37**

Structure (mirror ADR-36):

```markdown
## ADR-37 — Runtime cancel-cascade: parent-first under `_enqueue_lock`

**Status:** accepted (YYYY-MM-DD)
**Phase:** 9d (runtime cancel-cascade)

**Context.** 9c landed the join watcher (`_maybe_resume_parent`) that
transitions an `awaiting_children` parent → `running` when all
children settle. 9d closes the symmetric gap: when the *user* cancels
such a parent, the cancel must propagate to descendants and the
watcher must not race the cancel.

**Decision — parent-first.** `cancel_run` acquires `_enqueue_lock`,
flips the parent to `cancelled` first, then walks descendants via
`_cascade_cancel_runtime`. The watcher acquires the same lock and
re-reads the parent; finding `cancelled`, it no-ops. With the reverse
order (descendants first, then parent), a child terminal landing
between the cascade and the parent flip would let the watcher resume
the parent — exactly what we are trying to cancel.

**Decision — in-flight vs DB-only split.** Per descendant:
- in-flight (`self._runs[id]` exists and not settled): signal
  `cancel_event` + `session.cancel()`. The child's `_run.CancelledError`
  branch writes its own `run_ended`. Do NOT pre-write the DB here —
  that would double-emit.
- otherwise: write `set_run_status(cancelled, ended=True)` + `run_ended`
  directly (same body as the 9a startup helper).

The 9a `_cascade_cancel_descendants` (DB-only) stays — at startup
there are no in-memory states by definition.

**Decision — `_run` cancelled-before-start guard.** A queued
descendant pre-flipped to `cancelled` by the cascade must not run when
the supervisor picks it up. `_run` checks the DB status on entry and
returns immediately if terminal.

**Rejected — await-each-descendant.** Blocking `cancel_run` on every
in-flight child finishing would make a Cancel button on a 10-child
fanout feel broken. Fire-and-forget matches the existing single-run
cancel semantics.

**Rejected — cancel from inside the watcher.** Having the watcher
detect "user cancelled mid-resume" and back out is more complex than
serialising both via `_enqueue_lock`. The lock already exists for
exactly this kind of "look-decide-mutate" race.

**Consequences.**
- An `await cancel_run(parent)` returns quickly; descendants finalise
  asynchronously. Tests that need full quiescence use
  `await wait_for_run(descendant_id)`.
- A new test pattern (`test_cancel_run_serialises_with_watcher`)
  documents the safe race outcomes: either the cancel wins (parent
  cancelled, no run_ended duplication) or the watcher wins (parent
  back to running, cancel falls through to the in-flight branch
  against the synthesizer's new `_RunState`).

**Related:** ADR-12 (single-process MVP), ADR-31/32 (run finalisation
+ orphan safety nets), ADR-34 (startup cascade helper, 9a), ADR-36
(join watcher, 9c — the same `_enqueue_lock` serialiser).
```

- [ ] **Step 3: Commit**

```bash
git add docs/decisions.md
git commit -m "$(cat <<'EOF'
docs(adr): ADR-37 runtime cancel-cascade (9d)

Records parent-first ordering inside _enqueue_lock (avoids watcher
race), in-flight-vs-DB-only descendant split (avoids run_ended
duplication), and _run's cancelled-before-start guard (avoids stray
iter rows on pre-flipped runs).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8 — Full gate + CLAUDE.md update + PR

**~25 min**

- [ ] **Step 1: Full gate**

```bash
uv run pytest
uv run ruff check .
uv run mypy
cd frontend && npm run check
```
Expected: ~265–268 backend tests passing (256 baseline + 7–10 new from 9d). ruff + mypy clean (still 39 source files; 9d adds no modules). Frontend unchanged.

Stability check:
```bash
for i in 1 2 3; do uv run pytest tests/orchestrator/ 2>&1 | tail -1; done
```

- [ ] **Step 2: Update CLAUDE.md**

Append a `**Phase 9d** then …` paragraph to the "Current state" section, mirroring the 9c paragraph's density and citing ADR-37. Update the test count.

- [ ] **Step 3: Commit + push + PR**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE.md): record Phase 9d under Current state

Phase 9d paragraph appended: cancel_run cascades through
awaiting_children parents (ADR-37), parent-first ordering inside
_enqueue_lock prevents the watcher race, in-flight descendants get
signal-only (their own _run.CancelledError finalises), DB-only
descendants get cascade DB-finalise, _run guards against cancelled-
before-start. 9e/9f noted as still open follow-ups.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin phase-9d-cancel-cascade
gh pr create --title "Phase 9d: runtime cancel-cascade (ADR-37)" --body "..."
```

Match the 9c PR description shape (summary, changes, test plan checklist).

---

## Verification commands (post-task summary)

```bash
uv run pytest tests/orchestrator/test_cancel_cascade.py -v
uv run pytest tests/orchestrator/test_loop.py::test_cancel_run -v
uv run pytest tests/orchestrator/  # full orchestrator regression
uv run pytest                      # full backend
uv run ruff check . && uv run mypy
cd frontend && npm run check       # no changes expected
```

---

## Out of scope (deferred)

- **REST/MCP surface.** `POST /api/runs/{id}/cancel` and the MCP
  `relay__cancel_run` tool already call `RelayCore.cancel_run` — they
  inherit the new behaviour automatically. No API change.
- **Dashboard "Children" pane (9e).** The cascaded `run_ended` events
  stream through SSE; the existing timeline shows them. A parent-side
  Children panel with cancel-cascade UI is 9e's job.
- **OTel span parenting (9f).** Cancel events are already mirrored as
  span attributes; cross-run parenting is 9f.
- **Worktree cleanup on cancel.** The orchestrator never auto-cleans
  worktrees (proposal §tradeoffs). Cancelled children leave their
  worktrees in place for forensics. Out of V1 scope.

---

## Risks and what could go wrong

- **`session.cancel()` may block.** `SessionHandle.cancel` is async
  and may await pi I/O. `_cascade_cancel_runtime` awaits it per
  descendant — sequentially. With 10 children, that's 10 sequential
  cancels. For V1 this is fine (single-user, low fanout fan-out). If
  it bites, parallelise with `asyncio.gather` inside the helper —
  contract-preserving change.
- **Watcher race with a different child.** If child A's settle fires
  the watcher and child B is still running, the watcher sees A's terminal
  + B's running → no-op. Then the user cancels. `cancel_run` flips
  parent, cascades B. Fine. The only race ADR-37 closes is "all
  children just settled" + concurrent cancel, which the lock handles.
- **Test counts drift.** The plan assumes 8 new tests; if a subagent
  finds a needed extra test (e.g., the watcher-race acceptance window),
  add it and note in the commit message. Don't trim tests to hit a
  count.
- **`_run` guard vs `_run`'s otel span.** The guard returns before
  the `with self._otel.run_span(...)` block. A cancelled-before-start
  run produces NO `relay.run` span. That's defensible — the run never
  ran. If 9f's mirror logic needs a span for cancelled runs, address
  there.

---

## Effort estimate

- Task 1 (scaffold + helper): 15 min.
- Task 2 (_cascade_cancel_runtime + tests): 30 min.
- Task 3 (extend cancel_run + tests): 30 min.
- Task 4 (cancelled-before-start guard + test): 15 min.
- Task 5 (deep-tree test): 25 min.
- Task 6 (spec update): 15 min.
- Task 7 (ADR-37): 20 min.
- Task 8 (gate + CLAUDE.md + PR): 25 min.

Total ~3 hours focused. One commit per task = 8 commits, all in one PR.
