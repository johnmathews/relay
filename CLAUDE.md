# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Phases 0–8 are complete — the MVP is done.** Phase 0 scaffold + Phase 1 harness layer +
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
is green, eager bundle ~41 KB gz (heavy renderers lazy). Phase 5 adds
the **MCP server** (`src/relay_v2/mcp/`): a FastMCP server mounted at
`/mcp` on the same app, whose seven spec §8 tools
(`relay__list_runs/get_run/start_run/cancel_run/pause_response/
tail_events/read_artifact`) are thin adapters over the single shared
`RelayCore`, reusing the REST `api/schemas.py` Pydantic models
(ADR-07/15) — no proxying, no new core capability. Mounted in the app
lifespan with `async with mcp.session_manager.run():` (the #1367
footgun: a mounted sub-app's lifespan is not auto-run). Phase 6 adds
the **engineering-team skill port** (`skills/engineering-team/`, 11
docs) + `relay install-skill` (`src/relay_v2/cli/install_skill.py`):
the v1 skill ported faithfully with six deliberate adaptations
(single-session/no-subagent-dispatch, `.relay/runs` paths,
relay-provisioned worktree, inlined Phase-4 gate replacing
`/done`+`/merge-push`, repointed sentinel pointers, `uv run`
examples) — sentinel grammar verbatim (ADR-28). Skill+CLI only; no
orchestrator/REST/SSE/MCP contract changed. Phase 7 adds the **OTel
mirror** (`src/relay_v2/observability/`): an opt-in
`relay.run`→`relay.iter`→`relay.tool_call` span tree mirroring the
event store (ADR-10 — never a second source), exported OTLP/HTTP to
self-hosted Langfuse when `RELAY_OTEL_EXPORT=langfuse`, a strict
literal no-op (no provider/exporter/network) when `none`. GenAI/usage
attributes come from pi's verbatim `SessionEnded.messages[].usage`
(ADR-18); recovering them on the terminal-sentinel close path needed a
one-event `AssistantText` lookahead in `PiSession.events()` —
**Option D**, harness-only (ADR-04), order-preserving, deterministic,
no loop/event-store contract change (ADR-29). Phase 8 adds the
**verification & polish** layer (ADR-30): a rewritten `README.md`
(Phases 0–8; install/run/dashboard/MCP/observability/Docker); an
**additive, conditional** production frontend mount
(`src/relay_v2/api/static.py` — `mount_frontend` appends a vue-router
history-mode `StaticFiles` catch-all at `/` in the lifespan *after*
`/mcp`, a literal no-op when `frontend/dist/` is absent so dev/test is
byte-for-byte unchanged; spec §11.2); a multi-stage `Dockerfile` +
`.dockerignore` + `docker-compose.example.yml`; and
`.github/workflows/ci.yml` (full Python **and** frontend gate + GHCR
publish to `ghcr.io/johnmathews/relay-v2` on push to `main`,
`workflow_dispatch`). The Phase-8 verification split is ADR-30
(automated CI for the deterministic half — ruff/mypy/pytest + `npm run
check` + `docker build`; manual journal-attested for the real-pi e2e
demo, "image pulls and runs", "MCP from Claude Code", live-Langfuse
tree — gated like `PI_INTEGRATION=1`, mirroring ADR-24/28 §3/29).
`uv run pytest` green (**194 passed**, 3 pi-e2e gated),
`ruff`/`mypy --strict` clean (**38** source files), backend coverage
93%; `docker build` + container-boot smoke verified locally. **Two
follow-ups remain open, deliberately not closed by Phase 8** (closing
either is a contract change): the live-Langfuse-UI acceptance was
never run (manual, journal-attested when done); and the latent ADR-10
gap that `agent_end`/`SessionEnded` is never persisted as an `events`
row on the sentinel-close path (its own ADR + spec §6 change —
ADR-29/30). Operational refs: `docs/harness.md`,
`docs/orchestrator.md`, `docs/api.md`, `docs/dashboard.md`,
`docs/mcp.md`, `docs/skills.md`, `docs/observability.md` (Phase 7;
the OTel mirror + Langfuse wiring + the manual trace-tree acceptance
procedure; `docs/langfuse-compose.example.yml` is the self-host
pointer; `docs/mcp-config.example.json` is the MCP client
registration snippet; `frontend/README.md` is the dev quick-start).
Design docs (`docs/`) and the pi de-risking `scratch/` dir remain the
canonical context. New ADRs: ADR-19/20/21 (Phase 2 — orchestrator
runtime, pause/resume, async DB), ADR-22 (resume forward-progress,
pre-Phase-3 hardening), ADR-23 (SSE broadcaster + Last-Event-ID
cutover), ADR-24 (API test toolchain), ADR-25 (run-artifacts second
sandboxed root), ADR-26 (Phase-4 frontend toolchain mandates), ADR-27
(Phase-5 MCP toolchain: bundled SDK, `mcp>=1.27.1,<2` pin, lifespan
session-manager wiring), ADR-28 (Phase-6 skill port: single-session,
repo-root + wheel force-include, manual behavioral verification),
ADR-29 (Phase-7 OTel mirror: self-owned non-global TracerProvider,
deferred literal no-op, `opentelemetry-*>=1.27,<2` pins, Option-D
pi-harness lookahead so terminal-sentinel iters still recover usage,
automated span-structure tests + manual Langfuse-UI acceptance),
ADR-31 (post-MVP bugfix: a non-Cancelled exception out of the loop
or `_apply_result` is finalised as `failed` + `run_ended`
`internal_error: …` instead of leaving the run permanently
`running` — paired with an `expanduser` + existence check in
`register_project` so `~/...` no longer lurks as a literal path;
extended in the same iter with a startup-time orphan sweep in
`RelayCore.start()` and a `cancel_run` safety net that finalise any
'running' row whose owning process is gone — single-user/-process
MVP per ADR-12, so a 'running' row at startup must come from a
prior process and can never resume).

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
- MCP server (Phase 5, ADR-27): the **bundled** official SDK
  (`mcp.server.fastmcp`, dep pinned `mcp>=1.27.1,<2` — the `<2` cap is
  load-bearing, v2 rearchitects the transport), built with
  `streamable_http_path="/"` and mounted at `/mcp` in the app lifespan,
  which wraps its body in `async with mcp.session_manager.run():` (a
  mounted sub-app's ASGI lifespan is not auto-run — the #1367 footgun).
  Tools are thin `RelayCore` adapters reusing `api/schemas.py`. Tests:
  `tests/mcp/` (`test_mcp_tools.py` in-process via `FastMCP.call_tool`;
  `test_mcp_mount.py` end-to-end through the real lifespan). Ops ref:
  `docs/mcp.md`.
- OTel mirror (Phase 7, ADR-29): deps `opentelemetry-api`,
  `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, all
  pinned `>=1.27,<2` (the `<2` is *precautionary*, not load-bearing —
  OTel 2.0 doesn't exist yet; recorded honestly as such). Deliberately
  **no** `opentelemetry-semantic-conventions` dep (its GenAI module is
  unstable) — `gen_ai.*` keys are stable string literals. The mirror
  is an injected `Instrumentation` (the harness-style seam: `RelayCore(
  …, otel=)`); `none` → a literal no-op that constructs no provider/
  exporter and makes no network call; `langfuse` → a **self-owned,
  non-global** `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter)`
  to `{RELAY_LANGFUSE_HOST}/api/public/otel/v1/traces` with HTTP Basic
  `base64(public:secret)`. Span emission is threaded into the loop by
  defaulted parameter (run span in `core._run`, iter/tool spans in
  `loop`/`_drive_iter`) — additive, no control-flow change. Usage on
  the terminal-sentinel path relies on the Option-D one-event
  `AssistantText` lookahead in `harness/pi.py` `PiSession.events()`
  (harness-only, ADR-04; order-preserving; no event-store change).
  Tests: `tests/observability/test_otel_export.py` (span structure via
  `InMemorySpanExporter`, no network) +
  `tests/harness/test_pi_session_lookahead.py` (Option D, offline fake
  proc). Ops ref: `docs/observability.md`.
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
  `relay --version` (Phase 0), `relay install-skill`
  (Phase 6 — `[--project PATH] [--force] [--harness NAME]`; ADR-28,
  `docs/skills.md`). Skill source lives at
  `skills/engineering-team/<harness>/` (variant directory, default
  `pi`); the variant model is documented in ADR-33. `relay start` /
  `status` / `cancel` arrive in later phases. Default bind
  `127.0.0.1:7800`.
- Pi integration tests are gated behind `PI_INTEGRATION=1`; harness
  unit tests run offline against the captured `scratch/*.jsonl` fixtures.
  Orchestrator tests live under `tests/orchestrator/` and drive the loop
  against a scripted `Harness` double (no pi). Tests stay under
  `tests/` (`testpaths=["tests"]`), not the per-package `tests/` dirs
  plan.md sketches.
- Packaging (Phase 8, ADR-30): a multi-stage `Dockerfile` (Node stage
  builds `frontend/dist/`; `python:3.13-slim` runtime runs the
  `uv`-synced backend from `/app/.venv/bin/relay` — not `uv run`, no
  runtime cache write; healthcheck uses `urllib`, not curl) +
  `.dockerignore` + `docker-compose.example.yml` (un-vendored Langfuse
  — points at `docs/langfuse-compose.example.yml`).
  `.github/workflows/ci.yml` runs the **full** gate (Python
  `ruff`/`mypy`/`pytest` **and** `frontend/ npm run check`) and
  publishes to `ghcr.io/johnmathews/relay-v2` on push to `main` via
  `${{ github.token }}` (`workflow_dispatch` present). The prod
  frontend is served by FastAPI via the additive conditional
  `relay_v2.api.static.mount_frontend` (no-op without a build) — spec
  §11.2.
