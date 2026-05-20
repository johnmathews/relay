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
`iter_ended`, `pause_requested`, `pause_resolved`, `run_ended`.

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
(`<data_dir>/runs/<run_id>/`, sibling of the worktree). A per-run git
worktree (`<data_dir>/worktrees/<run_id>/`, branch `relay/<run_id>`) is
provisioned best-effort; when the project root is not a git work tree it
degrades to running in the project root with `worktree_path=NULL`. The
loop's `cwd` is `worktree_path or project_root` either way.

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
