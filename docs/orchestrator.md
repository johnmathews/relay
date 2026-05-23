# Orchestrator layer

> Phase 2 deliverable. Implementation reference for `src/relay_v2/core.py`,
> `src/relay_v2/events.py`, and `src/relay_v2/orchestrator/`. Canonical
> design is `spec.md` §6 (loop) + §3 (data model); ADR-07, ADR-10,
> ADR-19, ADR-20, ADR-21 carry the rationale. When this doc disagrees
> with `spec.md`, `spec.md` wins.

## What it is

The orchestrator runs the chained-iter loop: a fresh harness session per
iter, signal detection at turn boundaries, a compressed handoff carrying
state to the next iter, and clean termination on `done` / `pause` /
no-signal / `max_iters`. It consumes only the normalized
harness/signaling surface — it contains no pi knowledge (ADR-04).

```
src/relay_v2/
├── core.py                 RelayCore — single shared service + runtime
├── events.py               EventStore — append-only event-log writer
└── orchestrator/
    ├── loop.py             run_loop() (spec.md §6) + _drive_iter
    ├── lifecycle.py        run/iter row mutations, workspace, resume compose
    └── preamble.py         RELAY_RUN_DIR / RELAY_PHASE preamble builder
```

## RelayCore (ADR-07, ADR-15, ADR-19)

The one in-process object every write flows through. Phase 2 surface:

- `register_project(root_path, name) -> int` — idempotent.
- `start_run(project_id, prompt_body, *, max_iters?, iter_timeout?) -> run_id`
  — provisions the workspace, creates the run row, emits `run_started`,
  enqueues the run.
- `cancel_run(run_id)` — flag + cancel the in-flight session.
- `resume_run(run_id, answer)` — recompose + re-enqueue a paused run.
- `store_event`, `list_runs`, `get_run` — service reads/writes.
- `wait_for_run(run_id) -> LoopResult` — deterministic await point for
  tests/automation (not a route).
- `start()` / `aclose()` — lifecycle, driven by FastAPI's `lifespan`.

**Runtime.** `start()` ensures the schema (idempotent `create_all`,
ADR-17) and spawns a supervisor task. The supervisor drains an
`asyncio.Queue` of `RunContext`s, launching one tracked child task per
run. `aclose()` cancels the supervisor then every run task, swallowing
`CancelledError` and run exceptions so shutdown can't stall. This is the
open-ended-server form of plan.md's "TaskGroup in lifespan" (ADR-19).

## The loop (spec.md §6 — canonical)

`run_loop` mirrors the spec pseudocode one-to-one; production concerns
the spec elides live in `_drive_iter` so the loop stays readable:

- **Fresh context per iter.** `last_session_id` (→ `resume_from`) is
  always `None` between iters. Pi resume is crash-recovery only
  (CLAUDE.md invariant).
- **Turn-boundary detection.** The pi mapper flushes exactly one
  `AssistantText(kind="text")` per turn at `turn_end`, so detection runs
  on whole-turn text — never on streaming deltas (spec.md §6 risk note).
  A defensive guard ignores a spec-violating second flush of a turn.
- **Terminal vs non-closing.** `done` / `handoff` / `pause` close the
  iter; `phase_start` / `unit_*` are recorded as `signal_emit` events
  and the iter continues. The terminal signal wins if both are present.
- **Phase carry-forward.** The *last* `phase-start` of the iter (via
  `extract_phase_start`) sets the next iter's `RELAY_PHASE` and is
  written to `$RELAY_RUN_DIR/phase` — independent of which signal
  closed the iter. When a terminal signal shared the turn with the
  `phase-start` (so `detect_in_text` returned the terminal and never
  emitted the phase_start), the carry-forward path appends the
  `signal_emit{kind:phase_start}` itself, exactly once — the Phase 4
  timeline/replay always sees the transition.
- **No usable signal.** Clean `agent_end` with no column-0 closing
  sentinel (a fenced/indented one never matches) *and* a `MarkerError`
  both → `exit_reason="agent_end_no_signal"`, run fails; this stays
  within spec.md §3.1's `iters.exit_reason` set (the marker headline is
  kept in `signal_args` + the `run_ended` summary).
- **Caps.** `max_iters` (`while seq < max_iters`) and `iter_timeout`
  (orchestrator-enforced wall clock) both fail the run cleanly.
- **Internal error (ADR-31).** Any non-`CancelledError` exception out of
  `run_loop` or `_apply_result` is finalised as `failed` with a
  `run_ended` payload `{"status": "failed", "summary": "internal_error:
  <exc>"}` and `LoopResult.reason == "internal_error"`. The exception
  is also logged via `logger.exception`. Without this a spawn-time
  error (bad project root, missing pi binary) would leave the run
  permanently `running` with no closing event.

`run_loop` returns a `LoopResult(status, reason, summary, question,
next_prompt, pause_id)`; `RelayCore` maps it to the run's final status +
the closing run-level event.

## EventStore (ADR-10)

Append-only writer over the `events` table — the single source of truth.
Every observable action is one row; status transitions are **new
events**, never in-place rewrites. The mutable projection columns
(`runs.status`, `iters.*`) are a convenience view updated *in step with*
the matching event, so the log alone reconstructs a run.

- One `asyncio.Lock` serialises seq-assignment + insert → strictly
  monotonic per-run `seq` even across concurrent runs (also serialises
  SQLite's single writer).
- Tool-result truncation lives here, not in the harness (plan.md Phase 1
  follow-up): `tool_use_end.result` over `TOOL_RESULT_CAP` (16 KiB) is
  replaced with a bounded preview + size marker.
- `ToolUseUpdate` and `SessionStarted`/`SessionEnded` are not persisted
  here — the loop records iter lifecycle as `iter_*` events; spec.md
  §3.2 has no kind for tool-update partials.

Event kinds emitted (spec.md §3.2): `run_started`, `iter_started`,
`assistant_text`, `tool_use_start`, `tool_use_end`, `signal_emit`,
`iter_ended`, `pause_requested`, `pause_resolved`, `run_ended`,
`harness_session_ended` (9g — close-time `SessionEnded` mirror, ADR-39),
`artifact_edited` (14a — paused-iter artifact write, ADR-40),
`subagent_dispatch` / `subagent_return` / `child_runs_resolved` (9a–9c —
fanout-join, ADR-34/35/36).

## Pause / resume (ADR-20)

`pause` persists `{next_prompt, question, id}` in the pausing iter's
`iters.signal_args` (no new column) + a `pause_requested` event;
`runs.status=paused` (not ended). `resume_run` recomposes the body as
`next_prompt` + a delimited answer block, flips status to `running`,
emits `pause_resolved`, restores phase from `$RELAY_RUN_DIR/phase`, and
re-enqueues at the next `seq`. A lock + liveness guard prevents a
duplicate resume from spawning two loops for one run.

## Workspace (ADR-13, spec.md §3.3)

`start_run` always creates `RELAY_RUN_DIR`
(`<project_root>/.relay/runs/<run_id>/`, sibling of the worktree). A
per-run git worktree (`<project_root>/.relay/worktrees/<run_id>/`,
branch `relay/<run_id>`) is provisioned best-effort; when the project
root is not a git work tree it degrades to running in the project
root with `worktree_path=NULL`. The loop's `cwd` is `worktree_path or
project_root` either way.

> **Path note (post-9g bug-fix sweep, 2026-05-23).** The worktree
> moved from `<data_dir>/worktrees/<run_id>/` (relay-global) to
> `<project_root>/.relay/worktrees/<run_id>/` (per-project), matching
> spec §3.3. `provision_workspace` no longer takes a `data_dir`
> argument. The single resolver `RelayCore.get_run_artifacts_dir
> (run_id)` is the one place routes / MCP tools call for the artifacts
> root. `data_dir` now holds only the multi-tenant `relay.db`.

## Fanout-join (9a–9f, ADR-34/35/36/37/38)

Fanout dispatch + the synthesizer join landed post-MVP as the 9a–9f
arc. The orchestrator-level moving parts:

- **Status `awaiting_children`** (9a) — not terminal; a paused parent
  awaiting child runs. `runs.parent_run_id` ties children to parent.
- **`RelayCore._dispatch_children`** (9b) — spawns N child runs whose
  worktrees branch off the parent's worktree HEAD (via
  `provision_workspace(..., parent_worktree_path=…)`). The dispatch
  pass is two-step: create ALL child rows + `subagent_dispatch` events
  first, THEN enqueue (so a fast scripted harness can't let child A
  finish before B's row exists — the join watcher's "all terminal?"
  check would short-circuit on the partial set). Concurrency capped
  by `asyncio.Semaphore(max_fanout_concurrent)`; depth bounded by
  `max_fanout_depth` (ADR-35).
- **`RelayCore._maybe_resume_parent`** (9c, ADR-36) — fired from each
  child's `_run` finally block under `_enqueue_lock`. When every
  sibling reaches terminal, emits one `subagent_return` per child +
  one `child_runs_resolved`, transitions parent
  `awaiting_children → running`, re-enqueues with a synthesizer
  `RunContext` whose body is `compose_join_prompt(join_prompt,
  child_results)`. The synthesizer iter runs on the parent's
  existing worktree (no new worktree for the join). The watcher
  fires BEFORE `state.settled.set()` so a caller awaiting a child's
  `wait_for_run()` then immediately the parent's cannot race.
- **`RelayCore._cascade_cancel_runtime`** (9d, ADR-37) — runtime
  cancel-cascade. `cancel_run` on an `awaiting_children` parent flips
  the parent to `cancelled` *first* (parent-first ordering — required
  to close the watcher race), then cascades depth-first to
  descendants. In-flight descendants get a fire-and-forget signal
  (`cancel_event.set()` + `session.cancel()`); DB-only descendants
  get `set_run_status(cancelled, ended=True)` + `run_ended` directly.
- **Orphan recovery** (`_recover_orphans` + ADR-31 / 32 / 34) —
  startup sweep: cascade `awaiting_children` parents first (with
  descendants), then finalise any leftover `running` rows from a
  prior process. Single-user / single-process MVP means a `running`
  row at startup must come from a dead prior process and can never
  resume. ADR-34 carry-over: recovering an in-flight fanout across
  a restart is a deliberate V1 non-goal.

## Pause-for-review write endpoint (14a, ADR-40)

`RelayCore.write_artifact(run_id, rel_path, content, *, editor)` is
the **single write entry point** on the run artifacts dir. Coupled
to `runs.status == 'paused'` AND set-membership of the requested
path in the paused iter's `signal_args.review_paths` (14f / ADR-41;
legacy scalar `review_path` is read as a one-element list during
the migration window). Failures map to `PauseReviewError` codes
(`unknown_run` / `not_paused` / `no_review_path` / `path_mismatch` /
`too_large` / `binary` / `missing_parent_dir`); REST adapter
translates each to the right HTTP status. On success: an atomic
write (tempfile-in-same-dir + `Path.replace`) and one
`artifact_edited` event (iter-scoped to the paused iter) with pre/
post sha256 + size + editor tag. Content lives on disk per ADR-25;
the event carries hashes for integrity, not the bytes (ADR-40 §B1).

## Testing

- `tests/orchestrator/test_loop.py` drives the real `RelayCore` +
  `run_loop` against `scripted_harness.py` (a `Harness` double, no pi,
  fully offline). It covers every plan.md Phase 2 criterion plus
  `max_iters`, timeout, and cancel. Pi e2e stays gated behind
  `PI_INTEGRATION=1` (harness suite).
- Tests live under `tests/orchestrator/` (not the
  `src/.../orchestrator/tests/` path plan.md sketched) to match the
  established Phase 0/1 `testpaths=["tests"]` convention.

Run: `uv run pytest`; gates `uv run ruff check .` and `uv run mypy`
(strict) must stay clean.
