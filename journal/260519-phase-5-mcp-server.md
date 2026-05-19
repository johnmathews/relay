# Phase 5 — MCP server

**Date:** 2026-05-19
**Branch:** `eng-phase-5-mcp-server` (engineering-team cycle, FF-merged to `main`)

## What shipped

A FastMCP server mounted at `/mcp` on the existing FastAPI app
(`src/relay_v2/mcp/`), exposing the seven `spec §8` tools
(`relay__list_runs / get_run / start_run / cancel_run / pause_response /
tail_events / read_artifact`) as thin adapters over the single shared
`RelayCore`, reusing the REST `api/schemas.py` Pydantic models
(ADR-07/15). No proxying, no new core capability — pure adaptation, as
the plan scoped it. Backend-only.

Files: `src/relay_v2/mcp/{__init__,server}.py`, mounted in
`src/relay_v2/app.py`'s lifespan; `tests/mcp/{test_mcp_tools,
test_mcp_mount}.py`; docs `docs/mcp.md` +
`docs/mcp-config.example.json`; ADR-27 in `docs/decisions.md` + a
`spec §8` toolchain note; `pyproject.toml`/`uv.lock` add `mcp`.

## Key decisions (ADR-27)

1. **Bundled official SDK, not standalone `jlowin/fastmcp`.** ADR-07
   already names the FastAPI-shaped `FastMCP.streamable_http_app()`
   mount (that is the bundled SDK's API). One fast-moving dependency
   instead of two — smaller surface *is* the churn mitigation the plan
   asked for.
2. **Pin `mcp>=1.27.1,<2`.** The `<2` cap is load-bearing: the official
   repo split at v1.25.0 — `v1.x` is maintenance, `main` is v2 with a
   rearchitected transport (the surface we mount). `uv.lock` records
   `1.27.1` exactly.
3. **`tail_events` is a bounded snapshot, not an async iterator.**
   `spec §8` types it `-> AsyncIterator[Event]`, but an MCP tool result
   is a single value. Implemented as events after `since_seq` (a
   caller-advanced cursor) — same data as the SSE tail (ADR-23),
   pull-paged. Recorded as an explicit spec-vs-impl delta in ADR-27 /
   `docs/mcp.md` / the tool docstring rather than left implicit.

## Issues discovered during development (not in the plan)

1. **Mount path doubling.** `streamable_http_app()` serves at its own
   `streamable_http_path` (default `/mcp`); `app.mount("/mcp", …)` then
   yields `/mcp/mcp` → 404. Fix: build the server with
   `streamable_http_path="/"` so the sub-app serves at its root and the
   external endpoint is exactly `/mcp`.
2. **The #1367 footgun, confirmed empirically.** A sub-app mounted via
   `app.mount()` does not get its ASGI lifespan auto-run, and
   `StreamableHTTPSessionManager` starts in that lifespan. The host
   lifespan wraps its body in `async with mcp.session_manager.run():`.
   `tests/mcp/test_mcp_mount.py` is the explicit regression test (a
   missing wrap → request hangs; assertions are `timeout`-guarded so a
   hang fails fast).
3. **DNS-rebinding Host allow-list.** FastMCP 1.27 defaults to
   `enable_dns_rebinding_protection=True` with
   `allowed_hosts=['127.0.0.1:*','localhost:*','[::1]:*']` — note the
   required `:*` port. Real clients hit `127.0.0.1:7800` (matches);
   correct for the single-user localhost MVP (ADR-12), no code change.
   Tests must use a `host:port` base URL.
4. **Core stays lazily created in the lifespan.** `RelayCore.__init__`
   constructs a DB engine eagerly, so the MCP server is built/mounted
   *inside* the lifespan (where `core` exists), not at `create_app`
   time — mounting during lifespan startup is fine since Starlette
   matches routes per request.

## Verification

Gate green in the worktree: `uv run ruff check .` clean, `uv run mypy`
clean (33 source files — `relay_v2.mcp` typechecked under `--strict`),
`uv run pytest` **158 passed, 3 skipped** (was 142; +14 tool unit
tests, +2 mount integration tests; pi-e2e still gated behind
`PI_INTEGRATION=1`). Backend coverage 91% (was 92%; the 1-pt dip is
`# pragma: no cover` / defensive `OSError` branches in `server.py`,
which is 88% covered; `app.py` lifespan wiring is 100%). End-to-end
manually probed: `initialize` → `relay-v2` serverInfo + session id,
`tools/list` → all seven tools, existing `/health` and `/api/*`
unaffected.

## Follow-ups

- Next coding work is **Phase 6** (`docs/plan.md`).
- Auth remains deferred to MVP+1 (spec §8) — bearer token in the
  Streamable-HTTP headers, same path as REST, when multi-user lands.
- Dockerfile + GHCR workflow remain Phase 8 (global policy + plan).
