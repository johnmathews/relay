# relay

Chain agent sessions for unattended multi-phase engineering work.

> **Status:** Phases 0–2 complete (scaffold, harness layer, orchestrator)
> plus a pre-Phase-3 hardening pass. Next: **Phase 3** (REST API +
> persistence) of [`docs/plan.md`](docs/plan.md).

## What relay is for

relay implements large detailed plans without losing accuracy as context fills
up. It breaks a plan into work units and runs each in a **separate** headless
agent session, carrying state forward via a deliberately compressed handoff
between sessions. The orchestrator (this project) is harness-agnostic; v2 uses
[pi](https://github.com/earendil-works/pi) as the inference harness, against
the user's Claude Max subscription via `PI_AGENT_SDK=1`.

v2 is a clean-break rewrite of [v1](https://github.com/johnmathews/relay-v1),
replacing bash + Flask + `claude -p` with Python + FastAPI + Vue 3 + pi.
There is no backward compatibility; v1 is deprecated when v2 ships.

## Documents — read these before coding

The four docs under `docs/` are the canonical source. Read in this order:

| Doc | Purpose |
|---|---|
| [`docs/motivation.md`](docs/motivation.md) | Why v2 exists. Goals, non-goals, hard constraints, parked risks. |
| [`docs/decisions.md`](docs/decisions.md) | 22 ADRs with context, alternatives, rationale, consequences. **Append-only.** |
| [`docs/spec.md`](docs/spec.md) | Canonical design — architecture, data model, harness protocol, signaling, REST + MCP surface, Vue dashboard, observability. |
| [`docs/plan.md`](docs/plan.md) | 9 MVP phases (28 dev-days) + 7 post-MVP phases, with per-phase verification criteria. |

`CLAUDE.md` summarises the design hierarchy and load-bearing invariants for
Claude Code sessions. The ADR log now has 22 entries (ADR-22 records the
resume forward-progress decision from the pre-Phase-3 hardening pass).

## Development quickstart

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                              # create the venv, install deps
uv run pytest                        # run the test suite
uv run relay serve                   # daemon on http://127.0.0.1:7800
curl http://127.0.0.1:7800/health    # -> {"status": "ok"}
```

`relay serve` creates `<cwd>/.relay/relay.db` (the SQLite event store) on
first run. Configuration is env-driven via `RELAY_*` variables (see
[`docs/spec.md`](docs/spec.md) §11); e.g. `RELAY_PORT=8080 uv run relay serve`.
Lint and types: `uv run ruff check .` and `uv run mypy`.

### pi version

relay v2 is pinned to **pi 0.74.0** — the version exercised by the
de-risking fixtures (OQ-5). The pin lives in [`.tool-versions`](.tool-versions);
install that exact version (`pi` is not a Python dependency, so `uv sync`
does not manage it). On a mismatch `relay` logs a non-fatal warning at
the first run; bumping the pin is a deliberate maintenance step (re-run
the de-risking fixtures first).

## De-risking evidence

`scratch/pi_derisk_workdir/findings.md` records empirically confirmed pi
behavior — confirmed event schema, no 30-second tool timeout, working
session resume. Captured event fixtures under `scratch/pi_derisk_workdir/`
are referenced by `spec.md` §4.2 and will become Phase 1 harness unit-test
inputs.

## License

[MIT](LICENSE)
