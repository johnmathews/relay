# 2026-05-19 — Phase 2: orchestrator

Built Phase 2 per `docs/plan.md`: `RelayCore` (single shared service +
queue/supervisor runtime), the append-only `EventStore`, the chained-iter
`run_loop` (spec.md §6), run lifecycle (start/cancel/pause/resume), and
the `RELAY_*` preamble builder. Done in an isolated worktree
(`phase-2-orchestrator`), merged to `main`.

## What landed

- `src/relay_v2/core.py` — `RelayCore`. The one object every write flows
  through (ADR-07/ADR-15). Owns an `asyncio.Queue` + a supervisor task
  that launches one tracked child task per run; `start()`/`aclose()`
  bracket it and are driven by the app `lifespan`.
- `src/relay_v2/events.py` — `EventStore`. Append-only writer; one
  `asyncio.Lock` makes per-run `seq` strictly monotonic and serialises
  SQLite's single writer. Tool-result truncation lives here (the Phase 1
  follow-up), capped at 16 KiB.
- `src/relay_v2/orchestrator/{loop,lifecycle,preamble}.py` — the spec §6
  loop kept readable, with timeout/cancel/MarkerError/phase-carry
  isolated in `_drive_iter`.
- Async DB engine added to `relay_v2.db` (ADR-21); app `lifespan` now
  owns `RelayCore`. Phase 0's `app.state.engine` (sync) is untouched so
  the existing smoke test still passes.

## Decisions (ADR-19, ADR-20, ADR-21)

**ADR-19 — orchestrator runtime.** plan.md said "`asyncio.TaskGroup` in
lifespan". A literal `async with TaskGroup()` can't stay open while
accepting new runs for a daemon's lifetime, so the structure is a
long-lived supervisor draining a queue + a tracked task set — the
open-ended-server equivalent with the same shutdown guarantees.

**ADR-20 — pause/resume persistence.** No new column: the paused
`{next_prompt, question, id}` is stored in the pausing iter's
`iters.signal_args` (the JSON column §3.1 already reserves). `resume_run`
recomposes the body as `next_prompt` + a delimited answer block and
re-enqueues at the next `seq`. Fresh-context-per-iter holds — the answer
travels in the prompt, never via pi resume.

**ADR-21 — async engine.** Executes ADR-17's anticipated consequence;
recorded only because it adds deps (`aiosqlite`, `sqlalchemy[asyncio]` →
`greenlet`). Sync engine survives for `create_all` bootstrap only.

spec.md §6 gained §6.1 (runtime model) + §6.2 (pause/resume); the
canonical loop pseudocode is unchanged. decisions.md is append-only —
ADR-19/20/21 appended, nothing edited.

## Deviation from plan.md (intentional)

plan.md sketched tests at `src/relay_v2/orchestrator/tests/`. The
established Phase 0/1 convention is `testpaths=["tests"]`, so orchestrator
tests live at `tests/orchestrator/` (mirroring `tests/harness/`) — they
would not be collected under the sketched path. CLAUDE.md toolchain
section updated to record this.

`agent_end_no_signal` taxonomy: both the fenced/indented-sentinel case
*and* a `MarkerError` (real closing sentinel, broken marker pair) are
classified `exit_reason="agent_end_no_signal"` rather than inventing a
new reason — keeps `iters.exit_reason` within spec.md §3.1's set; the
marker headline is preserved in `signal_args` + the `run_ended` summary.

## Code review

An independent reviewer pass found a real subprocess-leak blocker
(`_drive_iter` cleanup not in `finally` → pi zombie on shutdown
cancellation), a `wait_for_run` hang if `_apply_result` raised, an
`aclose()` stall on task exceptions, a missing `queue.task_done()`, a
non-atomic resume check-and-enqueue, and a missing `max_iters` test.
All fixed; #9 (drop sync `app.state.engine`) was declined — the Phase 0
smoke test reads it and changing it is out of Phase 2 scope.

## Verification

- `uv run pytest` → **69 passed, 1 skipped**. The skip is the gated
  `PI_INTEGRATION=1` harness e2e; pi is never spawned by the suite.
  The Phase 1 harness suite stayed green.
- Every plan.md Phase 2 criterion covered in `tests/orchestrator/
  test_loop.py`: phase-start+handoff → iter 2 with carried `RELAY_PHASE`;
  `done` → status=done; `pause` → status=paused + persisted next-prompt,
  `resume_run(answer)` re-spawns with the composed prompt; fenced/indented
  sentinel with no real closing → `agent_end_no_signal`, fails cleanly.
  Plus `max_iters`, iter-timeout, external cancel, list/get.
- `uv run ruff check .` clean; `uv run mypy` (strict) clean — 21 files.

## Follow-ups (out of Phase 2 scope)

- Real-pi `PI_INTEGRATION=1` orchestrator e2e against pi v0.74.0 before
  Phase 3 builds REST on top.
- Phase 3 wires REST routes/SSE over `RelayCore` (no new write paths —
  routes call existing service methods).
- `docs/orchestrator.md` added per the global docs policy.
