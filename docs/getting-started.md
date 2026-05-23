# Getting started — install, run, and verify relay-v2

> Single end-to-end walkthrough. Cross-links the per-subsystem docs
> instead of duplicating them. If a step's detail isn't here, the
> linked doc is authoritative. Time budget: ~15 min for steps 1–3
> (install + first run + dashboard); ~30 min for step 5 (MCP smoke);
> ~1–2 h for step 7 (the eng-team behavioral acceptance, which needs
> real pi).

## 1. Prerequisites

1. **Python 3.13** and [`uv`](https://docs.astral.sh/uv/) (`uv --version`
   should print something).
2. **`pi` 0.74.0** on `PATH` — the harness binary. relay pins this
   version (`.tool-versions`); a mismatch logs a non-fatal warning at
   first run. `pi` is **not** a Python dependency, so `uv sync` does
   not install it; install it separately and confirm with `pi
   --version`.
3. **Claude Max subscription**, for the pi auth path (`PI_AGENT_SDK=1`;
   ADR-09). This is required to actually run a session; you can do
   steps 2–5 without it but step 7 will fail.
4. **(Frontend dev only)** Node 22 — only needed if you'll edit the
   dashboard locally with `npm run dev`. The Docker image and the
   prod-serving path build the SPA themselves.

## 2. Install and first run

```bash
git clone https://github.com/johnmathews/relay && cd relay
uv sync                              # create .venv, install deps
uv run pytest                        # 342 passed, 3 skipped (pi-e2e gated)
uv run relay serve                   # daemon on http://127.0.0.1:7800
```

In another shell:

```bash
curl -s http://127.0.0.1:7800/health        # -> {"status":"ok"}
curl -s http://127.0.0.1:7800/openapi.json | head -c 80
```

`relay serve` creates `<cwd>/.relay/relay.db` (the SQLite event store —
ADR-10) on first run. Configuration is env-driven via `RELAY_*` (see
[`spec.md`](spec.md) §11), e.g. `RELAY_PORT=8080 uv run relay serve`.

## 3. Open the dashboard

Open <http://127.0.0.1:7800/> in a browser. You should see the **Hub**
(empty until you register a project), with the navigation for runs,
prompts, and the file browser. The dashboard is the primary control
plane (ADR-15, [`spec.md`](spec.md) §9, [`dashboard.md`](dashboard.md)).

If the page loads but is empty / blank, see [Troubleshooting](#troubleshooting).

> **Frontend dev mode** (only if you're editing the SPA): in
> `frontend/` run `npm install && npm run dev`. Vite proxies `/api` to
> `:7800`. See [`frontend/README.md`](../frontend/README.md).

## 4. Register a project and start your first run

You can register a project and start a run three ways: dashboard, REST,
or MCP. The dashboard is the easiest first path.

**From the dashboard (recommended for the first run):**

1. Hub → **Register project** → point at an absolute path to a real git
   repo of yours.
2. Open the project → **New run** → 4-step wizard (project → prompt →
   options → side-effect-free preview that shows what the run will see
   before commitment).
3. Confirm. The run appears on the Hub and in the project's runs list;
   open it for the **live SSE timeline** + Iters / Artifacts / Worktree
   panes ([`spec.md`](spec.md) §9, [`dashboard.md`](dashboard.md)).

**From the REST API** (useful for scripting; full surface
[`api.md`](api.md) / `/openapi.json`):

```bash
# 1. Register a project
curl -sX POST http://127.0.0.1:7800/api/projects \
  -H 'content-type: application/json' \
  -d '{"name":"demo","root_path":"/absolute/path/to/repo"}'

# 2. Start a run
curl -sX POST http://127.0.0.1:7800/api/runs \
  -H 'content-type: application/json' \
  -d '{"project_id": 1, "prompt_body": "echo hello"}'

# 3. List runs
curl -s http://127.0.0.1:7800/api/runs
```

> **Note on the CLI.** `relay serve`, `relay --version`, and
> `relay install-skill` are the only CLI subcommands implemented in
> the MVP (see `spec.md` §11.3 accuracy note). `relay start` / `status`
> / `cancel` are a post-MVP convenience; until then, use the dashboard,
> REST, or MCP for run management.

## 5. Register the MCP server and smoke-test it

The backend mounts an MCP server at `/mcp` with seven tools that are
thin adapters over the same `RelayCore` ([`mcp.md`](mcp.md), ADR-27).

1. Copy the `relay-v2` entry from [`mcp-config.example.json`](mcp-config.example.json)
   into your `.mcp.json` (Claude Code) or `claude_desktop_config.json`
   (Claude Desktop). The URL must stay on `127.0.0.1`/`localhost` —
   the server enforces a localhost DNS-rebinding allow-list (ADR-12 +
   ADR-27).
2. Restart your MCP client.
3. **Smoke test.** Ask Claude to call `relay__list_runs` (e.g. "use
   the relay-v2 MCP server and list current runs"). A successful call
   returns the list you saw via REST in step 4.

The full tool list ([`mcp.md`](mcp.md) §"Tools"): `relay__list_runs`,
`relay__get_run`, `relay__start_run`, `relay__cancel_run`,
`relay__pause_response`, `relay__tail_events`, `relay__read_artifact`.

## 6. (Optional) Enable Langfuse observability

relay can mirror its event store to an OpenTelemetry
`relay.run`→`relay.iter`→`relay.tool_call` span tree, exported to
self-hosted Langfuse. It is **opt-in** and non-load-bearing (ADR-10,
ADR-29) — when `RELAY_OTEL_EXPORT=none` (the default) no provider is
constructed and no network call is made.

Full procedure: [`observability.md`](observability.md) (self-hosting
Langfuse + the manual trace-tree acceptance). The short version:

```bash
# 1. Run Langfuse's officially-maintained compose (NOT vendored here):
git clone https://github.com/langfuse/langfuse
cd langfuse && docker compose up -d   # serves http://localhost:3000
# 2. In the Langfuse UI: create an org + project, copy the API keys.
# 3. Start relay with the OTel mirror enabled:
RELAY_OTEL_EXPORT=langfuse \
RELAY_LANGFUSE_HOST=http://localhost:3000 \
RELAY_LANGFUSE_PUBLIC_KEY=pk-lf-... \
RELAY_LANGFUSE_SECRET_KEY=sk-lf-... \
uv run relay serve
# 4. Start a run (step 4 above). Open Langfuse → Traces.
#    Acceptance: a relay.run span with relay.iter children and
#    relay.tool_call grandchildren nests correctly.
```

This is one of the three manual journal-attested acceptances (ADR-30,
carried from Phase 7). The deterministic span-structure tests (
`tests/observability/test_otel_export.py`) run in CI; the live-UI
verification does not.

## 7. (Optional) Run the engineering-team behavioral acceptance

This is the MVP's flagship demo: relay drives the bundled
`engineering-team` skill across multiple iters to evaluate, plan, and
fix bugs in the v1 demo fixture. It spawns real pi against your Max
subscription — multi-minute, non-deterministic — and is the third of
the three manual acceptances (ADR-28 §3 / ADR-30).

Full procedure: [`skills.md`](skills.md) §"Verification → Behavioral".
The shape:

1. Seed the v1 demo fixture (deliberately broken `factorial(5)`
   returns 24): `~/projects/relay/relay-v1/fixtures/eng-team-demo-seed/reset.sh`
2. `uv run relay install-skill --project <fixture-root>`
3. Register the fixture as a project (step 4 above) and start a run
   with the prompt "evaluate, plan, and fix the bugs" — set
   `PI_AGENT_SDK=1` in the `relay serve` environment.
4. Watch the dashboard: expect a clean four-phase timeline
   (`phase-start evaluation` → `planning` (with a `pause-for-input`
   gate) → `development` (`unit-start`/`unit-done` per work unit) →
   `wrap-up` (`done`)), with `evaluation-report.md` and
   `improvement-plan.md` rendering in the **Artifacts** pane.
5. Inspect the result: `~/projects/relay/relay-v1/examples/inspect-eng-team-demo.sh <fixture-root>`
   — expect the seeded bug fixed, fixture tests green, branch merged,
   journal entry written.
6. **Record the outcome in this repo's journal.** This is the
   project-wide convention for pi e2e (ADR-24).

## 8. (Optional) Docker / GHCR

Build and run locally:

```bash
docker build -t relay-v2 .
docker run -p 7800:7800 relay-v2          # dashboard at :7800
# or with the example compose:
docker compose -f docker-compose.example.yml up
```

Or pull the CI-published image (note: the package is private by default
until you flip it at <https://github.com/users/johnmathews/packages/container/relay/settings>):

```bash
docker pull ghcr.io/johnmathews/relay:latest
```

The image runs as a non-root user and serves both the REST/MCP API and
the built Vue SPA from one process at `:7800` (spec §11.2, ADR-30).

## Troubleshooting

1. **`pi` not on PATH / "harness binary not found"** — install pi
   0.74.0 (`.tool-versions`); confirm with `pi --version`.
2. **Run starts but the assistant never speaks / hangs forever** —
   almost always pi auth. Re-check `PI_AGENT_SDK=1` is set in the
   process that runs `relay serve` (not just your shell), and that
   `pi` works standalone with your Max account.
3. **Dashboard loads but every panel is empty / 401** — the SPA is
   served, but `/api/*` is not reachable. Confirm `curl
   http://127.0.0.1:7800/api/projects` returns JSON, not 404. In dev
   (`npm run dev`) check `VITE_API_PROXY_TARGET` matches your backend
   port.
4. **MCP client doesn't see relay tools** — restart the client after
   editing config; verify the URL is `http://127.0.0.1:7800/mcp` (note
   the trailing `/mcp`, not `/api/mcp`); confirm the backend log shows
   the `/mcp` mount on startup.
5. **`docker pull ghcr.io/johnmathews/relay:latest` returns 401** —
   the package is private; either log in (`docker login ghcr.io` with
   a PAT carrying `read:packages`) or flip the package to public in
   GHCR settings.
6. **SSE timeline stops updating behind a reverse proxy** — see
   [`spec.md`](spec.md) §9 / [`dashboard.md`](dashboard.md): SSE
   requires `X-Accel-Buffering: no` and a long `proxy_read_timeout` if
   you front the backend with nginx; the Vite dev proxy already does
   this.

## What to read next

- [`spec.md`](spec.md) — the canonical design contract.
- [`decisions.md`](decisions.md) — 42 ADRs; the *why* behind every
  load-bearing choice.
- [`plan.md`](plan.md) — the phased build history (MVP + the shipped
  post-MVP arcs: 9a–9g fanout-join + 14a–14f pause-for-review) and
  the remaining post-MVP sketch.
- [`acceptance-testing.md`](acceptance-testing.md) — the live tracker
  for the current MVP-acceptance phase (gates, exercise sweep, bug
  log).
- Per-subsystem ops refs: [`harness.md`](harness.md),
  [`orchestrator.md`](orchestrator.md), [`api.md`](api.md),
  [`dashboard.md`](dashboard.md), [`mcp.md`](mcp.md),
  [`skills.md`](skills.md), [`observability.md`](observability.md),
  [`fanout.md`](fanout.md) (parallel-iter fanout-join runbook).
