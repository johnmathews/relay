# relay

Chain agent sessions for unattended multi-phase engineering work.

> **Status:** Phases 0–8 complete — the MVP is done. Post-MVP polish
> has since shipped: parallel-iter fanout/join (Phases 9a–9f) +
> `harness_session_ended` event persistence (9g) +
> pause-for-review (Phases 14a–14f, inline artifact edit during a
> paused run). The codebase is now in an **MVP-acceptance-testing
> phase** — feature work is parked until the named gates close (see
> [`docs/acceptance-testing.md`](docs/acceptance-testing.md)).
> Harness (pi) → orchestrator (`RelayCore` + append-only event store
> + chained-iter run loop) → REST API + SSE → Vue 3 dashboard →
> MCP server at `/mcp` → bundled `engineering-team` skill →
> opt-in OTel mirror to Langfuse, plus a published container image
> and CI. See [`docs/plan.md`](docs/plan.md) for the full phase
> history and post-MVP sketch.

## What relay is for

relay implements large detailed plans without losing accuracy as context
fills up. It breaks a plan into work units and runs each in a
**separate** headless agent session, carrying state forward via a
deliberately compressed handoff between sessions. The orchestrator (this
project) is harness-agnostic; v2 uses
[pi](https://github.com/earendil-works/pi) as the inference harness,
against the user's Claude Max subscription via `PI_AGENT_SDK=1`.

v2 is a clean-break rewrite of [v1](https://github.com/johnmathews/relay-v1),
replacing bash + Flask + `claude -p` with Python + FastAPI + Vue 3 + pi.
There is no backward compatibility; v1 is deprecated when v2 ships.

## Documents — read these before coding

The four docs under `docs/` are the canonical source. Read in this order:

| Doc | Purpose |
|---|---|
| [`docs/motivation.md`](docs/motivation.md) | Why v2 exists. Goals, non-goals, hard constraints, parked risks. |
| [`docs/decisions.md`](docs/decisions.md) | 41 ADRs with context, alternatives, rationale, consequences. **Append-only.** |
| [`docs/spec.md`](docs/spec.md) | Canonical design — architecture, data model, harness protocol, signaling, REST + MCP surface, Vue dashboard, observability, packaging. |
| [`docs/plan.md`](docs/plan.md) | 9 MVP phases (0–8, all complete) + post-MVP arcs (9a–9g fanout-join + 14a–14f pause-for-review, both shipped) + remaining sketch. |
| [`docs/acceptance-testing.md`](docs/acceptance-testing.md) | Live tracker for the current MVP-acceptance phase: gates, exercise sweep, bug log, definition of done. |

Per-subsystem operational references: [`docs/harness.md`](docs/harness.md),
[`docs/orchestrator.md`](docs/orchestrator.md), [`docs/api.md`](docs/api.md),
[`docs/dashboard.md`](docs/dashboard.md), [`docs/mcp.md`](docs/mcp.md),
[`docs/skills.md`](docs/skills.md),
[`docs/observability.md`](docs/observability.md),
[`docs/fanout.md`](docs/fanout.md) (operator runbook for
parallel-iter fanout-join — what it looks like in production, how to
cancel, restart behaviour, troubleshooting). `CLAUDE.md` summarises
the design hierarchy and load-bearing invariants for Claude Code
sessions.

## Install & run

> For a single end-to-end walkthrough (install → first run → dashboard
> → register MCP → optional Langfuse → the eng-team demo acceptance →
> Docker), see [`docs/getting-started.md`](docs/getting-started.md).
> The section below is the quickstart.

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                              # create the venv, install deps
uv run pytest                        # run the test suite (342 passed, 3 pi-e2e gated)
uv run relay serve                   # daemon on http://127.0.0.1:7800
curl http://127.0.0.1:7800/health    # -> {"status": "ok"}
```

Then open the **dashboard** at <http://127.0.0.1:7800/> — the Hub, the
project view (runs / prompts / files), the 4-step New-Run wizard, and
the live run timeline. The dashboard is the primary control plane
(ADR-15). In production the built SPA is served by the backend itself
(see [Docker](#docker) / [`docs/dashboard.md`](docs/dashboard.md)); in
frontend development Vite proxies `/api` to the backend — see
[`frontend/README.md`](frontend/README.md).

`relay serve` creates `<cwd>/.relay/relay.db` (the SQLite event store)
on first run. Configuration is env-driven via `RELAY_*` variables (see
[`docs/spec.md`](docs/spec.md) §11), e.g. `RELAY_PORT=8080 uv run relay
serve`. Implemented CLI subcommands: `relay serve`, `relay --version`,
`relay install-skill` (`[--project PATH] [--force]`). In the MVP, runs
are created and managed through the dashboard, the REST API
([`docs/api.md`](docs/api.md)), or the MCP server — not a `relay start`
CLI (that, plus `status`/`cancel`, is a post-MVP convenience; spec
§11.3 is the target surface). Lint and types: `uv run ruff check .` and
`uv run mypy`.

### pi version

relay v2 is pinned to **pi 0.74.0** — the version exercised by the
de-risking fixtures (OQ-5). The pin lives in
[`.tool-versions`](.tool-versions); install that exact version (`pi` is
not a Python dependency, so `uv sync` does not manage it). On a mismatch
`relay` logs a non-fatal warning at the first run; bumping the pin is a
deliberate maintenance step (re-run the de-risking fixtures first).

## MCP server

The backend mounts an MCP server at `/mcp` (seven `relay__*` tools that
are thin adapters over the same `RelayCore` — ADR-27, spec §8). Register
it with Claude Code / Claude Desktop by copying the `relay-v2` entry
from [`docs/mcp-config.example.json`](docs/mcp-config.example.json) into
your `.mcp.json` (Claude Code) or `claude_desktop_config.json`
(Claude Desktop). The backend must be running and the URL must stay on
`127.0.0.1`/`localhost` (the server enforces a localhost
DNS-rebinding allow-list — ADR-12 single-user, ADR-27). Full reference:
[`docs/mcp.md`](docs/mcp.md).

## Observability (optional)

relay can mirror its event store to an OpenTelemetry
`relay.run`→`relay.iter`→`relay.tool_call` span tree, exported to
self-hosted Langfuse. It is **opt-in** and non-load-bearing — with
`RELAY_OTEL_EXPORT=none` (the default) no provider/exporter is
constructed and no network call is made (ADR-29). To enable:

```bash
RELAY_OTEL_EXPORT=langfuse
RELAY_LANGFUSE_HOST=http://localhost:3000
RELAY_LANGFUSE_PUBLIC_KEY=pk-lf-...
RELAY_LANGFUSE_SECRET_KEY=sk-lf-...
```

Run Langfuse from its officially-maintained compose (not vendored here
to avoid drift) — see
[`docs/langfuse-compose.example.yml`](docs/langfuse-compose.example.yml)
for the wiring and [`docs/observability.md`](docs/observability.md) for
the full procedure and the manual trace-tree acceptance check.

## Docker

A multi-stage image builds the Vue frontend and serves it from the
FastAPI backend (spec §11.2). Published to
`ghcr.io/johnmathews/relay` by CI on push to `main`.

```bash
docker build -t relay-v2 .
docker run -p 7800:7800 relay-v2          # dashboard at :7800
# or, with the example compose (optionally wiring Langfuse):
docker compose -f docker-compose.example.yml up
```

`docker-compose.example.yml` documents the `RELAY_*` env surface and
points at [`docs/langfuse-compose.example.yml`](docs/langfuse-compose.example.yml)
for the Langfuse stack (run upstream's compose; relay only needs the
three `RELAY_LANGFUSE_*` vars).

## De-risking evidence

`scratch/pi_derisk_workdir/findings.md` records empirically confirmed pi
behavior — confirmed event schema, no 30-second tool timeout, working
session resume. Captured event fixtures under `scratch/pi_derisk_workdir/`
are referenced by `spec.md` §4.2 and are the harness unit-test inputs.

## License

[MIT](LICENSE)
