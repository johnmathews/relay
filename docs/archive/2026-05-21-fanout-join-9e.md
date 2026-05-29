# Plan — Phase 9e (dashboard "Children" pane)

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9e)
**Predecessors:** 9a (cascade helper + `awaiting_children`, PR #2 / 4ebb1f8), 9b (dispatch, PR #3 / 381c147), 9c (join watcher, PR #4 / 37b8cb7), 9d (runtime cancel-cascade, PR #5 / 4a910b4)
**Successors:** 9f (OTel span parenting across runs); skill-side fanout guidance (separate follow-up PR)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal

Land the dashboard frontend for the fanout-join feature: a Run-detail "Children" pane that lists a parent run's direct children (status, role, branch, summary), a small "Parent" chip on child runs for upward navigation, a Cancel button that works on `awaiting_children` parents with cascade-aware copy, and a Project-view "Show child runs" toggle that hides children from the run list by default.

Backend additions are deliberately minimal: one new `RelayCore.list_children` service method exposed as `GET /api/runs/{run_id}/children`, plus an `include_children: bool = False` query param on the existing `GET /api/runs`. Both flow through `RelayCore` (ADR-07/15) and reuse `api/schemas.py` Pydantic models — no new schema, no new event kinds, no new sentinel grammar, no MCP surface change.

The Children pane is fed by a new `useRunChildrenQuery` Pinia Colada hook keyed on `['runs', 'children', runId]`; the existing events store invalidates that key (alongside the existing `['runs', 'detail', …]` / `['runs']` keys) when it sees `subagent_dispatch` / `subagent_return` / `child_runs_resolved` events on the parent's SSE stream. The pane reads per-row `role` and `summary` directly from the events list already in memory — no per-child REST fetch, no polling. The coalesced-invalidation pattern is the codebase's established way of reacting incrementally to SSE events (`frontend/src/stores/events.ts` — `INVALIDATING_KINDS`, `armInvalidation`).

After 9e:

1. Opening a parent run that has fanned out shows a **Children** pane between Iters and Artifacts, one row per direct child: `[StatusBadge] <short-id link> · <role> · <branch> · <summary excerpt>`.
2. Opening a child run shows a **Parent** chip beside the header status badge, linking up.
3. Cancelling an `awaiting_children` parent is reachable from the UI (the 9d cascade is already wired server-side; 9e just makes the button visible with cascade-aware copy: "Cancel run and N children").
4. The Project-view Runs pane hides child rows by default; a "Show child runs" checkbox reveals them. The Hub view's "most recent run" card likewise never shows a child run unless `include_children=true` is passed.

## Architecture

**Server side — three thin additions.** All writes/reads route through `RelayCore` (ADR-07/15). Route handlers are 3-5-line adapters. Pydantic models are reused verbatim from `api/schemas.py`:

- `RelayCore.list_children(parent_run_id: str) -> list[Run]` — one `select(Run).where(Run.parent_run_id == parent_run_id).order_by(Run.started_at)` under `self._sm()`. Returns direct children only (no recursive tree walk — the pane renders a flat list per ADR's V1 scope; nested-tree rendering is out of scope for 9e).
- `GET /api/runs/{run_id}/children` returning `list[RunOut]` — a thin adapter that 404s on unknown parent, otherwise returns `[RunOut.model_validate(r) for r in await core.list_children(run_id)]`.
- `RelayCore.list_runs` grows an `include_children: bool = False` kwarg-only param. When `False` (default), the SELECT adds `where(Run.parent_run_id.is_(None))`. `GET /api/runs` exposes the param as `include_children: bool = False` and threads it through.

The `include_children=False` default is a **breaking change to the implicit API contract** (previously `GET /api/runs` returned everything). Acceptable because (a) it's the right semantic going forward — the run list is a list of top-level runs; child runs are a parent-detail concern; (b) the only callers today are the Hub and Project run-list panes plus the e2e tests, and the change is what they want; (c) `include_children=true` restores the old behaviour exactly.

**Client side — additive, no toolchain change.** ADR-26 mandates locked: Vue 3 + vue-router v5 + Pinia + Pinia Colada + Vite + TypeScript strict. Render pipeline unchanged. The typed client is regenerated from `/openapi.json` via `npm run gen:api` to pick up the new endpoint + param.

- New component `frontend/src/components/runs/ChildrenPane.vue` — takes `runId`, calls `useRunChildrenQuery(runId)`, joins each child row with the events store's `subagent_dispatch` (for `role`) and `subagent_return` (for `summary`) payloads, renders nothing when `children.length === 0`. Each row uses the existing `<StatusBadge>` + `<router-link>` to `/runs/<child_id>`.
- New component `frontend/src/components/shared/ParentRunChip.vue` — renders only when its `parentRunId` prop is non-null; routes to `/runs/<parentRunId>`. Mounted in `RunDetailView.vue` next to the status badge.
- Modified `frontend/src/views/RunDetailView.vue` — mount `<ChildrenPane>` between Iters and Artifacts; mount `<ParentRunChip>`; broaden the Cancel-button visibility predicate to `status ∈ {running, awaiting_children}`; format the button label conditionally on child count.
- Modified `frontend/src/views/ProjectView.vue` — add a "Show child runs" checkbox above the Runs pane; bind to a local `showChildren` ref; thread into `useRunsQuery({ projectId, includeChildren })`.
- Modified `frontend/src/lib/queries.ts` — new `useRunChildrenQuery(runId)` hook + `keys.runChildren(runId)`; `RunListFilters` gains `includeChildren?: boolean`; `useRunsQuery` threads it as the `include_children` query param.
- Modified `frontend/src/stores/events.ts` — add `subagent_dispatch`, `subagent_return`, `child_runs_resolved` to `INVALIDATING_KINDS`; extend `armInvalidation`'s coalesced batch to also invalidate `['runs', 'children', openRunId]` so the pane refetches in lockstep with the existing detail/list invalidation.
- Regenerated `frontend/src/api/schema.d.ts` via `npm run gen:api` (one-shot after the backend route lands).

**SSE refresh model.** The events store already opens the SSE stream for `awaiting_children` parents (the live, non-terminal path — `TERMINAL_STATUSES` correctly excludes it; load-bearing comment at `frontend/src/views/RunDetailView.vue:60-66`). When `subagent_dispatch` lands on the parent's stream during fanout dispatch, the pane refetches and the new rows appear. When `subagent_return` lands per-child as the 9c watcher fires, the pane refetches and the row's `summary` populates from the events list. When `child_runs_resolved` lands, the pane refetches once more (the children's statuses will all be terminal at that point). When the parent's own `run_ended` lands (after the synthesizer iter or after a cascade), the pane refetches one final time. No polling.

**What the pane does NOT do.** No per-child SSE stream. No nested-tree rendering. No live-mutation of row state from the SSE payloads directly (the codebase's established pattern is "SSE event → invalidate Colada key → refetch"; in-place row mutation would create two sources of truth and defeat ADR-10). No "Show child runs" toggle in the Hub view (the Hub's "most recent run" card is per-project and a child being the most recent would still be wrong UX; default-hidden everywhere).

**What 9e changes about the Cancel button.** Today the button is gated on `status === 'running'`. After 9e: `status ∈ {running, awaiting_children}`. The label depends on the children count read from the new query: `"Cancel run and 2 children"` when `awaiting_children` with 2 children, `"Cancel run and N children"` for N>0, otherwise `"Cancel run"`. The button still calls `useCancelRunMutation()` → `POST /api/runs/{id}/cancel`. The backend cascade (9d) does the rest.

**Why no new ADR (probably).** The choices in 9e are direct applications of ADRs already in force:
- ADR-15 — RelayCore is the single chokepoint → adding `list_children` is just expanding its surface.
- ADR-23 — the SSE broadcaster only tails the event store → the children pane consumes the same stream via the existing events store.
- ADR-26 — frontend toolchain mandates → 9e uses Vue 3 + Pinia + Pinia Colada exactly as mandated.
- The `include_children` default-false on `GET /api/runs` is a contract change worth recording. If the implementing engineer decides it crosses the ADR bar (a behaviour change to a previously-stable endpoint), add ADR-38 — but the task list does NOT pre-claim ADR-38; the writer judges after the implementation lands. Conservative default: no ADR; the spec.md §7 + §9 update + the proposal-doc-quoted-paragraph cover it.

**Tech stack.** No new runtime deps. Reuses `RelayCore`, `EventStore`, `api/schemas.RunOut`, `useQuery`/`useMutation` (Pinia Colada), `StatusBadge.vue` (amber `awaiting_children` variant already present from 9a), `<router-link>` (vue-router v5), the existing events-store `INVALIDATING_KINDS` + `armInvalidation` machinery.

## File map

| file | action | one-line responsibility |
|---|---|---|
| `src/relay_v2/core.py` | modify | add `list_children(parent_run_id)`; extend `list_runs` with `*, include_children: bool = False` |
| `src/relay_v2/api/runs.py` | modify | add `GET /api/runs/{run_id}/children`; extend `GET /api/runs` with `include_children` query param |
| `tests/orchestrator/test_relay_core.py` | modify | unit tests for `list_children` (empty / direct-only / ordering) + `list_runs(include_children=…)` |
| `tests/api/test_runs.py` | modify | route tests for the new children endpoint (200 / 404) + `include_children` param |
| `frontend/src/api/schema.d.ts` | regenerate | `npm run gen:api` once the backend route lands |
| `frontend/src/lib/queries.ts` | modify | add `keys.runChildren(runId)` + `useRunChildrenQuery(runId)`; extend `RunListFilters` with `includeChildren?: boolean`; thread it through `useRunsQuery` |
| `frontend/src/stores/events.ts` | modify | add `subagent_dispatch` / `subagent_return` / `child_runs_resolved` to `INVALIDATING_KINDS`; invalidate `['runs', 'children', openRunId]` in the coalesced batch |
| `frontend/src/components/runs/ChildrenPane.vue` | create | conditional pane; renders one row per direct child with status + short-id link + role + branch + summary |
| `frontend/src/components/shared/ParentRunChip.vue` | create | conditional chip linking child → parent run |
| `frontend/src/views/RunDetailView.vue` | modify | mount `<ChildrenPane>` between Iters and Artifacts; mount `<ParentRunChip>` next to the status badge; broaden Cancel button predicate + label |
| `frontend/src/views/ProjectView.vue` | modify | add "Show child runs" checkbox above the Runs pane; thread `includeChildren` into `useRunsQuery` |
| `frontend/src/components/runs/__tests__/ChildrenPane.spec.ts` | create | empty / dispatched-only / summary-flows-from-events / refetch-on-invalidate cases |
| `frontend/src/components/shared/__tests__/ParentRunChip.spec.ts` | create | render / hidden-when-null / link target |
| `frontend/src/views/__tests__/RunDetailView.spec.ts` | modify | Cancel-button visibility + label on `awaiting_children`; parent chip rendering |
| `frontend/src/views/__tests__/ProjectView.spec.ts` | modify | checkbox toggles `include_children=true` and the query refetches |
| `frontend/src/stores/__tests__/events.spec.ts` | modify | `subagent_dispatch` arms invalidation that targets `['runs', 'children', …]` |
| `docs/spec.md` | modify | §7 — children endpoint + `include_children` param; §9.1 — Children pane + Parent chip + Cancel-button cascade copy + "Show child runs" toggle |
| `docs/dashboard.md` | modify | add operational notes for the Children pane + Show-child-runs toggle |
| `CLAUDE.md` | modify | extend "Current state" with a 9e walkthrough |

No new ADR pre-claimed. If implementation surfaces a decision worth recording, append ADR-38 at the bottom of `docs/decisions.md` (the file currently ends at ADR-37 from 9d).

## ADR claim

**No ADR pre-claimed.** All decisions in this plan are direct applications of ADR-15 (RelayCore chokepoint), ADR-23 (broadcaster scope), ADR-26 (frontend mandates), and ADR-10 (event store as single source of truth). If the implementation reveals a decision that does not fall cleanly under those — the most likely candidate is a write-up of "`include_children=False` as the new default on `GET /api/runs`" if it bites a downstream consumer — append a new ADR-38 then.

## Open contract questions

**OCQ-1 — Should an `awaiting_children` parent be reachable in the run list by default?**

The Project-view Runs pane already lists every status (no status filter applied by default), so an `awaiting_children` parent appears in the list under both the new default (`include_children=False` — parents are by definition `parent_run_id IS NULL`, so they survive the filter) and the toggle-on path. **Resolution: yes, the parent is visible by default; only its child runs are hidden by default.** No code action; the assertion is recorded in Task 6 as a regression test (parent in the list + children excluded).

**OCQ-2 — Does the children endpoint need its own paginated form?**

A fanout's `children` array is bounded by `max_fanout_concurrent` (default 4, hard cap configurable). A single parent's direct children are O(few). **Resolution: no pagination.** The endpoint returns the full list. If a future feature lifts the concurrency cap dramatically, add pagination then. Recorded here so a reader doesn't second-guess.

**OCQ-3 — Should the Hub view's "most recent run" card respect `include_children=False`?**

Yes. The Hub calls `useRunsQuery({ projectId, limit: 1 })`. With 9e's default flipped to `include_children=False`, the card naturally surfaces the most recent **top-level** run — which is what the user means by "the project's most recent run". A child being the most recent would be misleading. **Resolution: the Hub doesn't need a code change beyond the query default flipping.** Add a regression test that the Hub never shows a child as the most-recent card (Task 8 covers it).

**OCQ-4 — Should `subagent_dispatch` events on a parent's stream also invalidate `['runs']` (the list prefix)?**

Adding `subagent_dispatch` to `INVALIDATING_KINDS` already does this — the existing `armInvalidation` body invalidates `['runs']` unconditionally on each fire. So when fanout dispatches new child runs, the Project Runs pane (and the Hub) refetch and the new child rows appear — but only if the toggle is on. With the toggle off (default), the new child rows don't appear in the list (correct: they're hidden by default). **Resolution: no additional change; the existing `['runs']` invalidation in `armInvalidation` is sufficient.**

**OCQ-5 — How should the pane render a child whose `subagent_return` payload is missing (e.g., a child cancelled mid-flight via the 9d cascade)?**

The 9d cascade writes a `run_ended` event on the child's stream but does NOT write a `subagent_return` on the parent's stream (the 9c watcher is bypassed because the parent has already flipped to `cancelled`). So a cancelled child shows `status=cancelled` from the children-endpoint query, but has no `summary` to pull from the events list. **Resolution: render `summary` as empty (no fallback text). The status badge already conveys "cancelled" clearly; an "(no summary)" fallback would be noise.** Test in Task 7.

## Tasks (TDD-ordered)

---

### Task 1 — Backend: `RelayCore.list_children`

**~10 min**

**Files:**
- Modify: `src/relay_v2/core.py`
- Modify: `tests/orchestrator/test_relay_core.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/orchestrator/test_relay_core.py` (find the existing run-listing tests and append):

```python
async def test_list_children_empty_for_run_without_fanout() -> None:
    """A run with no fanout has no children — empty list, not None."""
    core, _settings = await _make_core()
    project_id = await _make_project(core)
    run_id = await core.start_run(project_id, "hello", max_iters=1)
    children = await core.list_children(run_id)
    assert children == []


async def test_list_children_returns_direct_children_only() -> None:
    """list_children returns rows where parent_run_id == argument, ordered by started_at asc.

    Recursive (grandchildren) are out of scope for 9e — the pane renders a flat
    list per direct child.
    """
    core, _settings = await _make_core()
    project_id = await _make_project(core)
    parent_id = await core.start_run(project_id, "parent", max_iters=1)
    # Directly insert two children + one grandchild via the DB layer (no
    # fanout sentinel needed — we're testing list_children, not dispatch).
    child_a = await _make_child_run(core, project_id, parent_id, "child-a")
    child_b = await _make_child_run(core, project_id, parent_id, "child-b")
    _grandchild = await _make_child_run(core, project_id, child_a, "grandchild")

    direct = await core.list_children(parent_id)
    assert {r.id for r in direct} == {child_a, child_b}
    assert [r.id for r in direct] == sorted(
        [child_a, child_b],
        key=lambda rid: next(r.started_at for r in direct if r.id == rid),
    )
```

Add the `_make_child_run` helper near `_make_project` if it does not already exist:

```python
async def _make_child_run(
    core: RelayCore,
    project_id: int,
    parent_run_id: str,
    prompt_body: str,
) -> str:
    """Insert a child run row directly (no fanout sentinel)."""
    from relay_v2.db.repo import create_run

    child_id = core._new_run_id()
    await create_run(
        core._sm,
        run_id=child_id,
        project_id=project_id,
        prompt_body=prompt_body,
        max_iters=1,
        iter_timeout=60,
        worktree_path=None,
        branch=None,
        parent_run_id=parent_run_id,
    )
    return child_id
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/orchestrator/test_relay_core.py::test_list_children_empty_for_run_without_fanout -v
uv run pytest tests/orchestrator/test_relay_core.py::test_list_children_returns_direct_children_only -v
```

Expected: AttributeError / NameError — `RelayCore` has no `list_children`.

- [ ] **Step 3: Implement `list_children`**

Add to `src/relay_v2/core.py` immediately after the existing `list_runs` method (~line 1033):

```python
async def list_children(self, parent_run_id: str) -> list[Run]:
    """Direct children of ``parent_run_id``, ordered by started_at asc.

    Returns ``[]`` for a parent that never fanned out. Does NOT walk
    grandchildren — the dashboard pane (spec.md §9.1, 9e) renders one row
    per direct child only. A nested-tree view is a future enhancement.
    """
    async with self._sm() as s:
        stmt = (
            select(Run)
            .where(Run.parent_run_id == parent_run_id)
            .order_by(Run.started_at.asc())
        )
        return list(await s.scalars(stmt))
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/orchestrator/test_relay_core.py::test_list_children_empty_for_run_without_fanout tests/orchestrator/test_relay_core.py::test_list_children_returns_direct_children_only -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_relay_core.py
git commit -m "$(cat <<'EOF'
Phase 9e: RelayCore.list_children

Direct children of a parent run, ordered by started_at asc. Returns []
for a parent that never fanned out. Used by the dashboard Children pane
(spec.md §9.1, 9e). No recursive tree walk — direct children only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2 — Backend: `list_runs` gains `include_children` flag

**~10 min**

**Files:**
- Modify: `src/relay_v2/core.py`
- Modify: `tests/orchestrator/test_relay_core.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/orchestrator/test_relay_core.py`:

```python
async def test_list_runs_excludes_children_by_default() -> None:
    """list_runs() default behaviour: top-level rows only (parent_run_id IS NULL)."""
    core, _settings = await _make_core()
    project_id = await _make_project(core)
    parent_id = await core.start_run(project_id, "parent", max_iters=1)
    _child_id = await _make_child_run(core, project_id, parent_id, "child")

    rows = await core.list_runs(project_id)
    assert {r.id for r in rows} == {parent_id}


async def test_list_runs_includes_children_when_requested() -> None:
    """list_runs(include_children=True) returns the full set."""
    core, _settings = await _make_core()
    project_id = await _make_project(core)
    parent_id = await core.start_run(project_id, "parent", max_iters=1)
    child_id = await _make_child_run(core, project_id, parent_id, "child")

    rows = await core.list_runs(project_id, include_children=True)
    assert {r.id for r in rows} == {parent_id, child_id}
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/orchestrator/test_relay_core.py::test_list_runs_excludes_children_by_default tests/orchestrator/test_relay_core.py::test_list_runs_includes_children_when_requested -v
```

Expected: the first fails (current `list_runs` returns both parent + child); the second fails (TypeError: unexpected keyword).

- [ ] **Step 3: Implement the flag**

Edit `src/relay_v2/core.py` `list_runs`:

```python
async def list_runs(
    self,
    project_id: int | None = None,
    *,
    include_children: bool = False,
) -> list[Run]:
    """List runs for a project (or all if ``project_id`` is None).

    By default returns only top-level runs (``parent_run_id IS NULL``);
    pass ``include_children=True`` to include child runs dispatched via
    fanout. The dashboard Run lists (spec.md §9.1) default-hide children
    so the list stays readable when fanout is in use.
    """
    async with self._sm() as s:
        stmt = select(Run).order_by(Run.started_at.desc())
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        if not include_children:
            stmt = stmt.where(Run.parent_run_id.is_(None))
        return list(await s.scalars(stmt))
```

- [ ] **Step 4: Audit existing `list_runs` callers**

```
grep -rn "\.list_runs(" src/relay_v2/ tests/
```

Expected callers: `src/relay_v2/api/runs.py` (Task 4 will update), `src/relay_v2/mcp/server.py` (the `relay__list_runs` tool), and tests. For each non-test caller, decide whether the call wants top-level-only (leave as-is, the new default) or the full set (add `include_children=True` in the same commit). For the MCP tool: the Claude-Code user driving relay via MCP wants to see the full tree — pass `include_children=True` there. Add a one-line MCP test asserting child runs are surfaced if the project doesn't have one already.

- [ ] **Step 5: Run the broader suite to catch test fallout**

```
uv run pytest tests/ -q
```

Expected: all PASS. If a test fails because it implicitly depended on `list_runs` returning child rows, update that test to pass `include_children=True` (the test is asserting "all runs including children"; making that explicit is the right outcome). Do NOT keep the old implicit behaviour — that's exactly what 9e is fixing.

- [ ] **Step 6: Run the full backend gate**

```
uv run ruff check .
uv run mypy
uv run pytest -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/relay_v2/core.py tests/orchestrator/test_relay_core.py
git commit -m "$(cat <<'EOF'
Phase 9e: list_runs gains include_children flag

Default flips to include_children=False so the dashboard Run lists
(spec.md §9.1, 9e) hide child runs by default. A "Show child runs"
toggle in the Project view will re-include them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3 — Backend: `GET /api/runs/{run_id}/children` route

**~15 min**

**Files:**
- Modify: `src/relay_v2/api/runs.py`
- Modify: `tests/api/test_runs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_runs.py`. Match the existing test style there (async + `httpx.AsyncClient` over `ASGITransport`; see the file's prelude for the standard fixtures):

```python
async def test_get_run_children_empty(api_client: AsyncClient, scripted_harness: ScriptedHarness) -> None:
    """A run that never fanned out returns an empty children list."""
    project_id = await _register_project(api_client)
    run_id = await _start_run(api_client, project_id, "hello")

    res = await api_client.get(f"/api/runs/{run_id}/children")
    assert res.status_code == 200
    assert res.json() == []


async def test_get_run_children_unknown_run(api_client: AsyncClient) -> None:
    """Unknown run → 404."""
    res = await api_client.get("/api/runs/unknown-run-id/children")
    assert res.status_code == 404


async def test_get_run_children_returns_direct_children(
    api_client: AsyncClient,
    scripted_harness: ScriptedHarness,
) -> None:
    """A parent with two direct children returns them ordered by started_at."""
    project_id = await _register_project(api_client)
    parent_id = await _start_run(api_client, project_id, "parent")
    # Seed two children directly via the core (no fanout sentinel needed
    # for this route test).
    core = scripted_harness.core
    child_a = await _seed_child(core, project_id, parent_id, "child-a")
    child_b = await _seed_child(core, project_id, parent_id, "child-b")

    res = await api_client.get(f"/api/runs/{parent_id}/children")
    assert res.status_code == 200
    body = res.json()
    assert {row["id"] for row in body} == {child_a, child_b}
    # Every row is a full RunOut.
    for row in body:
        assert row["parent_run_id"] == parent_id
        assert "status" in row
        assert "branch" in row
```

Reuse / add a `_seed_child` helper in the test file mirroring Task 1's `_make_child_run`.

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/api/test_runs.py -k children -v
```

Expected: 404 on every test (route does not exist).

- [ ] **Step 3: Implement the route**

Add to `src/relay_v2/api/runs.py` immediately after the `get_run` route (~line 89):

```python
@router.get(
    "/runs/{run_id}/children",
    response_model=list[RunOut],
)
async def list_run_children(
    run_id: str, core: CoreDep
) -> list[RunOut]:
    """Direct children of a run (spec.md §7, 9e).

    Returns the rows where ``parent_run_id == run_id``, ordered by
    ``started_at`` ascending. Returns ``[]`` for a parent that never
    fanned out. 404 if ``run_id`` itself is unknown.
    """
    if await core.get_run(run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    children = await core.list_children(run_id)
    return [RunOut.model_validate(r) for r in children]
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/api/test_runs.py -k children -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/relay_v2/api/runs.py tests/api/test_runs.py
git commit -m "$(cat <<'EOF'
Phase 9e: GET /api/runs/{id}/children endpoint

Thin adapter over RelayCore.list_children. Returns list[RunOut] of the
run's direct children (no recursion). 404 on unknown parent. Used by
the dashboard Children pane (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4 — Backend: `GET /api/runs` gains `include_children` query param

**~10 min**

**Files:**
- Modify: `src/relay_v2/api/runs.py`
- Modify: `tests/api/test_runs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_runs.py`:

```python
async def test_list_runs_excludes_children_by_default(
    api_client: AsyncClient,
    scripted_harness: ScriptedHarness,
) -> None:
    project_id = await _register_project(api_client)
    parent_id = await _start_run(api_client, project_id, "parent")
    _child_id = await _seed_child(scripted_harness.core, project_id, parent_id, "child")

    res = await api_client.get("/api/runs", params={"project_id": project_id})
    assert res.status_code == 200
    body = res.json()
    assert {row["id"] for row in body} == {parent_id}


async def test_list_runs_includes_children_when_requested(
    api_client: AsyncClient,
    scripted_harness: ScriptedHarness,
) -> None:
    project_id = await _register_project(api_client)
    parent_id = await _start_run(api_client, project_id, "parent")
    child_id = await _seed_child(scripted_harness.core, project_id, parent_id, "child")

    res = await api_client.get(
        "/api/runs",
        params={"project_id": project_id, "include_children": "true"},
    )
    assert res.status_code == 200
    body = res.json()
    assert {row["id"] for row in body} == {parent_id, child_id}
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/api/test_runs.py -k list_runs -v
```

Expected: the default-excludes test fails (current route returns both); the include-children test fails (param unrecognised).

- [ ] **Step 3: Thread the param through the route**

Edit `src/relay_v2/api/runs.py` `list_runs`:

```python
@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    core: CoreDep,
    project_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_children: bool = False,
) -> list[RunOut]:
    rows = await core.list_runs(project_id, include_children=include_children)
    if status is not None:
        rows = [r for r in rows if r.status == status]
    rows = rows[offset : offset + limit]
    return [RunOut.model_validate(r) for r in rows]
```

- [ ] **Step 4: Run the broader suite**

```
uv run pytest tests/api/ -v
```

Expected: all PASS. If an existing test implicitly depended on the old "include everything" behaviour, update it to pass `include_children=True` — same principle as Task 2 Step 4.

- [ ] **Step 5: Validate OpenAPI**

```
uv run python -c "from relay_v2.api import create_app; from openapi_spec_validator import validate; from relay_v2.config import Settings; import json; app = create_app(Settings(), harness=None); spec = app.openapi(); validate(spec); print('OK'); print('include_children param:', any(p.get('name') == 'include_children' for p in spec['paths']['/api/runs']['get']['parameters']))"
```

Expected: `OK` then `include_children param: True`. (Adjust the inline script if the project's `Settings()` requires args; consult `tests/api/conftest.py` for the canonical way to instantiate.)

- [ ] **Step 6: Commit**

```bash
git add src/relay_v2/api/runs.py tests/api/test_runs.py
git commit -m "$(cat <<'EOF'
Phase 9e: GET /api/runs gains include_children query param

Default false — top-level runs only. include_children=true restores
the previous behaviour (returns child runs too). The dashboard Project
Runs pane will default-hide children and surface a "Show child runs"
toggle (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5 — Frontend: regenerate the typed API client

**~5 min**

**Files:**
- Regenerate: `frontend/src/api/schema.d.ts`

- [ ] **Step 1: Start the backend in one shell**

```
uv run relay serve
```

Leave it running. (The generator needs a live `/openapi.json`.)

- [ ] **Step 2: Regenerate the client**

In a second shell:

```
cd frontend
npm run gen:api
```

Expected: `schema.d.ts` updated. Diff should show the new `/api/runs/{run_id}/children` path and the new `include_children` parameter on `/api/runs`.

- [ ] **Step 3: Verify the client compiles**

```
cd frontend
npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Stop the backend**

`Ctrl-C` the `relay serve` shell.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/schema.d.ts
git commit -m "$(cat <<'EOF'
Phase 9e: regenerate typed API client

Picks up GET /api/runs/{id}/children and the include_children query
param on GET /api/runs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6 — Frontend: `useRunChildrenQuery` + `RunListFilters.includeChildren`

**~20 min**

**Files:**
- Modify: `frontend/src/lib/queries.ts`

- [ ] **Step 1: Write the failing test (via `RunDetailView` consumer)**

A queries-layer hook is awkward to test in isolation (Pinia Colada needs a Vue app + a Pinia instance + the openapi-fetch transport). The behaviour will be covered transitively by `ChildrenPane.spec.ts` in Task 9. **No standalone test for the hook itself in this task** — the type signature + integration tests cover it. Skip directly to implementation.

- [ ] **Step 2: Add `keys.runChildren`**

In `frontend/src/lib/queries.ts`, inside the `keys` object, add (alongside `runDetail` and `runEvents`):

```typescript
  /**
   * Direct children of a parent run (`GET /api/runs/{id}/children`).
   * Nested under `['runs', …]` so `invalidate(keys.runs())` (post-
   * mutation / SSE push) also refreshes an open Children pane.
   */
  runChildren: (runId: string): readonly ['runs', 'children', string] =>
    ['runs', 'children', runId] as const,
```

- [ ] **Step 3: Extend `RunListFilters`**

In the same file, find the `RunListFilters` interface and add `includeChildren`:

```typescript
/** Filters accepted by `GET /api/runs`. */
export interface RunListFilters {
  projectId?: number
  status?: string
  limit?: number
  offset?: number
  /**
   * When true, include child runs (parent_run_id NOT NULL). Default
   * false — the run lists default-hide children so the list stays
   * readable when fanout is in use (spec.md §9.1, 9e).
   */
  includeChildren?: boolean
}
```

- [ ] **Step 4: Thread `includeChildren` into `useRunsQuery`**

In `useRunsQuery`, update the query body to send the param:

```typescript
export function useRunsQuery(
  filters: MaybeRefOrGetter<RunListFilters>,
): UseQueryReturn<Run[]> {
  return useQuery({
    key: () => keys.runList(toValue(filters)),
    query: async () => {
      const f = toValue(filters)
      return unwrap(
        await api.GET('/api/runs', {
          params: {
            query: {
              project_id: f.projectId,
              status: f.status,
              limit: f.limit,
              offset: f.offset,
              include_children: f.includeChildren,
            },
          },
        }),
      )
    },
  })
}
```

- [ ] **Step 5: Add `useRunChildrenQuery`**

Add a new export below `useRunDetailQuery`:

```typescript
/**
 * `useQuery` for a run's direct children (`GET /api/runs/{id}/children`).
 * Feeds the dashboard Children pane (spec.md §9.1, 9e). The events
 * store invalidates `keys.runChildren(runId)` when a `subagent_dispatch`,
 * `subagent_return`, or `child_runs_resolved` event lands on the parent's
 * SSE stream, so the pane refetches in lockstep with each lifecycle
 * transition. No polling; no per-child SSE.
 */
export function useRunChildrenQuery(
  runId: MaybeRefOrGetter<string>,
): UseQueryReturn<Run[]> {
  return useQuery({
    key: () => keys.runChildren(toValue(runId)),
    query: async () =>
      unwrap(
        await api.GET('/api/runs/{run_id}/children', {
          params: { path: { run_id: toValue(runId) } },
        }),
      ),
  })
}
```

- [ ] **Step 6: Verify the file compiles**

```
cd frontend
npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/queries.ts
git commit -m "$(cat <<'EOF'
Phase 9e: useRunChildrenQuery + RunListFilters.includeChildren

Adds the Colada hook + query key for the run-children endpoint, and
threads include_children through useRunsQuery. Feeds the new Children
pane (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7 — Frontend: events store invalidates the children key

**~15 min**

**Files:**
- Modify: `frontend/src/stores/events.ts`
- Modify: `frontend/src/stores/__tests__/events.spec.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/stores/__tests__/events.spec.ts`:

```typescript
it('invalidates runChildren key on subagent_dispatch', async () => {
  const invalidated: Array<readonly unknown[]> = []
  const store = useEventsStore()
  await store.open('run-1', 'awaiting_children', {
    invalidate: (key) => invalidated.push(key),
    streamOptions: { eventSourceFactory: makeFakeEventSource },
  })

  pushEvent({
    type: 'subagent_dispatch',
    lastEventId: '1',
    data: JSON.stringify({ child_run_id: 'child-a', role: 'explorer', prompt: 'x' }),
  })

  // Coalesced — flush the microtask queue.
  await Promise.resolve()
  await Promise.resolve()

  // The arming fires three keys: ['runs', 'detail', runId], ['runs'],
  // and (new in 9e) ['runs', 'children', runId].
  expect(invalidated).toContainEqual(['runs', 'children', 'run-1'])
})

it('also invalidates on subagent_return and child_runs_resolved', async () => {
  const invalidated: Array<readonly unknown[]> = []
  const store = useEventsStore()
  await store.open('run-1', 'awaiting_children', {
    invalidate: (key) => invalidated.push(key),
    streamOptions: { eventSourceFactory: makeFakeEventSource },
  })

  pushEvent({
    type: 'subagent_return',
    lastEventId: '1',
    data: JSON.stringify({ child_run_id: 'child-a', status: 'done', summary: 's' }),
  })
  pushEvent({
    type: 'child_runs_resolved',
    lastEventId: '2',
    data: JSON.stringify({ children_count: 1, terminal_statuses: { 'child-a': 'done' } }),
  })

  await Promise.resolve()
  await Promise.resolve()

  const childrenInvalidations = invalidated.filter(
    (k) => Array.isArray(k) && k[0] === 'runs' && k[1] === 'children',
  )
  expect(childrenInvalidations.length).toBeGreaterThanOrEqual(1)
})
```

Look at the existing tests in `events.spec.ts` to crib the `useEventsStore` setup, `makeFakeEventSource`, `pushEvent` helpers — match the style there exactly. **Do not invent new helpers; reuse what's already there.**

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend
npx vitest run src/stores/__tests__/events.spec.ts
```

Expected: the new tests fail because `subagent_dispatch` / `subagent_return` / `child_runs_resolved` are not in `INVALIDATING_KINDS`, and the children key is not invalidated.

- [ ] **Step 3: Extend `INVALIDATING_KINDS` and `armInvalidation`**

Edit `frontend/src/stores/events.ts`. Update the comment + the set near line 71:

```typescript
/**
 * Relay event kinds (spec §3.2) whose arrival should refresh the
 * Colada-cached run detail / run lists / children list. Pure within-iter
 * chatter (`assistant_text`, `tool_use_*`) does NOT change the run/iter
 * rows, so it is intentionally excluded — only lifecycle transitions
 * invalidate. Fanout lifecycle events (`subagent_dispatch`,
 * `subagent_return`, `child_runs_resolved`) are included so the Children
 * pane (spec.md §9.1, 9e) refetches in lockstep.
 */
const INVALIDATING_KINDS = new Set([
  'run_started',
  'iter_started',
  'iter_ended',
  'signal_emit',
  'pause_requested',
  'pause_resolved',
  'run_ended',
  'subagent_dispatch',
  'subagent_return',
  'child_runs_resolved',
])
```

Then extend `armInvalidation` (near line 202) to invalidate the children key alongside detail + list:

```typescript
function armInvalidation(): void {
  if (invalidationArmed) return
  invalidationArmed = true
  queueMicrotask(() => {
    invalidationArmed = false
    if (openRunId == null) return
    if (invalidateFn) {
      invalidateFn(['runs', 'detail', openRunId])
      invalidateFn(['runs'])
      invalidateFn(['runs', 'children', openRunId])
    }
    if (onLifecycleFn) onLifecycleFn()
  })
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend
npx vitest run src/stores/__tests__/events.spec.ts
```

Expected: all PASS, including the existing tests (the broader invalidation is additive — no existing test depends on "only two keys are invalidated").

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/events.ts frontend/src/stores/__tests__/events.spec.ts
git commit -m "$(cat <<'EOF'
Phase 9e: events store invalidates runChildren key

INVALIDATING_KINDS gains subagent_dispatch / subagent_return /
child_runs_resolved; armInvalidation also invalidates the new
['runs', 'children', runId] key so the Children pane refetches in
lockstep with each fanout lifecycle event (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8 — Frontend: `ParentRunChip.vue`

**~15 min**

**Files:**
- Create: `frontend/src/components/shared/ParentRunChip.vue`
- Create: `frontend/src/components/shared/__tests__/ParentRunChip.spec.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/shared/__tests__/ParentRunChip.spec.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import ParentRunChip from '../ParentRunChip.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
  ],
})

describe('ParentRunChip', () => {
  it('renders nothing when parentRunId is null', () => {
    const wrapper = mount(ParentRunChip, {
      props: { parentRunId: null },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="parent-run-chip"]').exists()).toBe(false)
  })

  it('renders a router-link to /runs/<parentRunId> when set', async () => {
    const wrapper = mount(ParentRunChip, {
      props: { parentRunId: 'parent-abc' },
      global: { plugins: [router] },
    })
    const link = wrapper.find('[data-testid="parent-run-chip"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/runs/parent-abc')
    expect(link.text()).toContain('parent-abc'.slice(0, 8))
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend
npx vitest run src/components/shared/__tests__/ParentRunChip.spec.ts
```

Expected: import error — `ParentRunChip.vue` does not exist.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/shared/ParentRunChip.vue`:

```vue
<script setup lang="ts">
// A small "Parent: <short-id>" chip rendered next to the status badge on a
// child run's detail view (spec.md §9.1, 9e). Closes the upward-navigation
// gap: today nothing links a child back to its parent in the UI.
//
// Renders nothing when `parentRunId` is null (the common case for top-level
// runs). When non-null, links to `/runs/<parentRunId>` via vue-router.

import { computed } from 'vue'

const props = defineProps<{ parentRunId: string | null }>()

const shortId = computed(() =>
  props.parentRunId != null ? props.parentRunId.slice(0, 8) : '',
)
</script>

<template>
  <router-link
    v-if="parentRunId != null"
    :to="{ name: 'run-detail', params: { id: parentRunId } }"
    class="parent-run-chip"
    data-testid="parent-run-chip"
  >
    Parent: {{ shortId }}
  </router-link>
</template>

<style scoped>
.parent-run-chip {
  display: inline-flex;
  align-items: center;
  padding: 0.15em 0.6em;
  border-radius: 999px;
  font-size: 0.78em;
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  font-family: var(--font-mono);
}

.parent-run-chip:hover {
  background: var(--color-bg-subtle);
}
</style>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend
npx vitest run src/components/shared/__tests__/ParentRunChip.spec.ts
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shared/ParentRunChip.vue frontend/src/components/shared/__tests__/ParentRunChip.spec.ts
git commit -m "$(cat <<'EOF'
Phase 9e: ParentRunChip component

Small upward-navigation chip rendered next to the status badge on a
child run's detail view. Conditional on parent_run_id != null. Closes
the gap where the UI had no way to navigate child → parent (spec.md
§9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9 — Frontend: `ChildrenPane.vue`

**~30 min**

**Files:**
- Create: `frontend/src/components/runs/ChildrenPane.vue`
- Create: `frontend/src/components/runs/__tests__/ChildrenPane.spec.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/runs/__tests__/ChildrenPane.spec.ts`. Crib helpers from existing run-component tests in the same dir (e.g., `ItersPane.spec.ts` if it exists, or `TimelinePane.spec.ts`) — match the style. The pane reads from two sources: the `useRunChildrenQuery` result and the `useEventsStore` list. The test mocks both.

```typescript
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { ref } from 'vue'
import ChildrenPane from '../ChildrenPane.vue'
import { useEventsStore } from '@/stores/events'

vi.mock('@/lib/queries', async () => {
  const actual = await vi.importActual<object>('@/lib/queries')
  return {
    ...actual,
    useRunChildrenQuery: vi.fn(),
  }
})

import { useRunChildrenQuery } from '@/lib/queries'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
  ],
})

function makeChildRow(overrides: Partial<{ id: string; status: string; branch: string; parent_run_id: string }> = {}): any {
  return {
    id: 'child-a',
    project_id: 1,
    prompt_id: null,
    prompt_body: 'x',
    user_id: 0,
    status: 'running',
    started_at: '2026-05-21T00:00:00Z',
    ended_at: null,
    max_iters: 1,
    iter_timeout: 60,
    worktree_path: '/wt/child-a',
    branch: 'relay/child-a',
    parent_run_id: 'parent-1',
    ...overrides,
  }
}

describe('ChildrenPane', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders nothing when there are no children', () => {
    ;(useRunChildrenQuery as any).mockReturnValue({ data: ref([]) })
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="children-pane"]').exists()).toBe(false)
  })

  it('renders one row per direct child with status badge + link + branch', () => {
    ;(useRunChildrenQuery as any).mockReturnValue({
      data: ref([
        makeChildRow({ id: 'child-a', status: 'running' }),
        makeChildRow({ id: 'child-b', status: 'done', branch: 'relay/child-b' }),
      ]),
    })
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const rows = wrapper.findAll('[data-testid^="children-row-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('child-a'.slice(0, 8))
    expect(rows[0]!.text()).toContain('running')
    expect(rows[1]!.text()).toContain('relay/child-b')
  })

  it("populates role from the events store's subagent_dispatch payload", () => {
    ;(useRunChildrenQuery as any).mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a' })]),
    })
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const store = useEventsStore()
    store._ingest([
      {
        seq: 1,
        kind: 'subagent_dispatch',
        payload: { child_run_id: 'child-a', role: 'explorer-frontend', prompt: 'x' },
      },
    ])
    return wrapper.vm.$nextTick().then(() => {
      expect(wrapper.text()).toContain('explorer-frontend')
    })
  })

  it("populates summary from the events store's subagent_return payload", () => {
    ;(useRunChildrenQuery as any).mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a', status: 'done' })]),
    })
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const store = useEventsStore()
    store._ingest([
      {
        seq: 1,
        kind: 'subagent_return',
        payload: {
          child_run_id: 'child-a',
          status: 'done',
          summary: 'audit complete: 3 findings',
        },
      },
    ])
    return wrapper.vm.$nextTick().then(() => {
      expect(wrapper.text()).toContain('audit complete: 3 findings')
    })
  })

  it('renders an empty summary for a child whose subagent_return is missing (e.g., cascade-cancelled)', () => {
    ;(useRunChildrenQuery as any).mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a', status: 'cancelled' })]),
    })
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    expect(wrapper.text()).toContain('cancelled')
    // No "(no summary)" fallback noise — the status badge tells the story.
    expect(wrapper.text()).not.toContain('(no summary)')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend
npx vitest run src/components/runs/__tests__/ChildrenPane.spec.ts
```

Expected: import error — `ChildrenPane.vue` does not exist.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/runs/ChildrenPane.vue`:

```vue
<script setup lang="ts">
// Children pane (spec.md §9.1, 9e) — lists a parent run's direct child
// runs dispatched via fanout. Conditional: renders nothing until the
// first child appears.
//
// Data sources:
//   - `useRunChildrenQuery(runId)` → the child run rows (status, branch,
//     started_at). Refetched on each fanout lifecycle event via the
//     events store's INVALIDATING_KINDS (no polling).
//   - `useEventsStore().events` → the parent's SSE stream, already in
//     memory. We read `role` from each `subagent_dispatch` event and
//     `summary` from each `subagent_return` event, keyed by
//     `child_run_id`.
//
// One row per direct child. Each row: status badge · short-id link ·
// role · branch · summary excerpt. The short id routes to `/runs/<id>`.
// Nested grandchildren are NOT rendered here — the user can navigate
// into a child and see its own Children pane if the child fanned out.

import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useRunChildrenQuery } from '@/lib/queries'
import { useEventsStore } from '@/stores/events'

const props = defineProps<{ runId: string }>()

const childrenQuery = useRunChildrenQuery(() => props.runId)
const children = computed(() => childrenQuery.data.value ?? [])

const eventsStore = useEventsStore()

/** child_run_id → role (from subagent_dispatch payload). */
const rolesByChildId = computed(() => {
  const map = new Map<string, string>()
  for (const ev of eventsStore.events) {
    if (ev.kind !== 'subagent_dispatch') continue
    const cid = ev.payload.child_run_id
    const role = ev.payload.role
    if (typeof cid === 'string' && typeof role === 'string') {
      map.set(cid, role)
    }
  }
  return map
})

/** child_run_id → summary (from subagent_return payload). */
const summariesByChildId = computed(() => {
  const map = new Map<string, string>()
  for (const ev of eventsStore.events) {
    if (ev.kind !== 'subagent_return') continue
    const cid = ev.payload.child_run_id
    const summary = ev.payload.summary
    if (typeof cid === 'string' && typeof summary === 'string') {
      map.set(cid, summary)
    }
  }
  return map
})

function shortId(id: string): string {
  return id.slice(0, 8)
}
</script>

<template>
  <section
    v-if="children.length > 0"
    class="children-pane"
    data-testid="children-pane"
  >
    <h2 class="children-pane__title">
      Children ({{ children.length }})
    </h2>
    <ul class="children-pane__list">
      <li
        v-for="child in children"
        :key="child.id"
        :data-testid="`children-row-${child.id}`"
        class="children-pane__row"
      >
        <StatusBadge :status="child.status" />
        <router-link
          :to="{ name: 'run-detail', params: { id: child.id } }"
          class="children-pane__id"
        >
          {{ shortId(child.id) }}
        </router-link>
        <span
          v-if="rolesByChildId.get(child.id)"
          class="children-pane__role"
        >
          {{ rolesByChildId.get(child.id) }}
        </span>
        <span
          v-if="child.branch"
          class="children-pane__branch"
        >
          {{ child.branch }}
        </span>
        <span
          v-if="summariesByChildId.get(child.id)"
          class="children-pane__summary"
        >
          {{ summariesByChildId.get(child.id) }}
        </span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.children-pane {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.children-pane__title {
  margin: 0.5rem 0 0;
  font-size: 1.05rem;
}

.children-pane__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.children-pane__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.children-pane__id {
  font-family: var(--font-mono);
  font-weight: 600;
  text-decoration: none;
  color: var(--color-link);
}

.children-pane__id:hover {
  text-decoration: underline;
}

.children-pane__role {
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.children-pane__branch {
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.children-pane__summary {
  font-size: 0.85em;
  color: var(--color-text);
  margin-left: auto;
  max-width: 40ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend
npx vitest run src/components/runs/__tests__/ChildrenPane.spec.ts
```

Expected: all PASS. If a test fails due to a mocking subtlety with `useRunChildrenQuery` (Pinia Colada's `useQuery` returns a complex shape), adjust the mock to match Colada's `UseQueryReturn` shape — at minimum `{ data: ref, isLoading: ref, error: ref }`. Look at the existing tests for `ItersPane`/`TimelinePane` to see how Colada-backed components are mocked there.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/ChildrenPane.vue frontend/src/components/runs/__tests__/ChildrenPane.spec.ts
git commit -m "$(cat <<'EOF'
Phase 9e: ChildrenPane component

Conditional pane (renders nothing without children) that lists a
parent run's direct children: status badge · short-id link · role ·
branch · summary excerpt. Role/summary come from the events store
(subagent_dispatch / subagent_return payloads); status/branch come
from the new run-children endpoint via Pinia Colada (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10 — Frontend: wire `ChildrenPane` + `ParentRunChip` into `RunDetailView`; broaden Cancel button

**~25 min**

**Files:**
- Modify: `frontend/src/views/RunDetailView.vue`
- Modify: `frontend/src/views/__tests__/RunDetailView.spec.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/views/__tests__/RunDetailView.spec.ts` (match the existing style — there are already tests for the pause form, the failure banner, etc.):

```typescript
it('shows the Cancel button on awaiting_children with cascade copy', async () => {
  const detail = makeDetail({ status: 'awaiting_children' })
  const wrapper = await mountRunDetail(detail, {
    children: [makeChildRow({ id: 'child-a' }), makeChildRow({ id: 'child-b' })],
  })

  const btn = wrapper.find('[data-testid="cancel-run"]')
  expect(btn.exists()).toBe(true)
  expect(btn.text()).toBe('Cancel run and 2 children')
})

it('shows "Cancel run" (no cascade copy) when running with zero children', async () => {
  const detail = makeDetail({ status: 'running' })
  const wrapper = await mountRunDetail(detail, { children: [] })

  const btn = wrapper.find('[data-testid="cancel-run"]')
  expect(btn.exists()).toBe(true)
  expect(btn.text()).toBe('Cancel run')
})

it('renders the Parent chip when detail.parent_run_id is set', async () => {
  const detail = makeDetail({ parent_run_id: 'parent-abc' })
  const wrapper = await mountRunDetail(detail, { children: [] })

  const chip = wrapper.find('[data-testid="parent-run-chip"]')
  expect(chip.exists()).toBe(true)
  expect(chip.attributes('href')).toBe('/runs/parent-abc')
})

it('omits the Parent chip on top-level runs', async () => {
  const detail = makeDetail({ parent_run_id: null })
  const wrapper = await mountRunDetail(detail, { children: [] })

  expect(wrapper.find('[data-testid="parent-run-chip"]').exists()).toBe(false)
})
```

Reuse / add the `makeDetail`, `makeChildRow`, `mountRunDetail` helpers as needed (mirror Task 9's mocking pattern for `useRunChildrenQuery`).

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend
npx vitest run src/views/__tests__/RunDetailView.spec.ts
```

Expected: the awaiting_children + parent chip cases fail.

- [ ] **Step 3: Mount `<ChildrenPane>` and `<ParentRunChip>`**

In `frontend/src/views/RunDetailView.vue`, add imports near the existing ones:

```typescript
import ChildrenPane from '@/components/runs/ChildrenPane.vue'
import ParentRunChip from '@/components/shared/ParentRunChip.vue'
import { useRunChildrenQuery } from '@/lib/queries'
```

In the `<script setup>` block, after `iters` is computed, add:

```typescript
const childrenQuery = useRunChildrenQuery(() => props.id)
const children = computed(() => childrenQuery.data.value ?? [])
const childCount = computed(() => children.value.length)
```

Update the Cancel button visibility predicate. Find:

```typescript
const isRunning = computed(() => status.value === 'running')
```

and add:

```typescript
const isCancellable = computed(
  () => status.value === 'running' || status.value === 'awaiting_children',
)
const cancelLabel = computed(() => {
  if (childCount.value === 0) return 'Cancel run'
  const n = childCount.value
  return `Cancel run and ${n} child${n === 1 ? '' : 'ren'}`
})
```

In the `<template>`, mount the parent chip in the title row (next to `<StatusBadge>`):

```vue
<div class="run-detail__title-row">
  <h1 class="run-detail__title">
    Run {{ detail.id }}
  </h1>
  <StatusBadge :status="detail.status" />
  <ParentRunChip :parent-run-id="detail.parent_run_id" />
</div>
```

Update the Cancel button to use the new predicate + label:

```vue
<div class="run-detail__actions">
  <ActionButton
    v-if="isCancellable"
    :loading="cancelling"
    data-testid="cancel-run"
    @click="onCancel"
  >
    {{ cancelLabel }}
  </ActionButton>
</div>
```

Mount `<ChildrenPane>` between the Iters pane and the Artifacts pane:

```vue
<!-- Iters pane (existing) -->
<div data-testid="iters-pane-slot">
  <ItersPane :iters="iters" />
</div>

<!-- 9e — Children pane: direct children dispatched via fanout
     (spec.md §9.1, 9e). Conditional — renders nothing on a run
     that never fanned out. -->
<ChildrenPane :run-id="detail.id" />

<!-- W7 — Artifacts pane (existing) -->
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend
npx vitest run src/views/__tests__/RunDetailView.spec.ts
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RunDetailView.vue frontend/src/views/__tests__/RunDetailView.spec.ts
git commit -m "$(cat <<'EOF'
Phase 9e: wire ChildrenPane + ParentRunChip into RunDetailView

Mounts ChildrenPane between Iters and Artifacts; mounts ParentRunChip
next to the status badge; broadens the Cancel button to status ∈
{running, awaiting_children} with cascade-aware label ("Cancel run
and N children") (spec.md §9.1, 9e).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11 — Frontend: "Show child runs" toggle in ProjectView

**~20 min**

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`
- Modify: `frontend/src/views/__tests__/ProjectView.spec.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/views/__tests__/ProjectView.spec.ts`:

```typescript
it('hides child runs by default and shows them when the toggle is checked', async () => {
  const queries = stubProjectQueries({
    runs: { value: [makeRunRow({ id: 'parent-1' })] },
  })
  const wrapper = await mountProject(queries)
  expect(wrapper.findAll('[data-testid^="runs-row-"]')).toHaveLength(1)

  // The runs query should have been called with includeChildren=false.
  expect(queries.useRunsQuery.lastCall?.includeChildren).toBe(false)

  // Toggle the checkbox.
  const checkbox = wrapper.find('[data-testid="show-children-toggle"]')
  expect(checkbox.exists()).toBe(true)
  await checkbox.setValue(true)

  // The query is now called with includeChildren=true.
  await wrapper.vm.$nextTick()
  expect(queries.useRunsQuery.lastCall?.includeChildren).toBe(true)
})
```

Cribbing the `stubProjectQueries` / `mountProject` / `makeRunRow` helpers from the existing tests there. If the existing tests don't expose `lastCall`, add a small recording wrapper around the `useRunsQuery` mock that captures `toValue(filters)` on each evaluation.

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend
npx vitest run src/views/__tests__/ProjectView.spec.ts
```

Expected: checkbox does not exist; `includeChildren` is undefined in the filter.

- [ ] **Step 3: Add the checkbox + thread `includeChildren`**

In `frontend/src/views/ProjectView.vue`, find the runs-query block:

```typescript
const runsQuery = useRunsQuery(() => ({ projectId: projectId.value }))
```

Add a local toggle state and thread it through:

```typescript
const showChildren = ref(false)
const runsQuery = useRunsQuery(() => ({
  projectId: projectId.value,
  includeChildren: showChildren.value,
}))
```

In the `<template>`, in the Runs pane header (above the runs list — look for the pane title `Runs` or the tab content), add:

```vue
<label class="project-view__runs-toggle">
  <input
    v-model="showChildren"
    type="checkbox"
    data-testid="show-children-toggle"
  />
  Show child runs
</label>
```

Add a minimal style:

```css
.project-view__runs-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85em;
  color: var(--color-text-dim);
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend
npx vitest run src/views/__tests__/ProjectView.spec.ts
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ProjectView.vue frontend/src/views/__tests__/ProjectView.spec.ts
git commit -m "$(cat <<'EOF'
Phase 9e: "Show child runs" toggle in ProjectView

A small checkbox above the Runs pane threads include_children through
useRunsQuery. Default hidden; toggle reveals child runs (spec.md §9.1,
9e). The Hub view inherits the new default automatically (it calls
useRunsQuery({ projectId, limit: 1 }) and now sees top-level runs only).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12 — Full frontend gate + a manual smoke

**~15 min**

**Files:**
- (none — verification only)

- [ ] **Step 1: Run the full frontend gate**

```
cd frontend
npm run check
```

Expected: eslint clean, `vue-tsc` clean, vitest green. Resolve any lint warnings before proceeding (the gate is `--max-warnings 0`).

- [ ] **Step 2: Run the full backend gate**

```
uv run ruff check .
uv run mypy
uv run pytest -q
```

Expected: all green.

- [ ] **Step 3: Manual scripted-harness fanout smoke**

Confirm end-to-end against a real backend + dev frontend:

1. In one shell: `uv run relay serve`.
2. In a second shell: `cd frontend && npm run dev`.
3. Open the dashboard, register a project, start a run whose prompt body emits a `[[engteam:fanout]]` payload (the simplest path is to use the `tests/orchestrator/test_fanout_integration.py` scripted-harness pattern as a guide — install the `engineering-team` skill into a scratch project, run a prompt that fans out).
4. Verify:
   - Children pane appears on the parent's run-detail view.
   - Each child row has a status badge, short-id link, role, branch.
   - Clicking a child navigates to the child's run-detail view.
   - The child's run-detail view shows the Parent chip; clicking it returns to the parent.
   - The parent's Cancel button reads "Cancel run and N children" while `awaiting_children`. Cancelling cascades through the children (status flips to cancelled across the row).
   - The Project Runs pane shows only the parent by default; toggling "Show child runs" reveals the children.
5. Write a short journal entry recording what was verified (`/journal/<YYMMDD>-phase-9e-manual-smoke.md`).

- [ ] **Step 4: Commit the journal**

```bash
git add journal/<YYMMDD>-phase-9e-manual-smoke.md
git commit -m "$(cat <<'EOF'
Phase 9e: manual smoke journal

Records the end-to-end fanout + dashboard verification done locally.
The deterministic half is covered by the test suite; this is the
journal-attested half (mirrors ADR-30 §3).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13 — Update spec.md, dashboard.md, CLAUDE.md

**~30 min**

**Files:**
- Modify: `docs/spec.md`
- Modify: `docs/dashboard.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `docs/spec.md` §7 (REST API surface)**

Find the `GET /api/runs` row and update its description to note `include_children`. Add a new row for `GET /api/runs/{run_id}/children` immediately after `GET /api/runs/{run_id}`. Match the existing table style — short, declarative, one row.

- [ ] **Step 2: Update `docs/spec.md` §9.1 (MVP views) — Run detail view**

In the Run-detail-view bulleted list:

- Add a "**Children pane**" bullet between **Iters pane** and **Artifacts pane**, describing the conditional rendering, the row shape (`status · short-id · role · branch · summary`), and the SSE-driven refresh model.
- Update the **Header** bullet to mention the **Parent chip** when `parent_run_id != null`.
- Update the **Cancel action** bullet to read "always available while `status ∈ {running, awaiting_children}`. When `awaiting_children` with N children, the label reads 'Cancel run and N children'; cancellation cascades through descendants (ADR-37, 9d)."

In the Project-view bulleted list:

- Add a sentence to the **Runs pane** bullet: "Child runs are hidden by default; a 'Show child runs' toggle reveals them (9e)."

- [ ] **Step 3: Update `docs/dashboard.md`**

Add a short subsection (one or two paragraphs) covering: the Children pane, the Parent chip, the Cancel-button cascade copy, the run-list toggle. Keep it operational — what the user sees, how it refreshes.

- [ ] **Step 4: Update `CLAUDE.md` "Current state"**

Append a 9e paragraph to the "Current state" walkthrough, mirroring the density of the 9d paragraph. Cover: the four user-visible pieces (Children pane, Parent chip, Cancel cascade copy, Show-child-runs toggle); the backend additions (`list_children`, `include_children`); the frontend additions (the two SFCs, the events-store extension); the test counts (final numbers from Task 12's gate); the absence of new ADR / new schema / new event kinds / new sentinel grammar.

- [ ] **Step 5: Run the docs gate (verify links + section anchors)**

```
grep -n "9e\|9.1\|§9" docs/spec.md | head -20
```

Eyeball the matches — the new bullets should be reachable from the table of contents at the top of §9.1.

- [ ] **Step 6: Commit**

```bash
git add docs/spec.md docs/dashboard.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Phase 9e: spec / dashboard / CLAUDE.md updates

spec.md §7 documents the new GET /api/runs/{id}/children endpoint and
the include_children param on GET /api/runs; §9.1 documents the
Children pane, the Parent chip, the cascade-aware Cancel button copy,
and the "Show child runs" toggle. dashboard.md gains the
matching operational notes. CLAUDE.md "Current state" gets a 9e
walkthrough mirroring 9d's density.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14 — Push the branch, open the PR

**~5 min**

**Files:**
- (none — git operation only)

- [ ] **Step 1: Push the branch**

```
git push -u origin phase-9e-dashboard-children
```

- [ ] **Step 2: Open the PR**

```
gh pr create --title 'Phase 9e: dashboard "Children" pane' --body "$(cat <<'EOF'
## Summary

- Backend: new `GET /api/runs/{id}/children` endpoint + `include_children` query param on `GET /api/runs` (default `false`); both are thin adapters over RelayCore (ADR-07/15).
- Frontend: new Children pane on the Run-detail view (status · short-id · role · branch · summary); new Parent chip in the header for child runs; Cancel button works on `awaiting_children` with cascade-aware copy; "Show child runs" toggle in the Project Runs pane.
- Events store invalidates `['runs', 'children', runId]` on `subagent_dispatch` / `subagent_return` / `child_runs_resolved` — SSE-driven refresh, no polling.
- No new ADR. No new schema. No new event kinds. No new sentinel grammar.

## Test plan

- [ ] Backend `uv run pytest` green (~270 passing; 3 pi-e2e still gated).
- [ ] `uv run ruff check .` and `uv run mypy` clean.
- [ ] Frontend `npm run check` green (eslint --max-warnings 0 + vue-tsc + vitest).
- [ ] Manual scripted-harness fanout smoke: Children pane appears on parent; Parent chip on child; Cancel cascades; Show-child-runs toggle reveals children. Journal attested.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Squash-merge after review**

Once approved, squash-merge into `main`. Pattern mirrors PRs #2–#5.

---

### Task 15 — Smoke-discovered SSE MIME-type fix on the 204 path

**~10 min — added 2026-05-22 after the manual smoke (journal entry `journal/260522-phase-9e-smoke.md`).**

**Files:**
- Modify: `src/relay_v2/api/events.py`
- Modify: `tests/api/test_sse.py`
- Modify: `docs/api.md`

**Background.** The manual smoke surfaced a pre-existing latent defect in `GET /api/events/{run_id}`. For a terminal run with nothing at/after `Last-Event-ID`, the handler returns `Response(status_code=204)` — FastAPI's bare 204 defaults to `Content-Type: text/plain`. Browsers' `EventSource` validates the MIME type *before* the status code, so a 204 with `text/plain` aborts the connection with `MIME type ("text/plain") that is not "text/event-stream"` instead of treating the 204 as a clean end-of-stream (per the EventSource spec).

Manifests only when a run finishes before the SSE wrapper reconnects on the empty tail — invisible until pi was unexpectedly fast on the smoke. Not a 9e regression (this code is from Phase 3) but discovered by 9e's smoke, so closed in the same PR.

- [ ] **Step 1: Strengthen the existing 204 assertion**

In `tests/api/test_sse.py::test_route_404_and_204_and_stream` (~line 327), append after the status-code assertion:

```python
                assert r.status_code == 204
                assert "text/event-stream" in r.headers["content-type"]
```

Plus a 2-3 line comment explaining why the MIME type matters on 204.

- [ ] **Step 2: Run the test, watch it fail**

```
uv run pytest tests/api/test_sse.py::test_route_404_and_204_and_stream -v
```

Expect FAIL: the 204 currently has `content-type: text/plain` per FastAPI default.

- [ ] **Step 3: Apply the one-line fix**

In `src/relay_v2/api/events.py` around line 200, change:

```python
return Response(status_code=status.HTTP_204_NO_CONTENT)
```

to:

```python
return Response(
    status_code=status.HTTP_204_NO_CONTENT,
    media_type="text/event-stream",
)
```

With a load-bearing comment above explaining the EventSource MIME-check ordering.

- [ ] **Step 4: Re-run the test + full suite**

```
uv run pytest tests/api/test_sse.py::test_route_404_and_204_and_stream -v
uv run pytest -q
```

Expect PASS + 278 total (277 + nothing-new — the existing 204 test gets a strengthened assertion, not a new test).

- [ ] **Step 5: Update `docs/api.md`**

In the SSE connect-flow paragraph (~line 56), append a sentence noting the 204 carries `Content-Type: text/event-stream` (not the FastAPI default) and why — keep it terse.

- [ ] **Step 6: Commit (on the same `phase-9e-dashboard-children` PR)**

```bash
git add src/relay_v2/api/events.py tests/api/test_sse.py docs/api.md
git commit -m "$(cat <<'EOF'
Phase 9e: SSE 204 carries text/event-stream mime

Browsers' EventSource validates Content-Type before status code, so
a bare FastAPI Response(204) (which defaults to text/plain) makes the
client abort with a MIME mismatch instead of treating the 204 as a
clean end-of-stream. Surfaces on the wrapper's reconnect path for a
short-running run whose tail is empty — exposed by the Phase 9e
manual smoke (journal/260522-phase-9e-smoke.md).

Pre-existing defect from Phase 3; closed in the same PR as 9e since
that's when it was found.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Verification commands (per phasing precedent)

Run after each backend-touching task:

```
uv run ruff check .
uv run mypy
uv run pytest -q
```

Run after each frontend-touching task:

```
cd frontend
npm run check
```

Run after the backend route lands (Task 4) to regenerate the typed client (Task 5):

```
# in shell A
uv run relay serve

# in shell B
cd frontend
npm run gen:api
```

End-to-end manual smoke (Task 12) — see Task 12 for the journaled procedure.

## Out of scope (deliberate non-goals for 9e)

- **Nested-tree rendering in the Children pane.** Direct children only; a recursive tree view is a future enhancement.
- **OTel span parenting across runs.** That's 9f.
- **Skill-side fanout guidance docs** (`skills/engineering-team/pi/references/fanout.md`). Separate follow-up PR.
- **Aggregated parent+children timeline view.** The proposal explicitly calls this out as out-of-scope-for-v1; the user navigates between parent and child detail views.
- **Per-child cancellation from the parent's Children pane.** The user navigates to the child run-detail view and uses the existing Cancel button there. Adding a per-row Cancel control here is a UX-density question better answered after the basic pane is in use.
- **A "Children" tab in the Project view** (a separate tab listing only child runs). The toggle on the Runs pane is enough.
- **Hub-view "Show child runs" toggle.** The Hub's "most recent run" card is meant to surface the top-level run; a child showing up there is wrong UX. The default-false on `include_children` is the correct fix for the Hub; no toggle is added there.

## Risks

- **`include_children=False` is a contract change to `GET /api/runs`.** Existing callers that implicitly relied on "returns everything" must be updated. The blast radius is small (the frontend's `useRunsQuery` + the test suite); pi MCP tools call `list_runs` via `RelayCore` directly and now must pass `include_children=True` if they want the full set. Audit: `grep -rn "list_runs" src/relay_v2/` — Task 2 catches the test-suite half; the MCP audit is a one-line check in Task 2 Step 4. If the MCP `relay__list_runs` tool surfaces children today (intentionally — a Claude-Code-driven user might want to see the full tree), Task 2 must update it to pass `include_children=True` and the change goes in the same commit. **Verification: this risk is closed by the broad `uv run pytest` run at the end of Task 2.**
- **Mocking Pinia Colada in component tests.** `useRunChildrenQuery` returns a `UseQueryReturn` shape with several reactive properties (`data`, `isLoading`, `error`, `refetch`, etc.). The Task 9 tests mock the minimal shape (`{ data: ref(…) }`); if the component touches more (e.g., reads `isLoading.value`), the mock must be widened. The mitigation is to keep the component narrow: it only reads `data.value`. Recorded so the implementer knows what to keep narrow.
- **Test mock for `useEventsStore`.** Task 9's tests rely on calling `store._ingest()` directly. `_ingest` is exposed on the store for exactly this reason (see `frontend/src/stores/events.ts:343`). No new test seam is required.
- **Vue-router v5's `router-link` rendering in `@vue/test-utils`.** The router stub in the test files installs the routes the link points to (`/runs/:id`); if a future route refactor renames the route name `run-detail`, all three test files need the matching update. Recorded so the implementer can grep for `'run-detail'` if a route name change lands later.

## Effort estimate

| task | minutes |
|---|---|
| 1 — `RelayCore.list_children` | 10 |
| 2 — `list_runs(include_children=…)` | 10 |
| 3 — `GET /api/runs/{id}/children` route | 15 |
| 4 — `GET /api/runs` `include_children` query param | 10 |
| 5 — regenerate typed client | 5 |
| 6 — `useRunChildrenQuery` + `RunListFilters.includeChildren` | 20 |
| 7 — events store invalidates children key | 15 |
| 8 — `ParentRunChip.vue` | 15 |
| 9 — `ChildrenPane.vue` | 30 |
| 10 — wire panes + Cancel button in `RunDetailView` | 25 |
| 11 — "Show child runs" toggle in `ProjectView` | 20 |
| 12 — full gate + manual smoke | 15 |
| 13 — spec / dashboard / CLAUDE.md updates | 30 |
| 14 — push + PR | 5 |
| **total** | **~3.5 h** |

Order-of-magnitude one focused half-day, in line with the proposal's "9e — dashboard + skill: 2 days" budget (which also covered the skill-side docs that 9e is now deferring).
