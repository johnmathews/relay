# relay

Chain agent sessions for unattended multi-phase engineering work.

> **Status:** Pre-implementation. The design corpus is complete and committed.
> Coding starts at **Phase 0** of [`docs/plan.md`](docs/plan.md).

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
| [`docs/decisions.md`](docs/decisions.md) | 16 ADRs with context, alternatives, rationale, consequences. **Append-only.** |
| [`docs/spec.md`](docs/spec.md) | Canonical design — architecture, data model, harness protocol, signaling, REST + MCP surface, Vue dashboard, observability. |
| [`docs/plan.md`](docs/plan.md) | 9 MVP phases (28 dev-days) + 7 post-MVP phases, with per-phase verification criteria. |

`CLAUDE.md` summarises the design hierarchy and load-bearing invariants for
Claude Code sessions.

## De-risking evidence

`scratch/pi_derisk_workdir/findings.md` records empirically confirmed pi
behavior — confirmed event schema, no 30-second tool timeout, working
session resume. Captured event fixtures under `scratch/pi_derisk_workdir/`
are referenced by `spec.md` §4.2 and will become Phase 1 harness unit-test
inputs.

## License

[MIT](LICENSE)
