# 260519 — Phase 8: verification & polish (MVP complete)

Final MVP phase. Additive/polish only — no orchestrator / REST / SSE /
MCP / observability contract changed. Branch
`worktree-eng-phase-8-polish` off `291853e`.

## What shipped (W1–W5)

- **W1 — README.md rewrite.** Was stale ("Phases 0–2 complete").
  Rewritten for Phases 0–8: install (`uv sync`), run (`uv run relay
  serve`), dashboard at `http://127.0.0.1:7800/`, MCP client setup
  (cross-links `docs/mcp-config.example.json` + `docs/mcp.md`),
  observability (cross-links `docs/observability.md` +
  `docs/langfuse-compose.example.yml`), Docker quickstart. ADR count
  corrected (30).
- **W2 — conditional prod static-serving.** spec §11.2 mandated the
  built SPA be served by FastAPI but no mount existed. New
  `src/relay_v2/api/static.py`: `frontend_dist_dir()` (packaged →
  repo-root resolution, mirrors `install_skill.skill_source_dir`),
  `_SpaStaticFiles` (vue-router history-mode fallback: an
  extension-less 404 → `index.html`; a missing *asset* still 404s so
  a broken ref isn't masked), `mount_frontend(app)`. `app.py` calls it
  in the lifespan **after** `/mcp`, so the catch-all is last in
  Starlette's registration order and never shadows `/health`, the REST
  routers, `/openapi.json` or `/mcp`. **No-op + `False` when
  `frontend/dist/` is absent** → the entire test tree and any un-built
  checkout are byte-for-byte unchanged. New
  `tests/api/test_static_frontend.py` (2 tests: the no-op regime and
  the built-frontend regime incl. SPA fallback + non-shadowing).
- **W3 — Docker.** Multi-stage `Dockerfile`: `node:22-slim` builds
  `frontend/dist/`; `python:3.13-slim` runtime, `uv` from the official
  image, `uv sync --frozen --no-dev`, repo layout preserved
  (`/app/frontend/dist`, where `frontend_dist_dir()` looks), non-root
  uid 10001, `urllib` healthcheck (python:slim has no curl — the
  documented healthcheck-validation rule). `.dockerignore`.
  `docker-compose.example.yml` wires `RELAY_*` (Langfuse vars
  commented) and points at `docs/langfuse-compose.example.yml` for the
  un-vendored Langfuse stack.
- **W4 — `.github/workflows/ci.yml`.** `gate` job: `uv sync --frozen`
  → ruff → mypy → pytest (pi-e2e stays gated, `PI_INTEGRATION` unset)
  → Node 22 → `npm ci` + `npm run check` in `frontend/`. `docker` job
  (`needs: gate`, push-to-main only): GHCR login with
  `${{ github.token }}`, build & push
  `ghcr.io/johnmathews/relay-v2:{latest,sha}`, `packages: write`.
  `workflow_dispatch` present. Only trusted contexts interpolated.
- **W5 — ADR-30 + doc accuracy pass.** ADR-30 appended (append-only)
  mirroring ADR-28 §3 / ADR-29's automated-vs-manual format. spec
  §11.2 + §11.3 Phase-8 implementation/accuracy notes; `docs/plan.md`
  Phase 8 marked complete; `CLAUDE.md` "Current state" + toolchain
  updated to Phases 0–8.

## Key decisions

1. **CI vs. manual split → ADR-30.** Resolved exactly per the
   established ADR-24 / ADR-28 §3 / ADR-29 precedent (the early
   judgment call the brief delegated). Deterministic/offline →
   automated CI (ruff/mypy/pytest + `npm run check` + `docker build`).
   Real-pi / live-Langfuse / live-Claude-Code → manual,
   journal-attested, gated like `PI_INTEGRATION=1`. Recorded as an ADR
   because it shapes ci.yml's scope.
2. **Prod static mount is conditional, not always-on.** An
   unconditional mount would add a `/` route and alter 404s in
   dev/test — a contract change. Conditional + last-mounted keeps it
   provably additive (the 192 pre-Phase-8 tests are untouched; only
   2 net-new tests).
3. **Wrap-up followed the user's explicit sequence (= ADR-28 §4
   inlined gate), not `/done`.** ADR-28 §4 already established that
   this project inlines the Phase-4 gate because `/done`/`/merge-push`
   are Claude Code slash-skills that don't fit the pi/relay model.
   The user's pre-approved "gate → journal → FF-merge → ask before
   push" coincides with that. The inlined-gate equivalent was run in
   full: Python gate + frontend gate + security sanity + journal +
   FF-merge + ask-before-push.
4. **Container runs `/app/.venv/bin/relay`, not `uv run`.** First
   image attempt used `uv run relay serve`; it tried to write
   `~/.cache/uv` at container start and failed on the constrained
   Docker VM. Switched to the venv binary on `PATH` — no runtime uv
   resolution/cache; the environment is fully materialised at build.

## Verification

- **Automated (this session, deterministic):** `uv run pytest` →
  **194 passed, 3 skipped** (192 baseline + 2 new; existing 192
  unchanged → additive proven). `uv run ruff check .` clean.
  `uv run mypy` clean, **38** source files. Backend coverage 93%.
  Frontend `npm run check` → **136 passed**, lint + typecheck clean
  (run in clean `node:22` — the host's Homebrew Node 25 is broken
  with a `libsimdjson` dyld error, an environment issue unrelated to
  the code; no frontend source changed this branch).
- **Docker (this session, deterministic):** `docker build .`
  succeeds (in-image `npm run build` + `uv sync`). Container booted
  and smoke-tested: `/health` → `{"status":"ok"}`, `/` serves the
  real built SPA shell, `/projects/x` SPA-fallback → 200,
  `/openapi.json` → 200 (API not shadowed). Proves W2+W3 together.
- **Manual, owner-run, journal-attested (NOT done this session — the
  ADR-30 manual half):**
  1. End-to-end `relay start eng-team-demo.md` against the v1 fixture
     with real pi (Max sub) — dashboard shows the full run; MCP
     callable from Claude Code. Procedure: `docs/skills.md`.
  2. Live-Langfuse-UI trace-tree acceptance (carried from Phase 7,
     ADR-29 §verification-2 — still never run). Procedure:
     `docs/observability.md`.
  3. "Image pulls and runs" from the *published* GHCR image (after
     the first push-to-main CI run publishes it).

## Open follow-ups (deliberately NOT closed by Phase 8)

Closing either would be a contract change, out of scope for
verification & polish:

1. **Live-Langfuse-UI acceptance** — manual, journal-attested when
   performed (ADR-29 §verification-2).
2. **Latent ADR-10 gap:** `agent_end`/`SessionEnded` is never
   persisted as an `events` row on the sentinel-close path. ADR-29
   explicitly fences this as "C's territory — its own ADR + `spec.md`
   §6 change." Phase 8 neither widens nor closes it.

Plus a recorded accuracy note (spec §11.3): `relay start/status/cancel`
are a *target* CLI surface; only `serve`/`--version`/`install-skill`
are implemented. MVP run management is via dashboard/REST/MCP. README
and spec §11.3 now state this explicitly rather than implying a CLI
that doesn't exist.

## Status

MVP complete. Every `docs/plan.md` "what done with MVP looks like"
bullet is satisfied except the three owner-run manual acceptances
above, which are documented procedures, not code work.
