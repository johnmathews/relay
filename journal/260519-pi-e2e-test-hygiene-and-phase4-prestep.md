# 2026-05-19 — Test hygiene, real-pi e2e, Phase 4 scoping + backend pre-step

Follows the Phase 3 ship (`260519-phase-3-rest-api.md`). Three pieces of
work, each in its own worktree → independent review → FF merge → push:

## 1. Eliminate all test `ResourceWarning`s (`ea0d176`)

The throwaway sync read-back engines (`_read` in test_loop /
test_service_methods), the async-engine helpers (`_store` / `_sm` in
test_events / test_lifecycle), and the schema-inspect engine in
test_smoke created SQLAlchemy/aiosqlite connections that were never
disposed — ~19 `ResourceWarning`s ("unclosed database", "aiosqlite
Connection deleted before being closed") on every run.

Fix: `_read` → `@contextmanager` disposing the sync engine; `_store` /
`_sm` → `@asynccontextmanager` that disposes the sync `init_db`
bootstrap engine immediately and the async engine in `finally` *inside
the caller's event loop* (aiosqlite connections bind to the loop that
created them — disposing in a second `asyncio.run` would not close
loop-1's connections). test_smoke disposes its inspect engine. Pure
test-infra hygiene, no production code, no behaviour change. Result:
**0 warnings** (was 19); suite unchanged at 138 passed.

## 2. Real-pi e2e for orchestrator + REST (`8f406fb`)

The Phase 1 harness already had a gated live-pi e2e; the overdue Phase 2
follow-up ("validate `run_loop` against real pi before more is built on
it") was never done, and Phase 3 then stacked REST on top. Closed both
with `tests/orchestrator/test_pi_e2e.py` (skipped unless
`PI_INTEGRATION=1`, same gate as the harness e2e — pi never spawns in
normal runs):

- `test_orchestrator_drives_real_pi_to_done`: `RelayCore.run_loop`
  drives a live pi v0.74.0 session to a clean `done`; run row +
  `run_started`..`run_ended` asserted.
- `test_rest_start_run_real_pi_completes`: `POST /api/runs` with the
  production `PiHarness` (no scripted double) reaches `done`; the
  events endpoint shows the run correctly bracketed and seq-ordered.

Verified live: pi is installed at exactly the pinned **v0.74.0**; both
pass. The full REST → RelayCore → run_loop → PiHarness → pi →
EventStore path is now validated end-to-end against real pi. Normal
suite: 138 passed, now 3 skipped (the 2 new gated tests + the harness
one).

## 3. Phase 4 scoping (discussion) + backend pre-step (`d10d86a`)

**Discussion workflow** (engineering-team) scoped Phase 4 (Vue dashboard
MVP) before any code. Research: a backend gap-analysis (spec §9 panes →
Phase 3 endpoints) and a live frontend-toolchain currency review.
Decision record:
`.engineering-team/runs/manual-20260519T145208Z/discussions/260519-phase-4-dashboard-scope.md`.

Findings: almost every spec §9 pane is already backed by Phase 3;
**one hard gap** — the Artifacts pane browses `data_dir/runs/<run_id>/`,
which is disjoint from the project file browser's sandbox
(`project.root_path`) and had no endpoint. Toolchain: the plan.md stack
stands (Pinia Colada 1.3 and openapi-typescript 7 risks both cleared);
five implementation mandates recorded for the build (vue-router v5,
shiki fine-grained bundle, mermaid lazy import, Vite SSE dev-proxy
config, diff2html maintenance/​vitest v4 coverage). Worktree pane's live
git status/diff is a deliberate post-MVP gap (degrade to read-only
`worktree_path`/`branch`).

**Backend pre-step (ADR-25)** — closed the one hard gap:
`GET /api/runs/{id}/artifacts[/*]`, sandbox root derived server-side as
`settings.data_dir/runs/<run_id>`, run must exist (`get_run` → 404) and
its artifacts dir must exist (→ 404). The audited
`resolve_within_sandbox` is reused unchanged, and the listing/content
bodies were extracted into shared `serve_listing`/`serve_file` helpers
that *both* the project file browser and the artifacts browser call —
one audited confinement function, one serving path, two trust roots
(proactively addresses the Phase 3 review's duplicate-logic class of
finding). Independent review confirmed the refactor is behaviorally
identical (test_files.py is the regression guard) and the `run_id`
segment cannot traverse (DB existence check first + sandbox as defense
in depth). decisions.md kept append-only; spec §7/§9 additive notes;
artifacts routes added to the OpenAPI validity test.

## State

`origin/main` = `d10d86a`. `uv run pytest` → **142 passed, 3 skipped**
(pi e2e gated behind `PI_INTEGRATION=1`); ruff + mypy --strict clean
(31 source files); coverage **92%**. Phases 0–3 shipped; Phase 4
(dashboard MVP) is scoped, backend-unblocked, and ready to build
vertical-slice-first per the captured decision doc — that is the next
work.
