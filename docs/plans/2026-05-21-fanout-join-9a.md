# Plan — Phase 9a (fanout-join schema + events + orphan recovery)

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9a)
**Independent of:** `docs/plans/2026-05-21-skill-variants.md` (different layer)
**Predecessor of:** 9b (dispatch), 9c (join), 9d (cascade-cancel),
9e (dashboard), 9f (OTel)

## Goal

Land the **defensive plumbing** for the fanout-join feature without any
runtime behaviour change to existing flows. After 9a:

- The `awaiting_children` run status is recognized everywhere a status
  value flows (DB round-trip, SSE terminal check, frontend status badge).
- The `child_runs_resolved` event kind is documented in the taxonomy.
- The orphan-recovery sweep handles `awaiting_children` correctly per
  the S1 decision (cancel parent + cascade to children).

No fanout sentinel is parsed, no child runs are spawned, no preamble is
extended. 9a is *all* the foundational infrastructure 9b/9c will assume.

## Locked decisions (from the discussion)

- **S1 — restart during fanout:** cancel the parent with cascade to
  children. Honest about the "single-process MVP" limitation. The
  cascade helper added here is reused by 9d for runtime cancellation.
- **New status, not reused `paused`:** `awaiting_children` is its own
  status value. Frontend, MCP, OTel will all distinguish.
- **`awaiting_children` is NOT terminal:** like `paused`, it can
  transition back to `running` (when 9c lands). Must be excluded from
  every `_TERMINAL` constant.
- **`child_runs_resolved` event kind:** optional per proposal but worth
  reserving now — replay diff'ing benefits, and adding it to the
  taxonomy is one row.

## What 9a does NOT do

- Does not parse the `[[engteam:fanout-start]]` / `[[engteam:fanout-end]]`
  marker pair (9b).
- Does not spawn child runs (9b).
- Does not extend the preamble (9c).
- Does not modify the loop (9b/9c).
- Does not add the synthesizer iter logic (9c).
- Does not change the dashboard pane structure (9e).
- Does not change OTel (9f).

After 9a, you can create an `awaiting_children` row in the DB by hand
(or via test seeding) and verify the system handles it correctly on
restart and via SSE. But no production code path will create one.

## File-by-file changes

### Spec — `docs/spec.md`

**§3.1 (schema) — runs.status column comment.** Find the status enum
list; add `awaiting_children` with a one-line description:

> `awaiting_children` — parent run is suspended pending completion of
> child runs dispatched via fanout. Not terminal; transitions back to
> `running` when all children settle (9c). Set under the S1
> cancel-with-cascade convention on server restart (ADR-NN, 9a).

**§3.2 (event taxonomy) — add row for `child_runs_resolved`.**

| kind | payload | when emitted |
|---|---|---|
| `child_runs_resolved` | `{children_count: int, terminal_statuses: dict[run_id, status]}` | After all children of an `awaiting_children` parent reach terminal status; immediately before the parent's synthesizer iter is enqueued (9c). Optional but recommended for replay diffing — derivable from the preceding `subagent_return` events. |

**§6 (loop semantics)** — add one sentence to the "Subagent dispatch"
paragraph: *"On server restart, parents in `awaiting_children` are
treated as orphans: cancelled, with their children cascade-cancelled
(ADR-NN). Single-process MVP — recovering an in-flight fanout across a
restart is deliberate non-goal for V1."*

### Backend — `src/relay_v2/api/events.py`

Update the `_TERMINAL` constant and its docstring:

```python
# Run statuses that will emit no further events. A run in one of these
# is served as paginated history then EOF (replay mode, spec.md §9.3).
# ``paused`` and ``awaiting_children`` are NOT terminal — both can
# transition back to ``running`` (pause/resume and fanout/join
# respectively) — so both are treated as live.
_TERMINAL = frozenset({"done", "failed", "cancelled"})
```

Constant value unchanged (the omission of `awaiting_children` from the
set is the behaviour); only the comment is updated so a future reader
doesn't add it by accident.

### Backend — `src/relay_v2/core.py`

**Extend `_recover_orphans` to sweep `awaiting_children` with cascade.**
Current code is at `core.py:127–154`. Refactor:

```python
async def _recover_orphans(self) -> None:
    """Finalise any pre-existing in-flight run from a prior process (ADR-31).

    Single-user, single-process MVP (ADR-12): if a row is 'running' at
    startup it cannot be owned by any in-process task. ``paused`` rows
    are preserved — they can legitimately be resumed.
    ``awaiting_children`` rows are swept under the S1 convention
    (ADR-NN, 9a): cancel the parent and cascade-cancel its children.
    Recovering an in-flight fanout across a restart is a deliberate
    V1 non-goal.
    """
    async with self._sm() as s:
        rows = list(await s.scalars(
            select(Run).where(Run.status.in_(("running", "awaiting_children")))
        ))
    for run in rows:
        if run.status == "awaiting_children":
            await self._cascade_cancel_descendants(
                run.id, summary="orphaned: parent interrupted during fanout"
            )
        await set_run_status(
            self._sm, run.id, "cancelled", ended=True
        )
        await self._store.append(
            run.id,
            "run_ended",
            {"status": "cancelled", "summary": "orphaned: server restart"},
        )


async def _cascade_cancel_descendants(
    self, parent_run_id: str, *, summary: str
) -> None:
    """Cancel all non-terminal runs descended from ``parent_run_id``.

    Used by orphan recovery (9a) and runtime cancel-cascade (9d).
    Depth-first so a child's children resolve before the child itself.
    """
    _TERMINAL_STATUSES = ("done", "failed", "cancelled")
    async with self._sm() as s:
        children = list(await s.scalars(
            select(Run).where(Run.parent_run_id == parent_run_id)
        ))
    for child in children:
        if child.status in _TERMINAL_STATUSES:
            continue
        # Recurse first (depth-first), then finalise this child.
        await self._cascade_cancel_descendants(child.id, summary=summary)
        await set_run_status(self._sm, child.id, "cancelled", ended=True)
        await self._store.append(
            child.id,
            "run_ended",
            {"status": "cancelled", "summary": summary},
        )
```

At 9a, no `awaiting_children` rows can be created by production code (no
fanout sentinel parser yet), so the cascade is dormant in practice. But
the helper is tested in isolation (via direct DB seeding) and 9b/9c
inherit a fully-functional recovery path.

### Frontend — `frontend/src/api/sse.ts` (or wherever `TERMINAL_STATUSES` lives)

Mirror the backend comment update. The constant value (which excludes
both `paused` and `awaiting_children`) is correct as-is — only the
comment needs to mention `awaiting_children` so a future contributor
doesn't add it.

### Frontend — `frontend/src/components/shared/StatusBadge.vue`

Add `'awaiting_children'` to the `KNOWN` set. Add CSS for
`.status-badge--awaiting_children` — recommend a distinct color (e.g.,
amber/orange, distinct from `paused`'s usual blue and `running`'s green
to make the dashboard's status mix readable at a glance).

Copy: the badge will display the raw string `awaiting_children`. Verify
this renders acceptably; if it's too long, add a display-name mapping
(e.g., `"awaiting children"` with a space).

### Frontend — TypeScript types (generated)

The `Run.status` type is generated from `/openapi.json`. If the backend
schemas declare `status` as a constrained literal union (rather than
bare `str`), regenerating the client will pull `awaiting_children` into
the type. Verify by running `npm run gen:api` after the backend change
and inspecting the diff. If `status` is bare `str` on the backend,
nothing to do here — the type stays `string` and `awaiting_children` is
a valid string.

## Tests

### Backend

**`tests/orchestrator/test_orphan_recovery.py`** (extend or new file):

- `test_recover_orphans_sweeps_running` — existing behaviour regression.
- `test_recover_orphans_sweeps_awaiting_children` — seed a parent row with
  `status='awaiting_children'`, call `RelayCore.start()`, assert parent is
  `cancelled` + has a `run_ended` event with summary
  `"orphaned: server restart"`.
- `test_recover_orphans_cascades_to_children` — seed parent + 2 children
  (both `status='running'`), call `start()`, assert both children also
  `cancelled` with their own `run_ended` events whose summary contains
  `"parent interrupted during fanout"`.
- `test_recover_orphans_cascades_recursively` — seed grandchild scenario:
  parent → child (awaiting_children) → grandchild (running). Call `start()`.
  Assert grandchild is cancelled depth-first before child; child cancelled
  before parent.
- `test_cascade_skips_already_terminal_children` — seed parent
  (awaiting_children) + child with `status='done'`. Assert the `done`
  child is not touched (no second `run_ended` event appended); only
  the parent is finalised.

**`tests/orchestrator/test_events.py`** (or wherever SSE terminal logic is tested):

- `test_sse_treats_awaiting_children_as_live` — seed a run with
  `status='awaiting_children'`, open the SSE stream, assert it
  subscribes-then-replays (live path) rather than paginated-history-then-EOF
  (terminal path). The simplest assertion: after seeding, append a
  `subagent_dispatch` event, verify the SSE stream emits it.

**`tests/db/test_models.py`** (if it exists, else just covered by the orphan tests):

- `test_status_round_trips_awaiting_children` — write a `Run` with
  `status='awaiting_children'`, read it back, assert equal.

### Frontend

**`frontend/src/components/shared/StatusBadge.test.ts`** (or whatever the
existing test file is named):

- `renders awaiting_children with its dedicated style`.
- `awaiting_children is in the KNOWN set` (regression for "unknown" fallback styling).

### Test count delta

Backend: ~6 new test cases. Frontend: ~2 new test cases. No existing tests
should break (this is additive).

## Build sequence (commit-by-commit)

1. **`docs(spec): add awaiting_children status + child_runs_resolved event (9a)`**
   - `docs/spec.md` §3.1, §3.2, §6.
   - Append ADR-NN (next available number, likely ADR-33 or 34
     depending on skill-variants order) to `docs/decisions.md`
     covering the S1 decision: "fanout parents are cancelled on server
     restart; cascade-cancel descendants; recovering in-flight fanout
     across restart is V1 non-goal".

2. **`feat(orchestrator): orphan recovery handles awaiting_children + cascade (9a)`**
   - `src/relay_v2/core.py` — extend `_recover_orphans`, add
     `_cascade_cancel_descendants`.
   - Backend tests.
   - **Verify:** existing tests still pass (the `running`-only sweep
     behaviour is preserved); new tests cover `awaiting_children` and
     cascade.

3. **`feat(api): treat awaiting_children as live in SSE (9a)`**
   - `src/relay_v2/api/events.py` comment update (value unchanged).
   - Frontend `TERMINAL_STATUSES` comment + the StatusBadge addition.
   - SSE live-path test for `awaiting_children`.

4. **`docs(claude-md): note 9a (awaiting_children plumbing) under Current state`**
   - One-paragraph addition matching the existing format.

Four commits, mergeable as one PR.

## ADR-NN (draft for `docs/decisions.md`)

```markdown
## ADR-NN — Awaiting-children parents are cancelled on server restart (V1)

**Status:** accepted (2026-05-21)
**Phase:** 9a (post-MVP fanout-join foundation)

**Context.** The fanout-join feature (proposal:
`docs/proposals/parallel-iters-fanout-join.md`) introduces a new
`awaiting_children` run status: a parent suspended pending completion
of children dispatched via fanout. ADR-31/32 established that
orphan-recovery sweeps any `running` row to `cancelled` on startup
(single-process MVP per ADR-12). The new status creates a state-machine
gap: how should the sweep handle `awaiting_children`?

**Decision.** Sweep `awaiting_children` rows the same as `running` —
mark them `cancelled` with a `run_ended` event whose summary is
`"orphaned: server restart"`. Additionally, **cascade-cancel** the
parent's descendants (recursively) with summary
`"orphaned: parent interrupted during fanout"`. Recovering an
in-flight fanout across a server restart is a deliberate V1 non-goal.

**Rationale.** Honest about the single-user, single-process MVP
limitation (ADR-12). Symmetric with the existing `running` sweep — no
new "preserve and reconcile" pathway. The cascade helper
(`_cascade_cancel_descendants`) is reused by 9d for runtime
cancellation. A future "preserve and reconcile" model can be added in
a later ADR if real workflows demand restart-survival.

**Rejected:** preserve `awaiting_children` and add a startup
reconciler that checks "have all children finished while we were
down?" — strictly more code (new background task, child-state
validation, partial-completion handling) for a benefit (restart
survival) that single-user MVP users don't pay for.

**Related:** ADR-12 (single-process MVP), ADR-31 (run finalisation on
internal errors), ADR-32 (orphan recovery on startup), proposal
`docs/proposals/parallel-iters-fanout-join.md`.
```

## Acceptance criteria

- [ ] `docs/spec.md` §3.1 lists `awaiting_children` in the status enum.
- [ ] `docs/spec.md` §3.2 lists `child_runs_resolved` in the event
      taxonomy with its payload shape.
- [ ] `docs/spec.md` §6 includes one sentence on restart behaviour for
      `awaiting_children`.
- [ ] `docs/decisions.md` has the new ADR.
- [ ] `_recover_orphans` sweeps both `running` and `awaiting_children`;
      cascade descendants for `awaiting_children`; preserves `paused`
      unchanged.
- [ ] `_cascade_cancel_descendants` exists, is depth-first, skips already-
      terminal children, and is unit-tested.
- [ ] `_TERMINAL` constants (backend events.py, frontend sse.ts)
      exclude `awaiting_children`; comments explain why.
- [ ] `StatusBadge.vue` renders `awaiting_children` with dedicated styling.
- [ ] `uv run pytest` green (~194 + 6 new = ~200).
- [ ] `uv run ruff check .` clean.
- [ ] `uv run mypy` clean (38 source files).
- [ ] `frontend/ npm run check` green.
- [ ] **Manual smoke test:** seed an `awaiting_children` row in a
      scratch DB, start `relay serve`, verify the run becomes
      `cancelled` in the dashboard within seconds of startup.

## Risks and what could go wrong

- **Forgetting the comment update in `_TERMINAL`.** Trivial to miss; the
  *value* is correct as-is, only the comment changes. If someone later
  reads the comment and thinks "`awaiting_children` isn't here, must be
  an oversight," they'll add it and break SSE for awaiting parents.
  Mitigation: explicit comment + test (`test_sse_treats_awaiting_children_as_live`).
- **Frontend TypeScript regeneration.** If `npm run gen:api` is forgotten
  the frontend type may not include `awaiting_children`. The check
  catches this only if a component dereferences a status-specific
  field; otherwise the bare `string` type accepts everything. Mitigate
  by including a regeneration step in the build sequence.
- **Cascade helper recursing forever.** Tests must cover a malformed DB
  where a child points to itself or a cycle. Add `test_cascade_handles_cycle_safely`
  — seed a row with `parent_run_id == id`, verify cascade terminates
  (the early-return on terminal status handles this naturally after
  the first iteration, but verify).
- **`set_run_status` + `_store.append` are two separate operations.**
  If the process dies between the status update and the `run_ended`
  event append, the DB shows `cancelled` but no closing event. ADR-32's
  existing logic has the same gap; not introduced by 9a. Worth knowing
  but out of scope.
- **`awaiting_children` rows can't be created by production code yet.**
  All tests rely on direct DB seeding. This is correct — 9a is pure
  infrastructure — but the tests must do their own seeding (use
  `RelayCore`'s SM directly, or a session-scoped DB fixture).

## Effort estimate

~½ day:

- Spec updates: 30 minutes.
- ADR-NN: 30 minutes.
- `_recover_orphans` + `_cascade_cancel_descendants`: 1 hour.
- Backend tests (6 cases): 1 hour.
- Frontend StatusBadge + tests: 45 minutes.
- Manual smoke + full gate: 30 minutes.

Total ~4 hours, fits in one focused half-day session.

## What unblocks after 9a

9b can assume:
- An `awaiting_children` row is a recognized DB state with a known
  recovery story.
- The SSE infrastructure won't prematurely close an `awaiting_children`
  parent's stream.
- A cascade helper exists for runtime cancellation (9d).
- The `child_runs_resolved` event kind is reserved and documented; 9c
  emits it for free.

9b's scope shrinks accordingly: it adds the dispatch sentinel grammar
(per the S2 decision: new `fanout` closing verb + `fanout-start/end`
marker pair) and the loop → `_run` plumbing to spawn children, but does
not touch orphan recovery or SSE terminality.
