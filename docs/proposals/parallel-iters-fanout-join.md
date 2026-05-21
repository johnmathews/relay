# Proposal — parallel iters via fanout/join (orchestrator-layer subagents)

**Status:** proposal (not yet ADR'd, not yet implemented)
**Date:** 2026-05-21
**Touches:** `docs/spec.md` §3, §4.2, §6; `src/relay_v2/orchestrator/` (loop,
preamble, lifecycle/workspace), `src/relay_v2/db/` (schema), `src/relay_v2/
api/` (run-tree views), `src/relay_v2/sse.py` (interleaved streams),
`frontend/` (timeline tree view), `skills/engineering-team/pi/` (skill-side
fanout guidance), `tests/orchestrator/`, `docs/orchestrator.md`.
**Does not touch:** pi harness (no protocol change), MCP tool surface
(additive at most), `harness/` package internals (ADR-04 preserved).

## Background

Pi has **no subagents at the protocol level** (`scratch/pi_derisk_workdir/
findings.md`, ADR-06). A single pi session cannot dispatch a child to
explore-in-parallel and return a summary the way Claude Code's `Task`
tool can. Relay compensates with a different model: each iter gets a
**fresh pi process** (ADR-04) carrying a compressed handoff from the
prior iter — sequential subagent dispatch, implemented at the
orchestrator layer.

This works well for chained engineering work (the engineering-team
skill's four-phase evaluate → plan → develop → wrap-up cycle). It does
not cover parallel work — "fan out three explorers, merge findings",
"run two candidate refactors and pick the better one", "build five
review lenses concurrently". The current loop processes one iter at a
time per run.

There is **no fundamental reason** relay can't run parallel pi
sessions. Pi sessions are independent OS subprocesses. ADR-12's
"single-process" invariant is about the **relay server** (one
process owning the SQLite WAL, one supervisor), not about the number
of pi children it spawns. What's missing is **orchestration
semantics**: how a parent iter dispatches children, how children get
isolated workspaces, how their results merge back, how the event store
records the tree, how the dashboard renders it.

## State of the world (today)

Relevant invariants and existing affordances:

- **Loop:** `orchestrator/loop.py` runs one iter at a time per run.
  `RelayCore` owns a supervisor task; concurrency exists *between
  runs*, not within.
- **Workspace:** `lifecycle.py:provision_workspace` creates one git
  worktree per run on branch `relay/<run_id>` (ADR-13). Run = worktree
  is 1:1.
- **Schema (`docs/spec.md` §3.1):**
  - `runs.parent_run_id TEXT REFERENCES runs(id)` — **already
    reserved** ("for subagent runs"). Nullable, never populated today.
  - `iters` has `seq INTEGER` (monotonic per run), no `parent_iter_seq`,
    no `parent_run_id`.
  - `events.iter_id` is nullable (run-level events allowed).
- **Event taxonomy (`spec.md` §3.2):** `subagent_dispatch` and
  `subagent_return` event kinds are **already reserved** with payload
  shapes — never emitted today.
- **Spec text:** `spec.md` §6 includes a "Subagent dispatch" paragraph
  describing the parent_run_id model as a future strategy "out of
  scope for MVP".
- **Skill:** `skills/engineering-team/` is single-session; ADR-28
  notes "subagent parallelism is a post-MVP relay feature (a
  `subagent_dispatch` signal the orchestrator does not yet handle)".

So the design has been planning for this since spec.md was written.
The data model has the FKs reserved, the event taxonomy has the kinds
reserved, and the spec narrates the future. **This proposal is the
"now implement it" companion** to that latent design, with the
specific shape — fanout via sentinel, children as iters of a child
run, join via post-completion synthesizer iter — filled in.

## The two viable shapes

A real decision lives here. Both have been considered:

### Shape A — Children are **iters of the parent run**

A `[[engteam:fanout]]` sentinel causes the orchestrator to start N
*concurrent* iters within the *same* run. Each child iter gets a unique
`parent_iter_seq` (pointing at the dispatching iter) and a per-child
worktree branched off `relay/<run_id>` (e.g. `relay/<run_id>/child-a`,
`/child-b`). When all children close (each emits its own terminal
sentinel), the orchestrator starts a synthesizer iter on the parent
branch whose preamble enumerates child summaries + diff paths.

**Pros:**
- Single run-id, single timeline; the tree lives inside one run.
- Simpler URL space (`/runs/<id>` shows everything).
- Cancellation: cancelling the run cancels all children naturally.
- Event-store SSE stays one stream per run.

**Cons:**
- `iters.seq` must lose its "monotonic-sequential" intuition (children
  share a parent_iter_seq and have ordering only within their lane).
  This is a real conceptual change to the iter abstraction.
- Need a new column `iters.parent_iter_seq INTEGER` (nullable).
- Worktree multiplication within one run — the `worktrees/<run_id>/`
  directory now contains a tree of branches, not a single workspace.

### Shape B — Children are **separate runs** (use the existing `parent_run_id`)

A `[[engteam:fanout]]` sentinel causes the orchestrator to start N
*new runs*, each with `parent_run_id = <parent>`. Each child run is
ordinary in every other respect — its own iters, its own worktree, its
own lifecycle, its own SSE stream. When all children reach a terminal
status, the parent run's loop resumes with a synthesizer iter whose
preamble enumerates child run-ids + summaries + diff paths.

**Pros:**
- The schema already supports it (`runs.parent_run_id`,
  `subagent_dispatch`/`subagent_return` events). Zero schema change.
- Children are first-class runs — cancellable individually, browsable
  in the run list, replayable independently.
- `iters.seq` keeps its monotonic-sequential intuition for any single
  run. No new column.
- Maps cleanly onto the spec.md §6 "subagent dispatch" paragraph
  verbatim — no spec rework, only spec *implementation*.

**Cons:**
- The dashboard "all my runs" list gets noisier (children appear as
  peer runs). Mitigated by filtering on `parent_run_id IS NULL` by
  default, with a "show children" toggle.
- Cancellation must cascade (cancel parent → cancel all `parent_run_id
  = parent` runs). Easy but explicit.
- Two SSE streams to watch if the dashboard wants a unified timeline
  (mitigated by a server-side aggregator if/when needed, additive).

**Recommendation: Shape B.** It is what the schema and spec were
already designed for; it requires zero schema change; it preserves the
iter abstraction; and child runs being first-class is a *feature*
(individual replay, individual cancel, individual artifact dirs) that
costs nothing extra. Shape A's "one run, one timeline" looks tidier
on a whiteboard but trades a clean abstraction (iters are sequential)
for a presentational nicety.

The rest of this proposal assumes Shape B.

## Proposal (Shape B)

### Sentinel grammar

Two new engteam verbs (spec.md §12 / `skills/engineering-team/pi/
references/sentinels.md`):

```
[[engteam:fanout]]
{
  "children": [
    { "role": "explorer-frontend", "prompt": "audit frontend/src/router for ..." },
    { "role": "explorer-backend",  "prompt": "audit src/relay_v2/api for ..." }
  ],
  "join_prompt": "synthesize the two audits and propose a unified fix list"
}
```

When parsed by the harness sentinels strategy, the orchestrator:

1. Closes the dispatching iter (`exit_reason="signal"`,
   `signal_kind="fanout"`).
2. Appends a `subagent_dispatch` event for each child
   (`{child_run_id, role, prompt}`).
3. Starts N child runs:
   - `parent_run_id = <parent>`
   - `project_id = parent.project_id`
   - `prompt_body = child.prompt`
   - own `run_id`, own worktree on branch `relay/<child_run_id>`
     **branched off the parent worktree HEAD**, not off the project
     default branch (so each child starts from the parent's current
     in-progress work).
4. Marks the parent run as `awaiting_children` (a new status, or a
   reuse of `paused` with a payload — see "open questions" below).

Each child run is otherwise ordinary: its own loop, its own iters, its
own sentinels, its own terminal status. A child can itself fanout
(recursive), constrained by a configurable `max_fanout_depth` (default
2, hard cap 4 — to keep the worst-case process count and disk usage
finite).

When all children reach a terminal status, the orchestrator:

1. Appends a `subagent_return` event per child
   (`{child_run_id, status, result}` where `result` is the child's
   last `iter_ended` payload + worktree diff path).
2. Resumes the parent run with a synthesizer iter whose preamble adds:
   ```
   RELAY_CHILD_RESULTS:
     - id: <child_run_id_a>
       role: explorer-frontend
       status: done
       summary: <...>
       worktree_diff: .relay/runs/<parent>/children/<child_a>/diff.patch
     - id: <child_run_id_b>
       ...
   ```
   and whose **user prompt** is the `join_prompt` from the fanout
   payload.

### Schema changes

**Minimal — none required.** All needed columns and event kinds are
already in spec.md §3.1 / §3.2:

- `runs.parent_run_id` — already there.
- `events.kind in ('subagent_dispatch', 'subagent_return')` — already
  there.

Two additions, both small and additive:

- A new `runs.status` value: `awaiting_children`. (Or reuse `paused`
  with a discriminator in the SSE payload — see "open questions".)
- A new event kind: `child_runs_resolved` (run-level event marking
  the moment the parent transitioned from `awaiting_children` →
  `running` after all children completed). Optional — derivable from
  the last `subagent_return` — but useful for replay diff'ing.

### Loop changes

`orchestrator/loop.py`:

- On `signal_kind=="fanout"`: stop the parent loop, schedule child
  runs through `RelayCore.start_run` (each child is just a normal
  run), record the child IDs against the parent (in-memory map +
  event store), mark parent `awaiting_children`.
- A new `RelayCore` background watcher: when a child run's
  `run_ended` event lands, check whether all siblings have ended; if
  so, append `subagent_return`s + `child_runs_resolved`, then resume
  the parent.
- Resume mechanism: piggyback on the existing pause/resume
  infrastructure (ADR-20) — the parent is in a paused-equivalent
  state with a structured payload describing what it's waiting for.

`orchestrator/preamble.py`:

- Extend the preamble builder to include `RELAY_CHILD_RESULTS` when
  the iter is a post-fanout join iter.

`orchestrator/lifecycle.py:provision_workspace`:

- When `parent_run_id` is set, branch off the parent worktree HEAD
  (`git worktree add -b relay/<child_id> <new_path> relay/<parent_id>`).
- Add a `merge_child_into_parent` helper (not auto-called — the
  join-iter agent decides whether to merge child branches into the
  parent worktree; the orchestrator only **provisions**).

### Cancellation semantics

- Cancel parent → cancel all `parent_run_id == parent` runs
  recursively (depth-first, children's children too).
- Cancel a single child → siblings continue; parent stays
  `awaiting_children` until they all resolve; cancelled child counts
  as resolved with `status="cancelled"`.

### Dashboard

- Run list: default filter `parent_run_id IS NULL`; "show children"
  toggle reveals the tree.
- Run detail: a "Children" pane appears on parent runs when any
  fanout happened, listing child run-ids with status pills and click-
  through. Implemented as a small additive component — the existing
  RunDetail panes stay unchanged.
- Timeline: out of scope for v1 of this feature. Each run has its own
  SSE stream; the user navigates between them. An aggregated timeline
  is a Phase-10-ish enhancement.

### Skill-side guidance

`skills/engineering-team/pi/` gets a new `references/fanout.md` and a
section in `phases/phase-1-evaluation.md` (and possibly
`phase-3-development.md`) explaining when fanout is appropriate
("two or more genuinely independent investigations whose results
merge cleanly into a single decision"). The skill emits the sentinel
when it judges the work parallelizable; relay handles the rest.

## What stays unchanged (load-bearing invariants)

These cannot break, and this proposal preserves all of them:

- **Fresh context per iter (`spec.md` §6).** Each child iter, and the
  join iter, is a fresh pi process with a compressed handoff
  (RELAY_CHILD_RESULTS *is* the handoff). The orchestrator's value
  proposition is intact.
- **Event store as single source of truth (ADR-10).** Every parent ↔
  child transition is one or more events. SSE / OTel / replay all
  derive from the event store; no in-memory shortcut.
- **Harness isolation (ADR-04).** Pi sees nothing new. No new
  HarnessEvent type. Fanout is a sentinel — pure text — and the
  harness layer is unchanged.
- **All writes through `RelayCore` (ADR-07/15).** Child-run creation,
  parent-run state transitions, all events: all through `RelayCore`.
- **Single-process server (ADR-12).** One relay process; many pi
  child processes. No change.

## Tradeoffs and risks

- **Process count.** N parallel pi sessions = N Max-subscription
  concurrent uses. Pi's auth is per-subscription; running 8 at once
  may hit rate limits. Mitigation: `max_fanout_concurrent` config
  (default 4), with overflow queued. This is a real operational
  consideration, not a blocker.
- **Disk usage.** Each child worktree is a full git checkout of the
  project. For large repos (multi-GB), 5 parallel children = 5×
  disk. Mitigation: document the cost; consider `git worktree add
  --no-checkout` + sparse checkout later if it bites.
- **Merge conflicts.** Children edit child branches; the join iter
  must decide what to merge. Initial version: orchestrator
  **never** auto-merges. The join iter has shell access and decides
  per-conflict. This is honest about the complexity rather than
  pretending the orchestrator can be cleverer than it is.
- **Cancellation correctness.** Cascade-cancel must be transactional
  with respect to the event store. Test coverage is essential —
  scripted-harness tests for the cascade are tractable.
- **Replay complexity.** Replaying a parent run requires replaying
  its children (or at least their summaries). Mitigation: child
  runs replay independently via existing machinery; parent replay
  treats `subagent_return` events as the boundary (the summary the
  parent saw at the time, not a re-execution).
- **Observability.** OTel spans need a parent-child relationship
  spanning runs. The existing `relay.run` span model handles this
  naturally — children get spans parented to the dispatching iter's
  span, even across run-id boundaries (OTel doesn't care about
  run-id; it cares about trace context).

## Open questions to resolve before implementation

1. **`awaiting_children` vs reused `paused`.** New status is cleaner
   but touches every status-handling code path (frontend, MCP tool
   schemas, etc.). Reusing `paused` with a structured `pause_reason`
   is less code but conceptually muddled. Lean toward new status,
   confirm via small spike.
2. **Synthesizer iter: same run, or child of children?** The current
   text proposes "resume the parent run with a join iter". An
   alternative is "the join iter is itself a child run with
   `parent_run_id` and a special role". The first is simpler and
   matches Shape B's parent-resumes-after model; flagging the
   alternative for completeness.
3. **Recursion bound.** `max_fanout_depth` default and cap. Proposed
   default 2, cap 4. Confirm against realistic skill workflows.
4. **What does child run's `prompt_body` look like in the runs list?**
   Probably the parent's prompt + role + child-intent excerpt. Needs
   a small UX call.
5. **Concurrency cap.** `max_fanout_concurrent` — semaphore in
   `RelayCore`? Per-user (single-user MVP, so trivial)? Configurable
   via `RELAY_*` env var?

## Phasing

This is **Phase 9** (post-MVP). MVP is done (CLAUDE.md). Sub-phases:

1. **9a — schema + events (no behaviour).** ✅ **DONE** (PR #2,
   merged 4ebb1f8; ADR-34). Added `awaiting_children` status,
   `child_runs_resolved` event kind, `_cascade_cancel_descendants`
   helper, spec.md §3 update.
2. **9b — fanout dispatch only (no join).** ✅ **DONE** (PR #3,
   merged 381c147; ADR-35). Sentinel parsed; child runs started;
   parent enters `awaiting_children`. Concurrency cap via
   `asyncio.Semaphore` (Option A). Depth cap via
   `max_fanout_depth`. Worktrees branch off parent HEAD.
3. **9c — join.** ✅ **DONE** (PR #4, merged 37b8cb7; ADR-36).
   `_maybe_resume_parent` watcher fires from child's `_run` finally;
   emits `subagent_return` × N + `child_runs_resolved`; transitions
   parent → `running`; enqueues synthesizer iter whose body is
   `compose_join_prompt(join_prompt, child_results)` (YAML-ish
   `RELAY_CHILD_RESULTS:` trailer in body, NOT preamble). Two
   structural fixes folded in: watcher invoked before
   `state.settled.set()`; `_dispatch_children` two-pass
   create-then-enqueue.
4. **9d — cancellation cascade.** ⏳ **NEXT**
   (`docs/plans/2026-05-21-fanout-join-9d.md`). Wire
   `_cascade_cancel_descendants` (already exists from 9a) into the
   runtime `cancel_run` path so cancelling an `awaiting_children`
   parent stops its in-flight children. Pure orchestrator change.
5. **9e — dashboard + skill guidance.** ⏳ **TODO**. Frontend
   Children pane; skill docs.
6. **9f — observability.** ⏳ **TODO**. OTel span parenting across
   runs; manual Langfuse acceptance.

Each sub-phase is shippable, deterministic-testable, and reversible.

## Acceptance criteria (per sub-phase)

**9b acceptance:**

- A scripted harness emits `[[engteam:fanout]]` with two children;
  two new runs exist with `parent_run_id` set; two
  `subagent_dispatch` events recorded on the parent; parent status
  is `awaiting_children`; children run their own iters
  independently.

**9c acceptance:**

- The 9b scenario, extended: both children complete; parent receives
  two `subagent_return` events; a join iter starts with
  `RELAY_CHILD_RESULTS` in its preamble; the parent run reaches a
  terminal status.

**9d acceptance:**

- Cancelling the parent during `awaiting_children` cancels both
  children; final event ordering is consistent; no leaked child
  processes (verified via tracked `RelayCore.children` map).

**9e acceptance:**

- Dashboard run-list defaults filter children out; toggle reveals
  them; parent run detail shows a Children pane with click-through.

**9f acceptance:**

- A live Langfuse trace tree shows a parent run with two
  child-run sub-trees rooted at the dispatching iter's span.

## Effort estimate

Significant. Order-of-magnitude:

- 9a (schema/events): 0.5 day.
- 9b (dispatch): 2–3 days.
- 9c (join): 2–3 days.
- 9d (cancel cascade): 1–2 days.
- 9e (dashboard + skill): 2 days.
- 9f (OTel): 1 day.

~10 working days total, plus a real-pi e2e demo + journal entry
(gated like the other `PI_INTEGRATION=1` acceptances per ADR-24).

Each sub-phase is independently mergeable; the feature is usable
(but not pretty) after 9c.

## Rejected alternatives (beyond Shape A above)

### Pi-side parallelism

Wait for pi to grow a Task-like tool. **Rejected** as a strategy:
relay's whole architecture (ADR-04, ADR-06) treats parallelism as an
orchestrator-layer concern. Even if pi gained subagents tomorrow, we
would still want orchestrator-layer fanout — it integrates with the
event store, worktrees, OTel, and cancellation. Pi-side subagents
would be a strictly weaker variant (less observable, less recoverable,
trapped inside one harness).

### Multiple parallel runs (manual, no orchestrator support)

The user starts N runs by hand and reconciles results themselves.
**Rejected** as a *replacement* (it doesn't compose into the skill
workflow), but it remains a valid manual workflow — nothing about
this proposal blocks it.

### Server-side process pool (no iter-tree model)

A pool of N pi workers consuming a queue of work units, no parent-
child semantics in the event store. **Rejected**: loses the
observability, replay, and merge-semantics that make relay
*relay* and not just "a pi process manager".

### Use the existing `paused` mechanism with manual child-run
linking

Have the skill emit `[[engteam:pause-for-input]]` after manually
starting child runs via MCP, then resolve the pause when children
complete. **Rejected**: works in theory but offloads orchestration
to the agent, defeating the point. The orchestrator should *own*
the fanout/join lifecycle.

## Related

- ADR-06 — pi has no subagents; relay manages them at the
  orchestrator layer.
- ADR-10 — event store as single source of truth (this proposal
  routes all child lifecycle through it).
- ADR-12 — single-process server (preserved; this proposal only
  adds child *pi* processes).
- ADR-13 — relay-provisioned per-run worktree (this proposal
  extends to per-child-run worktrees).
- ADR-20 — pause/resume infrastructure (the resume mechanism this
  proposal piggybacks on).
- ADR-28 — Phase 6 skill port, notes subagent parallelism as
  "post-MVP relay feature (a `subagent_dispatch` signal the
  orchestrator does not yet handle)" — this proposal is the
  implementation it anticipated.
- `docs/spec.md` §3.1 (schema), §3.2 (event taxonomy), §6 (loop +
  subagent dispatch paragraph), §12 (sentinel grammar).
- [[skills-harness-variants]] — independent proposal; touches a
  different layer (skill packaging vs orchestrator runtime).
