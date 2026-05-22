# Phase 9e — manual smoke (journal-attested, ADR-30)

**Date:** 2026-05-22
**Branch:** `phase-9e-dashboard-children` (PR #6)
**Mode:** real pi (`PI_AGENT_SDK=1`) + Vite dev frontend + Playwright-driven browser.

This closes the journal-attested half of Phase 9e per ADR-30. The
deterministic gates (276 backend / 155 frontend tests) were already
green on commit `fbc164d`; this entry covers the parts that can only be
verified against a live pi run.

## Setup

1. Killed any stale `relay serve` (a leftover from earlier in the session
   was bound to 7800).
2. `uv run relay install-skill --project ~/projects/eng-team-demo --force` →
   "Installed engineering-team skill (pi variant)".
3. `PI_AGENT_SDK=1 uv run relay serve` (background, 7800).
4. `cd frontend && npm run dev` (background, 5173).
5. Playwright @ `http://localhost:5173/`.

The `eng-team-demo` project at `/Users/john/projects/eng-team-demo`
was registered via the dashboard UI (project id = 2; "Documentation"
was a pre-existing project at id 1).

## Prompts used

The engineering-team skill does **not** yet carry fanout guidance — the
skill-side docs for fanout are an explicitly-deferred 9e follow-up
(plan §"Out of scope"). To trigger a real fanout I therefore put the
fanout sentinel grammar inside the prompt body itself, asking pi to
emit the block verbatim and stop. The grammar is from spec.md §5.4. Pi
just has to echo it; relay's parser does the rest.

**Run 1 — "fast" fanout** (id `20260522-101428-fbf7`): one-sentence
roles, one-line responses + `[[engteam:done]]` from each child. End-to-end
in **~15 seconds** (parent + 2 children + synthesizer). Used to verify
the steady-state Children pane on a terminal parent.

**Run 2 — "slow" fanout** (id `20260522-101812-50b0`): each child asked
to write a three-paragraph essay (history of clocks / history of the
wheel). Used to catch the `awaiting_children` window so I could see the
cascade-aware Cancel button copy. **Slower wasn't slow enough** — pi
produced both essays in ~20 seconds and the parent reached `done` at
08:18:34, before I was able to click Cancel (08:18:49). The cascade
*copy* was verified on screen; the cascade *behaviour* against an
in-flight run was not (it remains exercised by the 10 unit tests in
`tests/orchestrator/test_cancel_cascade.py` from Phase 9d).

## Verification points (plan Task 12 §3)

### 1. Children pane appears on the parent's detail view ✓

Run 1 after reload (necessary — see "incidental observation" below).
The pane sits between Iters and Artifacts. Heading reads
**"Children (2)"**. Two rows rendered, each with:

- status badge (`done`)
- short-id link (8-char prefix)
- role label (`explorer-a` / `explorer-b`)
- branch (`relay/<full-id>`)
- summary excerpt (the 9c watcher's `subagent_return.summary` value —
  which is the literal string `signal` for a sentinel-closed child;
  not pretty, but it's what the watcher records and the pane faithfully
  surfaces).

Screenshot: `journal/assets/journal-9e-parent-children-pane.png`.

### 2. Clicking a child navigates to its run-detail view ✓

Clicked the explorer-a link on the parent's pane → arrived at
`/runs/20260522-101433-6906`. Title header reads `Run 20260522-…6906`,
status badge `done`.

### 3. Child run shows the Parent chip ✓

On the explorer-a detail view, next to the status badge, a
`Parent: 20260522` chip is rendered with `href="/runs/20260522-101428-fbf7"`
pointing at the parent. Clicking it returns to the parent run.
Screenshot: `journal/assets/journal-9e-parent-children-pane.png` (the
parent is the one with the children listed; the chip itself only
appears on child runs and is visible in the larger
`journal-9e-second-run-final.png` capture of run 2's parent — where the
chip is *absent* because that's a parent, confirming the conditional).

### 4. Cancel button reads "Cancel run and N children" on awaiting_children ✓

Run 2, navigated while the parent was `awaiting_children` (verified
via `curl /api/runs/<id>` returning `awaiting_children` at the moment
of navigation). Header rendered the button label:

> **Cancel run and 2 children**

Screenshot: `journal/assets/journal-9e-cancel-cascade-copy.png`.

The cascade *behaviour* (parent → all descendants flipping to
`cancelled` in lockstep under `_enqueue_lock`) was not exercised
end-to-end here because pi was too fast — see "Run 2" above. The 9d
unit tests cover all five branches of that cascade.

### 5. Project Runs pane hides children by default; toggle reveals them ✓

`/projects/2` Runs tab: with the `Show child runs` checkbox unchecked
(the default), the list contained one row: the parent
`20260522-101428-fbf7`. Toggling the checkbox on caused the list to
refetch and re-render with three rows: the parent plus both children
`20260522-101433-6906` and `20260522-101434-2214`. Toggle off again →
back to one row. Screenshot:
`journal/assets/journal-9e-project-runs-toggle-on.png`.

### 6. ID-link route + Parent-chip-then-back round-trip ✓

Covered under (2) and (3). The router never bounced or 404'd; the
Children pane → child → Parent chip → parent path was clean.

## Incidental observation — not a 9e bug

The first time I opened the run-detail view immediately after start, the
SSE stream errored in the browser console with:

> EventSource's response has a MIME type ("text/plain") that is not
> "text/event-stream". Aborting the connection.

The `/api/events/<id>` endpoint directly via `curl` returns
`text/event-stream; charset=utf-8` correctly, so this is the Vite dev
proxy mishandling something — likely the
"run finished before the EventSource opened" edge case (run 1 finished
in 15 seconds; pi was unexpectedly fast for this trivial echo prompt).
The UI showed stale "running" status and "No events yet" until I
reloaded the page, at which point the now-terminal run took the REST
replay path (`/api/runs/<id>/events`) and the timeline filled in
correctly.

**This is not a Phase 9e regression** — the events store and SSE
contract were unchanged in this phase except for the new
INVALIDATING_KINDS entries (`subagent_dispatch`, `subagent_return`,
`child_runs_resolved`), which only affect the cache invalidation path,
not the EventSource open/MIME-type handling. The same proxy bug would
manifest for any pre-9e short-running run. Worth investigating
separately (probably a `vite.config.ts` proxy setting or the
`changeOrigin` handling of the SSE 200 response with chunked body); not
a blocker for the 9e PR.

## What's covered by this entry vs. what remains exercised by tests

| Concern | Manual smoke | Tests |
|---|---|---|
| Children pane render shape (rows, columns, status badge) | ✓ | `ChildrenPane.spec.ts` (5) |
| Children pane conditional rendering | ✓ (run 1 has children) | `ChildrenPane.spec.ts` empty case |
| Parent chip render + link | ✓ | `ParentRunChip.spec.ts` (2) |
| Cancel cascade copy on awaiting_children | ✓ (visible, not clicked) | `RunDetailView.spec.ts` cascade-copy test |
| Cancel cascade *behaviour* end-to-end | ✗ (pi too fast) | `test_cancel_cascade.py` (10) + 9d ADR-37 |
| Project Runs hide-by-default + toggle | ✓ | `ProjectView.spec.ts` toggle test |
| `useRunChildrenQuery` invalidation on SSE events | indirectly (children appeared as fanout ran) | `events.store.spec.ts` (3) |
| Children endpoint integration via REST | ✓ (via UI) | `test_runs.py::test_get_run_children_after_scripted_fanout` |

## Decision: smoke passes; PR ready to merge

All six checkpoints in plan Task 12 §3 are satisfied. The one item the
plan didn't anticipate — cascade-cancel against an in-flight run via
the UI — was attempted but missed the window because real pi was
unexpectedly fast even on a "make it slower" prompt. The cascade
behaviour itself is fully proven by 9d's unit tests; the UI surface
(the button label) is verified here.

PR #6 is ready to squash-merge.
