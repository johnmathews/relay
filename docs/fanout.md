# Fanout-join — operator runbook

> Phase 9a–9f deliverable. Operator-facing how-to for parallel-iter
> fanout/join — what it looks like in production, how to trigger one,
> how to cancel one, what happens on restart, and how to debug.
> **Canonical design** is `docs/spec.md` §5.4 (grammar) + §6 (lifecycle);
> ADR-34 / ADR-35 / ADR-36 / ADR-37 / ADR-38 carry the rationale.
> Implementation reference: `docs/orchestrator.md` §"Fanout-join".
> When this doc disagrees with `spec.md`, `spec.md` wins.

## What it is

Fanout-join is the orchestrator-level mechanism for parallel work in
relay-v2. The agent declares N child runs via a closing sentinel; the
orchestrator spawns each as its own run row with its own worktree,
the parent waits in a non-terminal `awaiting_children` status, and
when every child settles the orchestrator runs **one more iter on
the parent** — the synthesizer iter — with a join-prompt body that
includes per-child results.

The data shape is **separate runs joined by `parent_run_id`** (Shape
B in the proposal), NOT iters of the parent run. Each child has its
own event stream, worktree, and lifecycle; the parent's stream only
sees `subagent_dispatch` / `subagent_return` / `child_runs_resolved`
events plus the synthesizer iter.

## When to use it

- **Audits / surveys** where N independent passes over the same code
  produce richer signal than one pass (frontend audit + backend
  audit + dependency audit → synthesizer reconciles).
- **Independent fixes** where N small problems can each be tackled
  by a fresh-context session without one's solution polluting
  another (each child branches from the parent's worktree HEAD —
  see "Workspace semantics").
- **Speculative exploration** where you want N alternative plans
  scored against one another, then the synthesizer picks.

It is **not** the right mechanism for sequential work (use the
`handoff` sentinel) or for breaking a long single conversation into
phases (the existing `phase-start` mechanism).

## Lifecycle

```
                    parent's pre-fanout iters
                              │
                              ▼
                ┌──────── fanout iter ────────┐
                │  agent emits the sentinel   │
                │  parent → awaiting_children │
                └─────────┬───────────────────┘
                          │
                  ┌───────┼───────┐ (N children dispatched)
                  ▼       ▼       ▼
              child A  child B  child C    each runs its own
                  │       │       │         full iter chain
                  └───────┼───────┘
                          │
                  all children terminal
                          │
                          ▼
              ┌─ join watcher fires (under _enqueue_lock) ──┐
              │  • emit subagent_return × N                 │
              │  • emit child_runs_resolved                 │
              │  • parent: awaiting_children → running      │
              │  • enqueue synthesizer iter on parent's     │
              │    existing worktree                        │
              └─────────┬───────────────────────────────────┘
                        │
                        ▼
            synthesizer iter (parent's run)
                        │
                        ▼
              parent reaches terminal (or fans out again)
```

Phases in detail:

1. **Pre-fanout.** Parent run executes normal iters via the
   `handoff` / phase-start sentinels.
2. **Fanout iter.** Agent emits the `[[engteam:fanout]]` closing
   verb (paired with a `[[engteam:fanout-start]] … [[engteam:fanout-end]]`
   JSON marker block — see "Sentinel grammar" below). The
   orchestrator parses the payload, flips the parent's status from
   `running` to `awaiting_children`, and calls `_dispatch_children`.
3. **Dispatch (two-pass, ADR-36).** `_dispatch_children` creates
   ALL child rows + their `subagent_dispatch` events FIRST, then
   enqueues them in a second pass. This guarantees the join watcher
   always sees the full child set — a fast (scripted) harness must
   not let child A finish before B's row exists.
4. **Children run.** Each child runs as its own top-level run with
   `parent_run_id` set, its own worktree (branched from the parent's
   worktree HEAD — see "Workspace semantics"), and its own iter
   chain. Children are concurrent up to `max_fanout_concurrent` (an
   `asyncio.Semaphore` in the supervisor — ADR-35 Option A).
5. **Join (ADR-36).** When a child's `_run` task settles, its
   finally block calls `_maybe_resume_parent(parent_run_id)`
   BEFORE setting `state.settled` (so a caller awaiting the child's
   `wait_for_run()` then immediately the parent's cannot race the
   watcher's swap of `_runs[parent_id]`). The watcher acquires
   `_enqueue_lock`, checks "all siblings terminal?"; if yes, emits
   `subagent_return × N` + `child_runs_resolved`, transitions
   parent `awaiting_children → running`, and re-enqueues with a
   synthesizer `RunContext`.
6. **Synthesizer iter.** Runs on the parent's existing worktree
   (not a fresh sibling — the join sees the parent's pre-fanout
   state). Body = `compose_join_prompt(join_prompt, child_results)`:
   the `join_prompt` from the closing fanout iter, followed by a
   `---` separator and a `RELAY_CHILD_RESULTS:` YAML-ish trailer
   listing each child's `id` / `role` / `status` / `summary` /
   `branch` / `worktree_path`. The synthesizer may emit another
   fanout (recursive, up to `max_fanout_depth`).

## Sentinel grammar (spec §5.4)

```
[[engteam:fanout-start]]
{
  "children": [
    {"role": "frontend-audit", "prompt": "Audit the Vue dashboard..."},
    {"role": "backend-audit",  "prompt": "Audit the FastAPI service..."}
  ],
  "join_prompt": "Reconcile the two audits. Produce one prioritised list."
}
[[engteam:fanout-end]]

[[engteam:fanout]]
```

Rules (enforced by `extract_fanout_payload` and `FanoutPayload`):

- The JSON block must validate against `FanoutPayload`:
  `children: [{role, prompt}]` (≥ 1 child; `role` + `prompt` both
  required, both strings) + `join_prompt` (required, string).
- The `[[engteam:fanout]]` closing verb must follow the
  `[[engteam:fanout-end]]` marker (intervening blank lines allowed)
  at column 0 with no indentation.
- Malformed JSON, missing markers, or a `join_prompt`-less payload
  is treated as `agent_end_no_signal` → the run fails.
- The line-anchored markers mean a sentinel wrapped across lines
  silently drops. Keep the verb on its own line.

Example failure mode and repair:

```
[[engteam:fanout]]
[[engteam:fanout-start]] ... [[engteam:fanout-end]]
```

This fails — the verb must come AFTER the marker block, not before.
The parser searches backward from the closing verb for the marker
pair; an inverted order leaves the verb with no matching block.

## Dashboard

`docs/dashboard.md` §"Fanout-join dashboard additions" covers the
component-level behaviour. Operator-facing pieces:

- **Parent run detail.** A `ChildrenPane` renders below the timeline
  when `parent_run_id == null` AND ≥ 1 child run exists. Each row:
  `status · short-id · role · branch · summary`. Click → child's
  run detail view.
- **Child run detail.** A `ParentRunChip` in the header links back
  to the parent's run detail.
- **Cascade-aware Cancel.** While `parent_run_id == null` and
  `status ∈ {running, awaiting_children}`, the Cancel button is
  enabled. For an `awaiting_children` parent the label reads
  "Cancel run and N children" (N from the children query).
- **"Show child runs" toggle.** The Project Runs pane hides child
  runs by default (`?include_children=false`). Toggle on to surface
  them in the top-level list.

MCP callers see the full tree regardless: `relay__list_runs` always
passes `include_children=True` so a programmatic consumer never
misses a child run.

## Cancellation

`POST /api/runs/{id}/cancel` (or `relay__cancel_run`) is the single
entry point. Three branches (`RelayCore.cancel_run`):

1. **Parent in `awaiting_children`** — acquire `_enqueue_lock`, flip
   parent to `cancelled` FIRST (parent-first ordering, ADR-37), then
   call `_cascade_cancel_runtime` which walks descendants depth-first.
   For each descendant:
   - **In-flight** (`_RunState` exists, not settled): fire-and-forget
     signal (`cancel_event.set()` + `session.cancel()`); the
     descendant's own `_run.CancelledError` branch finalises its DB
     row and emits `run_ended`. We do NOT pre-write the DB row here
     — that would double-emit.
   - **DB-only** (no in-memory state, or already settled): write
     `set_run_status(cancelled, ended=True)` + `run_ended` directly,
     mirroring the 9a startup helper.
2. **Normal in-flight run** — set `state.cancel_event` + cancel the
   harness session; `_run.finally` writes the closing event. This
   path is preserved verbatim from pre-fanout.
3. **No in-memory state + DB row stuck** — ADR-31 safety net:
   finalise the DB row directly so the user sees a visible status
   flip.

Why parent-first? The 9c join watcher also acquires `_enqueue_lock`
and re-reads the parent's status under it. A child terminal landing
between a descendants-first cascade and the parent flip would let
the watcher resume the parent mid-cancel — exactly what we're
cancelling. Flipping the parent first closes that race.

## Restart behaviour

`_recover_orphans` runs at startup (ADR-34). Two passes:

1. **`awaiting_children` parents.** Cascade-cancel descendants
   first (via the 9a startup helper, DB-only writes), then finalise
   the parent. The cancelled summary is `"orphaned: server restart"`.
2. **`running` rows.** Any remaining row in `running` at startup
   must come from a dead prior process (single-user/-process MVP —
   ADR-12, ADR-32). Mark each `cancelled` + `run_ended`.

The order matters: cascade-from-awaiting-parents first, then sweep
the remaining `running` rows. A child of an awaiting parent is
itself `running` and would be matched by both passes — the cascade
gives it the more-specific "parent interrupted during fanout"
summary; the second pass skips already-finalised rows.

**Recovering an in-flight fanout across a restart is a deliberate
V1 non-goal** (ADR-34). Once the parent's in-memory join watcher is
gone, the children would have no consumer for their
`subagent_return` events and the parent would never reach its
synthesizer iter. Tearing the whole subtree down is the only
honest behaviour.

## OTel trace tree (9f, ADR-38)

When `RELAY_OTEL_EXPORT=langfuse`, a fanned-out workflow surfaces as
**one connected trace tree**:

```
relay.run (parent, pre-fanout)
└── relay.iter (pre-fanout iters)
    └── relay.iter (the fanout iter — exit_reason=signal)
        ├── relay.run (child A)
        │   └── relay.iter ... (child A's iters)
        ├── relay.run (child B)
        │   └── relay.iter ... (child B's iters)
        └── relay.run (parent, synth phase)
            └── relay.iter (synthesizer iter)
```

Mechanism: the loop captures the live iter span's `Context` on the
fanout iter and returns it on `LoopResult.fanout_parent_ctx`.
`_dispatch_children` stashes that context on each child's
`_RunState.parent_iter_ctx` in the first pass (create-all-rows);
`_run` passes it into `otel.run_span(..., parent_iter_ctx=…)` which
opens the run span under that context. The synthesizer-phase span
preserves the context across the `_RunState` overwrite at parent
resume — so it parents under the SAME iter as the children (sibling
of the children, NOT a sibling of the parent's pre-fanout iters).

Cross-run trace context lives in-memory only (threaded via
`LoopResult` → `_RunState`, never persisted). A restart loses the
linkage — acceptable under ADR-34.

Acceptance: see `docs/observability.md` §"Trace tree across fanout"
for the live-Langfuse-UI procedure (gated like `PI_INTEGRATION=1`,
journal-attested per ADR-30).

## Limits

| Env var | Default | Hard cap | What it bounds |
|---|---|---|---|
| `RELAY_MAX_FANOUT_CONCURRENT` | 4 | none | In-flight child runs at any moment (`asyncio.Semaphore` in the supervisor). Queued children wait for a slot; a slot is released when a child's `_run` task completes (regardless of outcome). |
| `RELAY_MAX_FANOUT_DEPTH` | 2 | 4 | Maximum depth of the `parent_run_id` chain. A child that would exceed the cap is finalised as `failed` immediately at dispatch time. |

The depth cap is bounded by a hard ceiling (4) in the settings so a
misconfigured env var can't blow up the system. Depth is measured
from the root: a top-level run is depth 0, its direct children are
depth 1, grandchildren are depth 2, etc. With the default of 2, the
deepest valid fanout is grandchildren — great-grandchildren are
rejected.

## Workspace semantics

Each child's worktree is provisioned via
`provision_workspace(project_root, child_run_id, parent_worktree_path=…)`.
The new worktree branches off the **parent's worktree HEAD**, not
the project's default branch. This means:

- A child sees the parent's pre-fanout commits (so audit children
  see the same code the parent was looking at).
- Children are isolated from each other: child A's commits never
  reach child B's worktree.
- The synthesizer iter runs on the parent's existing worktree (no
  new worktree for the join — the synthesizer is supposed to see
  the parent's pre-fanout state, not a sibling's edits).

Branch naming follows the same `relay/<run_id>` pattern as top-level
runs (ADR-13). Worktrees live under
`<project_root>/.relay/worktrees/<run_id>/` (per-project, per-run
— spec §3.3, corrected in the post-9g bug-fix sweep).

## How to drive a test fanout (without real pi)

The scripted harness (`tests/orchestrator/scripted_harness.py`) is
the offline driver. To exercise a fanout end-to-end:

```python
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_BLOCK = """\
Plan complete.

[[engteam:fanout-start]]
{"children": [
  {"role": "audit-fe", "prompt": "Audit the frontend."},
  {"role": "audit-be", "prompt": "Audit the backend."}
 ],
 "join_prompt": "Reconcile the two audits."}
[[engteam:fanout-end]]

[[engteam:fanout]]
"""

CHILD_DONE = "Audit complete.\n\n[[engteam:done]]"
SYNTH_DONE = "Synthesized.\n\n[[engteam:done]]"

# Three pi sessions: parent's fanout iter, both children's done iters,
# then parent's synthesizer iter.
harness = ScriptedHarness([
    TextScript(FANOUT_BLOCK),   # parent fanout iter
    TextScript(CHILD_DONE),     # child A
    TextScript(CHILD_DONE),     # child B
    TextScript(SYNTH_DONE),     # parent synth iter
])
```

The scripted harness is fully synchronous and deterministic; child
runs settle in the order their scripts return. `wait_for_run` on
the parent blocks until the synthesizer settles. See
`tests/orchestrator/test_fanout_integration.py` and
`tests/orchestrator/test_lifecycle_join.py` for full patterns.

## Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Parent stuck in `awaiting_children` after children settled | Watcher didn't fire — typically because of a process crash between child terminal and the watcher call | Restart the server; `_recover_orphans` will cancel the parent + cascade. The lost work cannot be recovered (ADR-34 V1 non-goal). |
| Children queue but never start | `max_fanout_concurrent` is set too low, OR an earlier sibling is wedged holding a semaphore slot | Check `RELAY_MAX_FANOUT_CONCURRENT`; look for a stuck child's iter timeout. |
| Synthesizer iter never runs after all children terminal | `_maybe_resume_parent` couldn't recover the `join_prompt` from the closing fanout iter's `signal_args["payload"]` | Check the orchestrator logs for `fanout-join: parent ... has empty join_prompt` — a malformed payload was dispatched. |
| Dashboard shows `awaiting_children` but the Children pane is empty | Children list query hasn't refreshed; or the children rows haven't been created yet (race in the dispatch first-pass) | Refresh the page; check `GET /api/runs/{id}/children` directly. The dispatch first-pass should always create every row before enqueueing — if rows are missing, file a bug. |
| Cancel button cancels the parent but a child keeps running | `_cascade_cancel_runtime` failed to signal an in-flight descendant | Check logs for an exception in the cascade. The startup orphan-recovery sweep is a backstop on next restart. |
| OTel trace shows children as disconnected roots in Langfuse | `parent_iter_ctx` was not threaded — possibly an older code path, or `RELAY_OTEL_EXPORT=none` mid-run | Confirm `RELAY_OTEL_EXPORT=langfuse` was set when the parent's fanout iter STARTED (not just when children spawned). Restart-loss of cross-run trace context is by design (ADR-34). |
| Child depth-exceeded error in logs | Recursive fanout hit `RELAY_MAX_FANOUT_DEPTH` | Raise the cap (max hard cap 4) or restructure the workflow to flatten one level. |

## Related docs

- **Design contract:** `docs/spec.md` §5.4 (sentinel grammar), §6
  (lifecycle).
- **Implementation reference:** `docs/orchestrator.md`
  §"Fanout-join", `docs/harness.md` §"Signaling" (fanout extractor).
- **Trace tree:** `docs/observability.md` §"Trace tree across
  fanout".
- **Dashboard:** `docs/dashboard.md` §"Fanout-join dashboard
  additions".
- **ADRs:** ADR-34 (schema + S1 convention + V1 non-goal),
  ADR-35 (semaphore concurrency cap), ADR-36 (join watcher
  placement + dispatch two-pass), ADR-37 (parent-first cancel
  cascade), ADR-38 (OTel cross-run span parenting).
- **Original proposal:** `docs/proposals/parallel-iters-fanout-join.md`.
