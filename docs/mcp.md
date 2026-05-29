# MCP server (Phase 5)

Operational reference for the relay MCP server. Design contract:
`docs/spec.md §8`; toolchain rationale: **ADR-27**. The server is a
thin adapter layer — for *why* it exists and how it fits the
architecture, read those; this doc is the how-to.

## What it is

A [FastMCP](https://modelcontextprotocol.io) server mounted at `/mcp`
on the same FastAPI app as the REST API. Its seven tools are thin
adapters over the single in-process `RelayCore` — the exact service
layer the REST routes use (ADR-07/15). No proxying, no separate
process, no new core capability. External MCP clients (Claude Code,
Claude Desktop) can drive relay runs through it.

## Tools (spec §8)

| Tool | Signature | RelayCore call |
|---|---|---|
| `relay__list_runs` | `(project_root?: str) -> list[Run]` | `list_runs(..., include_children=True)` (path → `project_id`) |
| `relay__get_run` | `(run_id: str) -> Run` (with iters) | `get_run` + `list_iters` |
| `relay__start_run` | `(project_root: str, prompt: str, max_iters?: int) -> Run` | `start_run` (path → `project_id`) |
| `relay__cancel_run` | `(run_id: str) -> Run` | `cancel_run` |
| `relay__pause_response` | `(run_id: str, answer: str) -> Run` | `resume_run` |
| `relay__tail_events` | `(run_id: str, since_seq?: int) -> list[Event]` | `list_events(after_seq=)` |
| `relay__read_artifact` | `(run_id: str, path: str) -> str` | sandboxed read of `<project_root>/.relay/runs/<run_id>/` (per-project, ADR-25; corrected from the pre-9g `<data_dir>/...` path in the post-9g bug-fix sweep) |

`project_root` is resolved to a registered project by exact `root_path`
match, then `Path.expanduser().resolve()`-normalised match (so a `~/…`
project root supplied by a tool caller resolves the same way as a
project registered against an absolute path). An unknown root, unknown
run, or a not-paused run for `pause_response`, is returned as a tool
error (mirrors the REST 404/409 intent without HTTP status codes).
`relay__cancel_run` on an unknown run raises a tool error rather than
silently no-op'ing — divergent from the REST endpoint's idempotent
behaviour, deliberately so for programmatic callers.

**Child runs.** `relay__list_runs` always passes `include_children=True`
internally so a Claude-Code-driven user sees the full run tree — both
top-level runs and any child runs dispatched via fanout (spec §6, 9e).
The REST `GET /api/runs` endpoint defaults to top-level-only (the
dashboard's "Show child runs" toggle re-enables children); the MCP
surface always shows the tree because programmatic clients are the ones
that benefit most from full visibility.

**Event kinds returned by `tail_events`.** The stream now includes
`harness_session_ended` (9g — close-time `SessionEnded` mirror with
`stop_reason` + `messages[].usage` summary), `artifact_edited` (14a —
paused-iter artifact writes), and `subagent_dispatch` /
`subagent_return` / `child_runs_resolved` (9a–9c — fanout-join), in
addition to the original Phase-3 set. Consumers should treat unknown
kinds as opaque so a future spec §3.2 addition does not break clients.

### `tail_events` is a snapshot, not a stream

`spec.md §8` types `relay__tail_events` as `-> AsyncIterator[Event]`,
but an MCP tool result is a single value — a live async generator
cannot be a tool return. It is implemented as a **bounded snapshot** of
events with `seq > since_seq`, oldest first. Poll it with the last
returned `seq` as the next `since_seq` to tail a run. This is the same
data the SSE tail carries (ADR-23), pull-paged instead of pushed; live
push remains the SSE endpoint's job. This spec-vs-impl delta is
intentional and recorded in ADR-27.

## Registering with a client

Copy the `relay` block from `docs/mcp-config.example.json` into:

- **Claude Code:** your project's `.mcp.json`
- **Claude Desktop:** `claude_desktop_config.json` → `mcpServers`

```json
{ "mcpServers": { "relay": { "type": "http",
  "url": "http://127.0.0.1:7800/mcp" } } }
```

The backend must be running (`relay serve`; default `127.0.0.1:7800`).
**Verify:** from a Claude conversation with the server registered,
invoke `relay__list_runs` and confirm it returns (an empty list is a
valid pass — it proves the transport and tool wiring).

## Operational notes (ADR-27)

- **Toolchain:** the *bundled* official MCP SDK (`mcp.server.fastmcp`),
  not standalone `jlowin/fastmcp`. Pinned `mcp>=1.27.1,<2`; the `<2`
  cap is load-bearing (v2 rearchitects the transport). Bump across the
  v1→v2 boundary only as a deliberate, ADR-gated action.
- **Lifespan wiring (the #1367 footgun):** a sub-app mounted via
  `app.mount()` does not get its ASGI lifespan auto-run, and
  `streamable_http_app()`'s session manager starts in that lifespan.
  `relay.app`'s lifespan therefore wraps its body in
  `async with mcp.session_manager.run():`. Omitting this makes every
  `/mcp` request hang — covered by `tests/mcp/test_mcp_mount.py`.
- **Mount path:** the FastMCP server is built with
  `streamable_http_path="/"` and mounted at `/mcp`, so the endpoint is
  exactly `/mcp` (the default `/mcp` internal path would double to
  `/mcp/mcp` under `app.mount("/mcp", …)`).
- **Host allow-list:** DNS-rebinding protection is on by default and
  permits `127.0.0.1:*` / `localhost:*` / `[::1]:*` only — correct for
  the single-user localhost MVP (ADR-12). Clients must use a
  `127.0.0.1`/`localhost` URL *with a port*. No auth in MVP (spec §8;
  bearer-token auth arrives with multi-user, same path as REST).
- **Testing:** `tests/mcp/test_mcp_tools.py` drives the tools in-process
  via `FastMCP.call_tool` against a scripted-harness `RelayCore`;
  `tests/mcp/test_mcp_mount.py` exercises the mounted endpoint
  end-to-end through the real app lifespan.
