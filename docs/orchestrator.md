# Orchestrator layer

> Phase 2 deliverable. Implementation reference for `src/relay/core.py`,
> `src/relay/events.py`, and `src/relay/orchestrator/`. Canonical
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
src/relay/
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
- `store_event`, `list_runs`, `get_run`, `list_children` — service
  reads/writes. `list_runs(..., include_children=False)` hides child
  rows from top-level listings by default (9e); `list_children(run_id)`
  returns direct children ordered by `started_at`.
- `preview_run(project_id, prompt_body, *, max_iters?, iter_timeout?)`
  — no-side-effect renderer of the prompt + preamble that *would* be
  sent (used by the dashboard's New-Run wizard).
- `write_artifact(run_id, rel_path, content, *, editor)` — the single
  write entry point on the run artifacts dir (see "Pause-for-review
  write endpoint" below). Sibling resolver
  `get_run_artifacts_dir(run_id)` is the one place routes / MCP tools
  call for the artifacts root.
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
next_prompt, pause_id, fanout_payload, fanout_parent_ctx)`;
`RelayCore._apply_result` maps it to the run's final status + the
closing run-level event — or, when `status == "awaiting_children"`,
routes to `_dispatch_children` (no `run_ended` for the parent; the
join watcher resumes it later).
`fanout_payload` carries the parsed `[[engteam:fanout]]` JSON marker
block (children + join_prompt); `fanout_parent_ctx: IterSpanContext`
is the live OTel span context of the closing fanout iter, threaded
into each child's run span for cross-run trace parenting (9f, ADR-38).

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
- `ToolUseUpdate` and `SessionStarted` are not persisted here — the
  loop records iter lifecycle as `iter_*` events; spec.md §3.2 has no
  kind for tool-update partials. `SessionEnded` is also dropped by the
  `EventStore` harness-event mapper, but the loop's `_finish_iter`
  writes a dedicated `harness_session_ended` event (9g, ADR-39) BEFORE
  the paired `iter_ended` on every close path — so replay consumers
  still see the close-time `{stop_reason, messages, summary}` payload.

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

## Fanout-join (9a–9g, ADR-34/35/36/37/38/39)

Fanout dispatch + the synthesizer join landed post-MVP as the 9a–9g
arc (9g adds the `harness_session_ended` close-time persistence in
the same close-path code touched by the fanout lifecycle). The
orchestrator-level moving parts:

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
  startup sweep: cascade `awaiting_children` parents first (the
  sibling helper `_cascade_cancel_descendants` is the startup-only
  variant; `_cascade_cancel_runtime` is the runtime equivalent —
  both walk descendants depth-first, the runtime variant has the
  fire-and-forget in-flight branch), then finalise any leftover
  `running` rows from a prior process (pass 2 catches non-fanout
  orphans; descendants of awaiting parents are already finalised
  by pass 1's cascade). Single-user / single-process MVP means a
  `running` row at startup must come from a dead prior process and
  can never resume. ADR-34 carry-over: recovering an in-flight
  fanout across a restart is a deliberate V1 non-goal.

## Chat mode (ADR-49)

Chat mode is a parallel run mode that turns relay's runtime into a
conversational webui for pi. The same `runs` row + `events` table +
SSE stream + worktree provisioner, with three mode-conditional
branches in the loop and zero new tables.

- **`runs.mode`** — `'task' | 'chat'`, default `'task'`. Constrained
  at the Python boundary (Pydantic `Literal["task", "chat"]`); the
  schema column is `mode TEXT NOT NULL DEFAULT 'task'`.
- **`RelayCore.start_run(mode='chat', ...)`** — direct-writes
  `run_started` + a synthetic `pause_requested` event and **settles
  without spawning a first iter**. The run is "ready to chat" the
  moment it's created. The first `resume_run` answer becomes iter 1's
  prompt body — that's why no first iter is needed up front.
- **`RelayCore.resume_run(run_id, answer)`** — branches on
  `run.mode`. Chat-mode resumes thread the prior iter's
  `pi_session_id` as pi's `--session` argument so each iter inherits
  the model's prior conversation memory. The operator's `answer` is
  the **verbatim** next-iter body — no preamble assembly, no
  compressed handoff. Task mode is unchanged (ADR-20: fresh context +
  recomposed `next_prompt` + delimited answer block).
- **`run_loop`** — branches on `ctx.mode`. Chat-mode iters skip the
  `RELAY_*` preamble (chat has no `RELAY_RUN_DIR` or `RELAY_PHASE`),
  skip sentinel enforcement (no `done` / `handoff` /
  `pause-for-input` parsing — pi's `agent_end` is the turn boundary),
  and on `session_end` write a synthetic `pause_requested` so the run
  lands in `paused` waiting for the operator's next message.
- **Skill injection (ADR-44) is omitted from chat-mode spawns.** The
  bundled engineering-team skill is a phased-build harness, wrong for
  free-form conversation. Pi's own auto-discovery of
  `<cwd>/.pi/skills/` and `~/.pi/agent/skills/` is preserved, so
  project-local skills carry over.
- **Voluntary end via `POST /api/runs/{id}/close`** — flips status to
  `closed` (ADR-50, a terminal status distinct from `done` /
  `cancelled` / `failed`). Reachable from `paused` (the natural
  resting state between turns) and `running` (mid-turn — the close
  mutation cancels the in-flight session first).
- **All other infra is shared.** The event store is unchanged; chat
  emits no new event kinds. SSE replay, OTel `relay.run` /
  `relay.iter` spans, the ADR-46 streaming-delta pipeline
  (`assistant_delta` ephemeral frames), the ADR-45 heartbeat — every
  cross-cutting addition for the timeline view applies to chat mode
  unchanged.

The frontend folds the same event stream into an alternating
user/assistant transcript (`ChatView.vue`); see `docs/dashboard.md`
for the rendering contract. The "Promote to task" affordance in the
chat header navigates to the New Run wizard with the chat transcript
prefilled into `prompt_body` (W6, via sessionStorage handoff);
promotion is non-destructive — the chat stays alive.

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
- Post-MVP coverage lives in the same dir: `test_cancel_cascade.py`,
  `test_fanout_{dispatch,integration,loop,join_integration}.py`,
  `test_join_watcher.py`, `test_lifecycle{,_join,_child_worktree}.py`,
  `test_project_data_dir.py`, `test_relay_core.py`,
  `test_sentinels_fanout.py`.
- Tests live under `tests/orchestrator/` (not the
  `src/.../orchestrator/tests/` path plan.md sketched) to match the
  established Phase 0/1 `testpaths=["tests"]` convention.

Run: `uv run pytest`; gates `uv run ruff check .` and `uv run mypy`
(strict) must stay clean.
