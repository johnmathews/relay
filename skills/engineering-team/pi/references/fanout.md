# Fanout reference

`[[engteam:fanout]]` is a closing sentinel that ends the current iter and
asks relay to spawn N parallel **child runs** from this run's current
worktree HEAD. When all children settle, relay re-enqueues this run with a
synthesizer iter whose user prompt is the `join_prompt` you supplied and
whose body trailer lists every child's `id` / `role` / `status` / `summary` /
`branch` / `worktree_path`.

This is the only mechanism for parallel work in relay today. Within a
single iter you still work sequentially — the lenses in Phase 1, the units
in Phase 3. Fanout operates at a coarser grain: an entire iter dispatches,
the parent waits, the parent resumes with the merged results.

> Contract source: `docs/spec.md` §3 (event taxonomy: `subagent_dispatch`,
> `subagent_return`, `child_runs_resolved`), §5.4 (sentinel grammar), §6
> (orchestrator: fanout / join lifecycle). The harness's sentinel parser
> implements the grammar (`src/relay/harness/signaling/sentinels.py`,
> payload model in `src/relay/harness/signaling/fanout.py`).

## When to emit `fanout`

Emit `fanout` when **two or more genuinely independent investigations**
have results that **merge cleanly into a single decision**. Each child gets
a fresh pi context (ADR-04); the synthesizer iter is the only place the
parent run sees their results, so the investigations have to be
self-contained.

Good fits:

1. **Parallel exploration that merges.** "Audit the frontend router AND the
   backend API surface; produce a unified fix list." Two independent
   passes; one combined plan.
2. **Candidate-and-pick.** "Implement candidate refactor A on its branch
   and candidate refactor B on its branch; the join iter compares
   diff sizes, test results, and benchmark output, then picks one."
3. **Multi-lens review of a single artifact.** "Review the new auth
   middleware from a security lens, a perf lens, and an accessibility
   lens — emit a merged findings doc."
4. **Sweeping a known set of work units.** When Phase 2's plan lists five
   units with no inter-dependencies, fanning them out as five child runs
   trades extra process count for wall-clock parallelism.

## When NOT to emit `fanout`

Do **not** fanout for:

1. **Sequential work.** If child B needs to read child A's output to start,
   that is not fanout — that is two iters in this run.
2. **A single investigation.** One audit, one refactor, one bugfix — write
   it in this iter and close with `handoff` / `done`.
3. **Small or cheap work.** Each child spawns a fresh pi process and a
   fresh worktree. The fixed cost dominates when each child's real work is
   under a minute.
4. **Speculative parallelism for its own sake.** Fanout because two paths
   genuinely need to be explored, not because parallel feels faster on
   paper.
5. **Work whose results don't merge cleanly.** If the join iter would
   struggle to reconcile child outputs — overlapping diffs on the same
   files, mutually exclusive design choices that aren't resolvable in a
   single synthesizer pass — keep it sequential and let one iter pick a
   direction.

## Format

Fanout is a **closing sentinel** like `handoff`, `done`, and
`pause-for-input`. Exactly one closing sentinel per iter; line-anchored at
column 0; same anti-mention rules as in `sentinels.md`.

The payload is a JSON block delimited by two marker sentinels in the
existing `[[engteam:...]]` namespace:

    [[engteam:fanout-start]]
    { ...JSON... }
    [[engteam:fanout-end]]

    [[engteam:fanout]]

The JSON schema (validated by `FanoutPayload` —
`src/relay/harness/signaling/fanout.py`):

| Field         | Type          | Required | Notes                                                  |
| ------------- | ------------- | -------- | ------------------------------------------------------ |
| `children`    | array of obj  | yes      | At least one child; empty array fails validation.      |
| `children[].role`   | string  | yes      | Short label surfaced in the synth trailer + dashboard. |
| `children[].prompt` | string  | yes      | The full user prompt body the child run starts with.   |
| `join_prompt` | string        | yes      | The user prompt body the synthesizer iter starts with. |

### Pairing rules you must obey

- Emit **exactly one** `fanout-start` / `fanout-end` pair before the
  closing `[[engteam:fanout]]` line. Missing pair → driver exits 1.
- Emit them **in order**: `fanout-start` first, `fanout-end` second.
- `[[engteam:fanout-end]]` must precede the closing `[[engteam:fanout]]`.
  Stray content between the JSON block and the closing sentinel may abort
  the iter (same shape as the `prompt-start` / `prompt-end` rules).
- **Do NOT** also emit `prompt-start` / `prompt-end` before `fanout`. The
  fanout payload IS the prompt mechanism — child prompts live inside the
  JSON `children[].prompt` field, the parent's resume prompt lives in
  `join_prompt`. Mixing the two marker pairs in one iter is undefined.

### Error messages you may see

The driver emits these one-line headlines on parse errors, each followed
by a repair recipe printed to stderr (matching the style of
`extract_handoff_prompt:` errors in `sentinels.md`):

- `extract_fanout_payload: no [[engteam:fanout-end]] found`
- `extract_fanout_payload: no [[engteam:fanout-start]] found before [[engteam:fanout-end]]`
- `fanout payload is not valid JSON: <reason>`
- `fanout payload failed validation: <reason>` (e.g. missing `join_prompt`,
  empty `children`, missing per-child `role` / `prompt`)

The repair recipe contains the literal correct-shape template (see "Worked
example" below).

## What happens after you emit `fanout`

When relay parses a well-formed fanout iter:

1. The dispatching iter closes with `exit_reason="signal"`,
   `signal_kind="fanout"`. The full payload is stored on the iter row
   (`iters.signal_args["payload"]`) so the synthesizer iter later can
   recover the `join_prompt`.
2. The parent run transitions to a new status, `awaiting_children`. Its
   loop does not advance until every child settles. (The parent is NOT
   terminal in this state — it can transition back to `running`.)
3. For each entry in `children`, relay creates a real child run:
   - `parent_run_id = <this run's id>`
   - `project_id = <this run's project_id>`
   - Worktree on branch `relay/<child_run_id>` **branched off this
     run's worktree HEAD** (not the project's default branch — the
     child starts from this run's in-progress work).
   - Initial user prompt = `children[i].prompt` verbatim.
   - A `subagent_dispatch` event is appended to the parent's stream.
4. Children run concurrently, capped by
   `RELAY_MAX_FANOUT_CONCURRENT` (default `4`). Each child is
   an ordinary run — its own iters, its own sentinels, its own SSE
   stream, individually cancellable, browsable in `/api/runs`.
5. When **all** siblings reach a terminal status, relay appends one
   `subagent_return` event per child plus one `child_runs_resolved` event
   to this run's stream, transitions this run from `awaiting_children`
   back to `running`, and enqueues a synthesizer iter:
   - Working directory: **this run's existing worktree** — the
     synthesizer is a continuation of this run, not a new workspace.
   - User prompt body: `join_prompt` from the fanout payload, with a
     trailer appended listing every child's `{id, role, status, summary,
     branch, worktree_path}` so you can read their diffs / artifacts
     directly.

Children may themselves emit `fanout` (recursive fanout). Recursion is
bounded by `RELAY_MAX_FANOUT_DEPTH` (default `2`, hard cap `4`). A child
attempted past the cap finalises as `failed` with a reason in its
`run_ended` payload — keep the tree shallow.

Cancelling a run in `awaiting_children` cascades depth-first to every
descendant (ADR-37). You don't need to cancel children manually.

## Cancellation semantics for the synthesizer

The synthesizer iter always runs once all children settle, **regardless
of how the children settled**: `done`, `failed`, `cancelled` — the join
iter sees all of them in the trailer and decides what to do. The
orchestrator does NOT auto-fail this run when a child fails. Your
synthesizer is responsible for reading each child's `status` and either
retrying, abandoning, or proceeding.

## Worked example

A Phase-2 planner has identified two independent audit lenses for the
target project:

`````
Based on the evaluation report I'm going to investigate the frontend and
backend in parallel and merge findings.

[[engteam:fanout-start]]
{
  "children": [
    {
      "role": "frontend-audit",
      "prompt": "Audit frontend/src for: router-state bugs, prop-drilling depth, untyped event handlers, and missing accessibility attributes. Write findings to $RELAY_RUN_DIR/frontend-audit.md.\n\n[[engteam:prompt-start]]\nyou are auditing the frontend; follow phase-1 evaluation discipline...\n[[engteam:prompt-end]]\n\n[[engteam:done]]"
    },
    {
      "role": "backend-audit",
      "prompt": "Audit src/relay/api and src/relay/orchestrator for: route handlers that bypass RelayCore, missing input validation, and untested error paths. Write findings to $RELAY_RUN_DIR/backend-audit.md.\n\n[[engteam:prompt-start]]\nyou are auditing the backend; follow phase-1 evaluation discipline...\n[[engteam:prompt-end]]\n\n[[engteam:done]]"
    }
  ],
  "join_prompt": "Read frontend-audit.md and backend-audit.md from each child's $RELAY_RUN_DIR (paths in the trailer). Cross-reference findings, deduplicate, and produce $RELAY_RUN_DIR/unified-fix-list.md ordered by severity. Then emit [[engteam:handoff]] with a prompt for Phase 3 to begin work on the unified list."
}
[[engteam:fanout-end]]

[[engteam:fanout]]
`````

Notes on the example:

- The child `prompt` fields are full prompt bodies including their own
  closing sentinels. Each child runs as an independent run with its own
  iters; the orchestrator does not splice this iter's preamble or
  sentinels onto the child.
- `join_prompt` is plain markdown; the synthesizer iter sees it as the
  next-iter prompt body, with the child-results trailer appended by relay
  below it.
- `[[engteam:fanout]]` is the closing sentinel. The iter does NOT also
  emit `handoff` / `done` / `pause-for-input`; doing so is a contract
  violation (multiple closing sentinels → driver exits 1).

## Anti-mention rule

The matcher is line-anchored (same as `sentinels.md`). To illustrate the
fanout grammar in chat without triggering the parser, indent the example
by at least one space, write it to a file via the Write tool, or echo it
from Bash.

## Who emits

Only the lead engineer (you, the single session). Children dispatched via
fanout each have their own pi session and emit their own closing
sentinels — but only the **top-level** session's assistant text is parsed
by the driver for any one run. A child's sentinels close the child's
iter, not the parent's.
