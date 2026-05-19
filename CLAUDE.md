# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Phases 0–2 are complete.** On top of the Phase 0 scaffold and the
Phase 1 harness layer, Phase 2 adds the orchestrator: `RelayCore` (the
single shared service + queue/supervisor runtime, wired into the app
lifespan), the append-only `EventStore`, the chained-iter `run_loop`
(spec.md §6), run lifecycle (start/cancel/pause/resume), and the
`RELAY_*` preamble builder. Verified end-to-end against a scripted
harness double (`tests/orchestrator/`); `uv run pytest` is green
(pi e2e gated behind `PI_INTEGRATION=1`), `ruff`/`mypy --strict` clean.
The next coding work is **Phase 3 (REST API + persistence)** in
`docs/plan.md`. Operational refs: `docs/harness.md`, `docs/orchestrator.md`.
Design docs (`docs/`) and the pi de-risking `scratch/` directory remain
the canonical context. New ADRs this phase: ADR-19 (orchestrator
runtime), ADR-20 (pause/resume persistence), ADR-21 (async DB engine).

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
- Schema management is hand-rolled `create_all` for the MVP (ADR-17);
  `src/relay_v2/db/migrations/` is a placeholder for future numbered
  upgrade scripts. Alembic is deferred.
- Two DB engines, both behind `relay_v2.db` (ADR-21): a **sync** engine
  for `create_all` schema bootstrap only; an **async** `aiosqlite`
  engine (deps `aiosqlite`, `sqlalchemy[asyncio]` → `greenlet`) for all
  orchestrator I/O. Nothing above `relay_v2.db` constructs an engine.
- Backend: FastAPI + Pydantic v2 + Uvicorn; SQLite via SQLAlchemy.
- Frontend: Vue 3 + Pinia + Pinia Colada + Vite, in `frontend/`.
- Console script: `relay` (`relay serve`, `relay start`, `relay status`,
  `relay cancel`, `relay install-skill`). Default bind `127.0.0.1:7800`.
- Pi integration tests are gated behind `PI_INTEGRATION=1`; harness
  unit tests run offline against the captured `scratch/*.jsonl` fixtures.
  Orchestrator tests live under `tests/orchestrator/` and drive the loop
  against a scripted `Harness` double (no pi). Tests stay under
  `tests/` (`testpaths=["tests"]`), not the per-package `tests/` dirs
  plan.md sketches.
- A `Dockerfile`/compose plus a GitHub Actions workflow publishing to
  `ghcr.io/johnmathews/relay-v2` are required (Phase 8 + global policy).
