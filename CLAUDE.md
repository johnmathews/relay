# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Phases 0–4 are complete.** Phase 0 scaffold + Phase 1 harness layer +
Phase 2 orchestrator (`RelayCore`, append-only `EventStore`, chained-iter
`run_loop`, run lifecycle, `RELAY_*` preamble). Phase 3 adds the **REST
API + persistence** (`src/relay_v2/api/`, `src/relay_v2/sse.py`): every
spec §7 endpoint (runs create/list/get/cancel/resume/events/preview; SSE
live stream; projects CRUD; sandboxed read-only file browser; versioned
prompts CRUD), Pydantic v2 schemas, and the SSE broadcaster (in-process
post-commit fan-out + DB-tail `Last-Event-ID` replay/cutover). Every
route is a thin adapter over the single shared `RelayCore` (ADR-07/15);
new capability was added as `RelayCore` service methods, not route
logic. SSE only tails the event store (ADR-10). Auto-generated OpenAPI
3.1 validated. Phase 4 adds the **Vue 3 dashboard MVP** (`frontend/`):
the primary control plane per ADR-15/spec §9 — Hub, Project view
(runs/prompts/files panes), 4-step New-Run wizard with side-effect-free
preview, Run-detail with a live SSE timeline + iters/artifacts/worktree
panes, prompts CRUD, project register/unregister. A typed
`openapi-fetch` client is generated from `/openapi.json`; Pinia Colada
is the REST cache (SSE pushes coalesce cache invalidations); the file
render pipeline is markdown-it + lazily-loaded shiki + dynamic-import
mermaid + diff2html. The Worktree pane is deliberately degraded to
read-only `worktree_path`/`branch` (live git status/diff is a named
post-MVP gap — Phase-4 scoping decision). Verified against a
scripted-harness double + the running backend; `uv run pytest` green
(142 passed, 3 pi-e2e gated behind `PI_INTEGRATION=1`), `ruff`/`mypy
--strict` clean, backend coverage 92%; the frontend gate (`npm run
check` = eslint `--max-warnings 0` + `vue-tsc` + vitest, 136 passed)
is green, eager bundle ~41 KB gz (heavy renderers lazy). The next
coding work is **Phase 5 (MCP server)** in `docs/plan.md`. Operational
refs: `docs/harness.md`, `docs/orchestrator.md`, `docs/api.md`,
`frontend/README.md`. Design docs (`docs/`) and the pi de-risking
`scratch/` dir remain the canonical context. New ADRs: ADR-19/20/21
(Phase 2 — orchestrator runtime, pause/resume, async DB), ADR-22
(resume forward-progress, pre-Phase-3 hardening), ADR-23 (SSE
broadcaster + Last-Event-ID cutover), ADR-24 (API test toolchain),
ADR-25 (run-artifacts second sandboxed root), ADR-26 (Phase-4 frontend
toolchain mandates).

## What relay v2 is

A Python service that orchestrates *chained agent sessions* against a
swappable headless harness (pi for MVP). It breaks a large plan into
work units, runs each in a **fresh** harness session, and carries state
forward via a deliberately compressed handoff. A structured SQLite event
store is the source of truth; a Vue dashboard tails it live and replays
history from it. v2 is a clean-break rewrite of v1 (`~/projects/relay`,
bash + Flask); there is no backward compatibility.

## Document authority — read these before designing or coding

The four docs in `docs/` are the canonical source. They have a
hierarchy; when they disagree, this is the precedence:

- **`docs/spec.md`** — canonical design (architecture, data model,
  harness layer, signaling, REST/MCP surface, dashboard, observability).
  Reflects current consensus and is updated as design evolves. **When
  building, this is the contract.**
- **`docs/decisions.md`** — ADR log with rationale and rejected
  alternatives. **Append-only.** Never edit or delete an existing ADR;
  superseded ADRs get a `**Status:** superseded by ADR-NN` header and a
  new ADR is added at the bottom. If you make a decision that changes
  the spec, record it as an ADR here *and* update `spec.md`.
- **`docs/motivation.md`** — why v1 must be replaced; goals, non-goals,
  hard constraints, parked risks. Consult before proposing scope changes.
- **`docs/plan.md`** — the phased (0–8) MVP build sequence with
  per-phase deliverables and verification criteria. Follow it; it is the
  execution order.

`spec.md` §13 tracks open questions (OQ-1…OQ-6); resolve them via the
de-risking evidence in `scratch/`, not by guessing.

## De-risking evidence is ground truth

`scratch/pi_derisk_workdir/findings.md` records empirically confirmed pi
behavior (run 2026-05-19). Treat it as authoritative over assumptions:

- pi authenticates via `PI_AGENT_SDK=1` (Max-subscription path) with no
  further config. Always set this env var when spawning pi.
- **No 30-second tool timeout** (a 70s Bash ran to completion) — this is
  the load-bearing finding behind choosing pi over the Claude Agent SDK.
- 11 confirmed pi event types; the `pi event → relay HarnessEvent`
  mapping in findings.md (and `spec.md` §4.2) is verified, not
  speculative. Captured event fixtures live alongside it as `*.jsonl` —
  use them for harness unit tests (Phase 1).
- pi has **no subagents at the protocol level** — relay manages
  subagents at the orchestrator layer (ADR-06).
- Confirmed pi version is **v0.74.0**; pin to it (`docs/plan.md`
  pre-phase). Note `motivation.md` mentions a newer release exists —
  pinning below current is intentional.

## Load-bearing design invariants

These are easy to violate by accident and must survive any
implementation:

- **Fresh context per iter.** `last_session_id` is intentionally always
  `None` between iters (`spec.md` §6). Pi's session resume preserves
  context; relay's entire value proposition is the *opposite* — fresh
  contexts with a compressed handoff. Resume is reserved for crash
  recovery only.
- **All writes flow through `RelayCore`.** REST routes, MCP tools, and
  the orchestrator share one in-process `RelayCore` instance and mutate
  state only through it; route handlers never touch the DB directly
  (ADR-07, ADR-15). This replaces v1's "dashboard never writes" rule.
- **Event store is the single source of truth.** Every observable
  action is an append-only `events` row (no in-place updates; status
  transitions are new events). SSE tails it; replay re-streams it; OTel
  mirrors it (ADR-10).
- **Harness isolation.** Only the `harness/` package knows about pi.
  The orchestrator sees normalized `HarnessEvent` types only (ADR-04).
- **Single-user, localhost MVP.** `user_id` FKs exist from day one but
  default to a sentinel; do not build multi-user/auth/RBAC in MVP
  (ADR-12). Many capabilities are deliberate non-goals — check
  `motivation.md` before adding scope.

## Toolchain (established in Phase 0; keep this section accurate)

- Python 3.13, dependency management via **`uv`** (not pip/poetry).
  `uv sync` to install, `uv run <cmd>` to execute. `uv.lock` is committed.
- Tests: **`pytest`** (`uv run pytest`); lint **`ruff`**
  (`uv run ruff check .`; `scratch/` is excluded — it is de-risking
  evidence, not source); types **`mypy`** strict (`uv run mypy`; package
  carries a `py.typed` marker).
- Test async convention (ADR-24): `pytest-asyncio` runs in
  `asyncio_mode = "auto"`. `tests/api/` uses bare `async def test_*` +
  `httpx.AsyncClient` over `ASGITransport`, entering the real lifespan
  via `app.router.lifespan_context`; `tests/orchestrator/` and
  `tests/harness/` keep the explicit `asyncio.run()` wrapper pattern
  (sync `def test_*`) — both coexist under the one `auto` config.
  `create_app(settings, *, harness=)` is a scripted-harness injection
  seam (mirrors the `settings` seam) so API tests never spawn pi.
  `openapi-spec-validator` (dev-dep) asserts `/openapi.json` is valid
  OpenAPI v3.
- Schema management is hand-rolled `create_all` for the MVP (ADR-17);
  `src/relay_v2/db/migrations/` is a placeholder for future numbered
  upgrade scripts. Alembic is deferred.
- Two DB engines, both behind `relay_v2.db` (ADR-21): a **sync** engine
  for `create_all` schema bootstrap only; an **async** `aiosqlite`
  engine (deps `aiosqlite`, `sqlalchemy[asyncio]` → `greenlet`) for all
  orchestrator I/O. Nothing above `relay_v2.db` constructs an engine.
- Backend: FastAPI + Pydantic v2 + Uvicorn; SQLite via SQLAlchemy.
- Frontend (`frontend/`, Phase 4): Vue 3 + vue-router **v5** + Pinia +
  Pinia Colada + Vite, TypeScript strict. Typed API client generated
  by `openapi-typescript` 7 + `openapi-fetch` off the running backend's
  `/openapi.json` (`npm run gen:api`; backend must be up). Render
  pipeline: markdown-it (+footnote/task-list, `html:false`), shiki
  (`createHighlighterCore` + JS regex engine + lazily-imported
  grammars — never the convenience bundle), mermaid (dynamic
  `import()` only), diff2html. Gate: `npm run check` = `eslint
  --max-warnings 0` + `vue-tsc` + `vitest` (jsdom, v8 coverage —
  vitest 4 has no `coverage.all` toggle, scope via `coverage.include`).
  Vite dev-proxies `/api` → `:7800` with a long `proxyTimeout` and
  no SSE buffering. Rationale + the five toolchain mandates: **ADR-26**;
  `frontend/README.md` has the operational notes. The full gate is
  Python (`ruff`/`mypy`/`pytest`) **and** the frontend `npm run check`.
- Console script: `relay`. Implemented today: `relay serve`,
  `relay --version` (Phase 0 subset). `relay start` / `status` /
  `cancel` / `install-skill` arrive in Phase 3+. Default bind
  `127.0.0.1:7800`.
- Pi integration tests are gated behind `PI_INTEGRATION=1`; harness
  unit tests run offline against the captured `scratch/*.jsonl` fixtures.
  Orchestrator tests live under `tests/orchestrator/` and drive the loop
  against a scripted `Harness` double (no pi). Tests stay under
  `tests/` (`testpaths=["tests"]`), not the per-package `tests/` dirs
  plan.md sketches.
- A `Dockerfile`/compose plus a GitHub Actions workflow publishing to
  `ghcr.io/johnmathews/relay-v2` are required (Phase 8 + global policy).
