# 2026-05-19 — Phase 3: REST API + persistence

Built Phase 3 per `docs/plan.md`: the full spec §7 REST surface, Pydantic
v2 schemas, and the SSE broadcaster. Done in an isolated worktree
(`eng-relay-v2-phase-3-rest-api`), fast-forward merged to `main`. Driven
through the engineering-team skill as a focused build: `docs/plan.md`
Phase 3 as the plan, subagent research + parallel implementation, an
independent code-review pass, gated on plan.md Phase 3 verification.

## What landed

Five work units (`.engineering-team/runs/manual-20260519T132150Z/improvement-plan.md`):

- **W1 — RelayCore service methods.** Every new read/write capability is
  a `RelayCore` method, not route logic (ADR-07/15): projects
  list/get/delete (unregister-only, never deletes disk files); versioned
  prompts create/list(latest-per-name)/get/update(snapshot
  bump)/delete(all versions)/list_versions; `list_events`
  (after_seq/limit/offset, delegated to a new `EventStore.list_events`
  so the event store stays the log authority — ADR-10); `list_iters`;
  and `preview_run` — pure, zero side effects (no run row, event, or
  dir; placeholder `<preview>` run_dir never created).
- **W2 — schemas + runs/projects/prompts routers.** `src/relay_v2/api/`
  with Pydantic v2 request/response models, thin routers that resolve
  the shared `RelayCore` via `deps.get_core` and map `ValueError` →
  HTTP (409 state-conflict, 404 unknown, 400 bad-request). Added a
  `create_app(settings, *, harness=)` test-isolation seam mirroring the
  existing `settings` seam so the API suite never spawns pi.
- **W3 — sandboxed read-only file browser.** All confinement in one
  audited `resolve_within_sandbox`: rejects NUL byte, absolute paths,
  lexical `..`, and symlink-escape (resolve + `is_relative_to`) → 400;
  binary (NUL in first 8 KiB) → 415; >5 MiB → 413; non-existence → 404
  (so a probe can't distinguish blocked vs absent). No write/delete
  route exists. Negative tests cover `../`, `%2e%2e%2f`, `/etc/passwd`,
  and an in-sandbox symlink to outside.
- **W4 — SSE broadcaster (ADR-23).** `src/relay_v2/sse.py`: per-run
  bounded subscriber queues; `publish` is non-blocking (`put_nowait`);
  hooked as a passive post-commit observer at the single
  `EventStore.append` chokepoint (after commit, guarded, never alters
  seq or the loop — ADR-10/ADR-04 intact). `GET /api/events/{run_id}`:
  subscribe-before-replay, then drain forwarding only
  `seq > max_replayed_seq` → gap-free and duplicate-free across
  `Last-Event-ID` reconnects; finished run streams paginated history
  then EOF, 204 only when nothing at/after `Last-Event-ID`;
  `X-Accel-Buffering: no` for nginx.
- **W5 — wiring, OpenAPI, toolchain, gate.** All routers mounted via
  `include_api_routers` from `create_app`; `asyncio_mode = "auto"`;
  `openapi-spec-validator` dev-dep + a test asserting valid OpenAPI v3
  with every spec §7 path and resource tags; `docs/api.md`; CLAUDE.md
  current-state/toolchain refresh.

## Decisions (ADR-23, ADR-24)

**ADR-23 — SSE broadcaster + Last-Event-ID cutover.** The broadcaster is
a passive post-commit observer at `EventStore.append`; ADR-10 holds (SSE
never writes, never assigns seq). Slow-consumer policy: bounded queue,
**close-on-full** (evict oldest + `CLOSED` sentinel) — a clean close the
client recovers from via `Last-Event-ID` replay beats a silent
unrecoverable gap. Subscribe-before-replay + `seq > max_replayed_seq`
cutover gives no-gap/no-dup. Finished run = paginated history then EOF;
real 204 only when empty (a `StreamingResponse` can't 204 mid-stream).

**ADR-24 — API test toolchain.** `pytest-asyncio` `asyncio_mode="auto"`
+ `httpx.AsyncClient` over `ASGITransport`, entering the real lifespan
via `app.router.lifespan_context`. Backward compatible: the Phase 1/2
suites use `asyncio.run()` inside sync `def test_*`, which pytest-asyncio
ignores — verified green after the switch. `openapi-spec-validator`
added so OpenAPI validity is asserted, not eyeballed.

`decisions.md` kept append-only — only ADR-23/24 appended, ADRs ≤22
untouched. `spec.md` §7 gained an additive ADR-23 pointer (no rewrite).

## Preview spec-ambiguity resolution

`spec.md` §7 nests preview under `/api/runs/:id/preview`, but the
New-Run wizard previews a run that does not exist yet. Resolved: the
path segment is the **project id**; the prospective prompt comes via
`prompt_body`/`prompt_id` query params. No `RelayCore` contract change;
documented in `docs/api.md`.

## Code review

An independent `feature-dev:code-reviewer` pass confirmed the sandbox is
solid and ADR-07/10/12, preview-no-side-effects, prompt versioning, and
the SSE cutover are correct. Five findings, all fixed: (1) double-mount
of routers in `test_w2_routes.py` (create_app already mounts); (2)
ADR-24 referenced but not yet recorded → appended; (3) duplicate inline
`_get_core` in files.py/events.py → unified onto `deps.get_core` (also
fixed an `Any` return); (4) deleted-project resume → 404 mapping now
documented in `deps.py`; (5) SSE lingered up to 15 s after a run
finished → close immediately on the `run_ended` event (it is always the
last event a run appends, spec §3.2).

## Verification

- `uv run pytest` → **138 passed, 1 skipped** (the skip is the
  `PI_INTEGRATION=1`-gated pi e2e; pi never spawned). Phase 1 harness +
  Phase 2 orchestrator suites stayed green.
- Every plan.md Phase 3 criterion: httpx.AsyncClient happy-path per
  endpoint; file browser refuses `../`//etc/passwd//symlink-out (400);
  binary → 415, markdown → 200; SSE order + `Last-Event-ID` reconnect
  no-gap/no-dup; finished-run paginated-then-close; `/openapi.json`
  valid OpenAPI 3.1 (openapi-spec-validator).
- `uv run ruff check .` clean; `uv run mypy` (strict) clean — 30 source
  files. Coverage **91%** (held from the pre-Phase-3 baseline while
  adding ~575 statements); `htmlcov/` generated.

## Wrap-up housekeeping

- `.gitignore` now excludes `.claude/`/`.engineering-team/` (run
  artifacts) and the standard secret patterns (`*.key`, `*.pem`,
  `*.secret`, `credentials.json`). No secrets in the changeset or
  history.
- Pre-existing benign `ResourceWarning: unclosed database` in the
  Phase 1/2 `tests/orchestrator/` throwaway-sync-engine pattern — not
  introduced by Phase 3, out of scope here. Follow-up candidate.
- This push also publishes the previously-unpushed pre-Phase-3
  hardening commits (`4001055`, `64b8c49`, `5b7f581`) — `origin/main`
  was behind at `8329ee5` (Phase 2).

## Follow-ups (out of Phase 3 scope)

- Phase 4 — dashboard MVP (Vue 3 + Pinia) consumes this REST surface +
  the SSE feed and the typed OpenAPI client.
- Phase 5 — MCP server (`/mcp`) over the same `RelayCore`.
- Tidy the orchestrator-test sync-engine `ResourceWarning`s.
- Docker + GitHub Actions → `ghcr.io/johnmathews/relay-v2` is Phase 8.
