# 260522 — Phase 9e: dashboard Children pane

Dashboard leg of the fanout-join arc (9a–9f). Branch
`phase-9e-dashboard-children` off `4a910b4` (9d). PR #6 open.

## What shipped (Tasks 1–14)

- **Tasks 1–4 — backend.** `RelayCore.list_children(run_id)` (direct
  children, no recursion; 404 on unknown parent), `GET
  /api/runs/{run_id}/children` REST endpoint, `include_children` query
  param on `GET /api/runs` (default false — top-level only), and the
  MCP `relay__list_runs` tool updated to always pass
  `include_children=True` internally. Thin-adapter pattern throughout —
  each task was one core method + one route adapter or one param
  threaded through an existing path. No schema change, no new event
  kinds.
- **Tasks 5–7 — frontend API + store.** `openapi-typescript` client
  regenerated to pick up the two new endpoints; Pinia Colada query for
  `useRunChildren` (`GET /api/runs/{id}/children`); `INVALIDATING_KINDS`
  in the SSE store extended with the new fanout-relevant event kinds
  (`subagent_dispatch`, `subagent_return`, `child_runs_resolved`) so the
  children query invalidates on relevant SSE events. The invalidation
  extension was a one-liner addition to the existing set.
- **Tasks 8–9 — ChildrenPane + StatusBadge variant wiring.** New
  `ChildrenPane.vue`: renders the children list query, shows a spinner
  on loading, an empty-state message on `[]`, and a compact run-row per
  child (status badge, id, role/prompt fragment, started/ended
  timestamps). `StatusBadge.vue` already gained an amber
  `awaiting_children` variant in 9a; wired it here to the pane.
- **Task 10 — Parent chip.** Thin `ParentChip.vue` component shown at
  the top of `RunDetailView.vue` when `run.parent_run_id` is set — a
  chip linking back to the parent's detail view. Two states: resolved
  (parent run fetched, shows status badge + id) and skeleton while
  loading.
- **Task 11 — Show-child-runs toggle.** Toggle in `RunListView.vue`
  (the Hub / Project view run list) — persisted in Pinia store, passed
  as `include_children` to the `useRuns` query. Children show indented
  under their parent when enabled.
- **Tasks 12–13 — cancel cascade copy + doc accuracy pass.** Cancel
  confirmation dialog in `RunDetailView.vue` notes that cancelling an
  `awaiting_children` parent also cancels its children (wired in 9d;
  this is copy, not new logic). `spec.md`, `docs/dashboard.md`, and
  `CLAUDE.md` updated to reflect 9e.
- **Task 14 — gate.** `uv run pytest` (276 passed, 3 skipped),
  `ruff`/`mypy --strict` clean, `npm run check` (155 frontend tests
  passed), lint + typecheck clean.

## Plan-doc-driven workflow

All 14 tasks were executed via the `superpowers:subagent-driven-development`
skill against `docs/plans/2026-05-21-fanout-join-9e.md`. Two-stage
review: spec alignment first (does this match §7/§9 intent?), then code
quality (ruff/mypy/vitest per-task). The spec doc pre-answered most
design questions; the only real judgment calls were CSS variable
substitution (see surprises below) and the toggle persistence choice
(Pinia store vs. URL param — chose store per existing pattern).

## What went smoothly

The thin-adapter backend pattern (established in Phases 3–5) made
Tasks 1–4 genuinely trivial — each was ~20 lines of new code: one
`RelayCore` method, one route, one param threading. The existing
`ScriptedHarness`/`_client_with_core` API test infrastructure meant
the unit tests for `list_children` and `include_children` were
straightforward ports of the existing children tests.

SSE invalidation (Task 6) was a one-liner because the INVALIDATING_KINDS
pattern was already established in Phase 4 — just added the three new
event kinds.

## Surprises

**SQLite 1-second timestamp granularity.** The Task 1 ordering test
for `list_children` (`children ordered by created_at asc`) was vacuous
with fresh rows — `current_timestamp` has 1-second granularity so two
rows inserted in the same second get identical timestamps and the order
was non-deterministic. Fixed by backdating timestamps (seeding child B
one second before child A, then asserting reversed insertion yields
correct order). The test file `tests/orchestrator/test_relay_core.py`
also didn't exist yet — it needed creating from scratch for the unit
test.

**`vi.hoisted` scoping subtlety.** Task 11's `RunListView` unit test
needed to mock `useRuns` and `useRunChildren`. `vi.hoisted` runs before
module imports, so the mock factory can't close over a `const` declared
in the test body — it gets `undefined`. Required a module-level capture
variable that the hoisted factory could reference; test bodies then
assign into it before `mount`. Standard vitest pattern but easy to miss.

**Missing CSS variables.** The plan doc referenced `--color-link` and
`--color-bg-subtle` for the Parent chip styling. Neither variable exists
in the design-system tokens; substituted with `--color-accent` (link
color) and `--color-surface` (subtle background) which are both defined
and used elsewhere. No visual regression — the variables landed in the
right semantic bucket.

## Test counts

- **Backend:** 266 → 276 (+10): `test_relay_core.py` (4 unit tests for
  `list_children` + `include_children`), `test_runs.py` (5 REST
  integration tests for the children endpoint and include_children
  param), `test_sse.py` (+1 invalidation kinds regression).
- **Frontend:** 142 → 155 (+13): `ChildrenPane.spec.ts` (4),
  `ParentChip.spec.ts` (3), `RunListView.spec.ts` (4 inc. toggle
  persistence), `StatusBadge.spec.ts` (+1 awaiting_children variant
  wiring), `stores/runs.spec.ts` (+1 include_children query param).

## Outstanding

**Manual scripted-harness fanout smoke** — a real browser session
exercising the full fanout path against the engineering-team skill —
is journal-attested per ADR-30 and has NOT been run this session. It
remains required before merge:

1. Start relay, register a project with the engineering-team skill.
2. Submit a prompt that triggers the `[[engteam:fanout]]` sentinel
   (two-child scenario).
3. In the dashboard: confirm the Children pane populates on the parent
   run, the Parent chip appears on each child's detail view, the
   Show-child-runs toggle includes children in the run list, and the
   cancel confirmation notes the cascade.
4. Attest in this journal (or a follow-up entry) with a timestamp.

## References

- Plan doc: `docs/plans/2026-05-21-fanout-join-9e.md`
- PR: #6 (open, in review)
- ADRs covering the backend: ADR-34 (9a), ADR-35 (9b), ADR-36 (9c),
  ADR-37 (9d). No new ADR for 9e — all decisions are in the plan doc
  and the existing frontend ADR-26 toolchain is unchanged.
