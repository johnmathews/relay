# relay v2 — motivation

## What relay is for

relay solves a specific problem: **implementing large detailed plans accurately, without losing fidelity as context fills up**. The strategy is to break a plan into work units and run each in a separate harness session, with a deliberately compressed handoff between sessions. The chained-sessions pattern keeps each session's context window manageable, while the orchestration layer carries the plan forward.

v1 of relay implements this with a bash driver wrapping `claude -p`, a sentinel-based wire protocol for state transitions, and a Flask dashboard. It works. This document is not a critique of whether the idea is sound — it is. It's a record of why v1's *implementation* now constrains what relay can become, and what v2 must change.

## What v1 gets right

These have to survive the rewrite:

- **The chained-sessions pattern itself.** Each iter is a fresh harness invocation with a compressed handoff. This is the core insight and v2 keeps it unchanged.
- **A line-anchored wire protocol** for state signals between the agent and the driver. The specific syntax (`[[engteam:...]]` sentinels) is one valid implementation; the *concept* of a structured wire format is what matters.
- **Per-run namespacing.** Every invocation owns its own subtree on disk. Avoids cross-run contamination and makes the history inspectable.
- **Read-only dashboard pattern.** All mutations go through the orchestrator; the dashboard only reads. Strong safety invariant.
- **Cross-process control through one surface.** The hub model — every cancellation, every cross-project action funnels through one place — replaces brittle ad-hoc signaling.
- **The engineering-team skill structure.** Router + per-phase docs + references. The skill itself is contract-coupled (reads a preamble, emits a wire protocol), not bash-coupled, so it ports without rewrite.

## What v1 gets wrong

The pain points, ordered roughly by how often they hurt:

### 1. The dashboard is inelegant and incomplete

The biggest day-to-day frustration. The Flask + HTMX + watchdog stack works, but:

- **No visibility into subagent activity.** When the lead engineer dispatches a research subagent, the dashboard shows… nothing useful. The subagent's tool calls, intermediate text, and progress are invisible until it returns. This is the single largest observability gap.
- **No structured timeline.** Events appear as iter logs and a worktree pane. There's no unified "what is happening right now" view that aggregates tool calls, file edits, sentinel emissions, and subagent dispatches into one chronological narrative.
- **Historical replay is awkward.** Past runs are scattered across `iter-NN-<ts>.jsonl` files; the dashboard can show one at a time but doesn't model a run as a first-class entity with a navigable history.
- **HTMX + watchdog has known limits** for the richer UI relay needs next — multi-pane state, filter-as-you-type, timeline scrubbing, drill-down on individual tool calls.

### 2. Bash is the wrong language for the orchestrator

The driver is ~1000 lines of bash doing:

- JSONL parsing via `jq` + plain-text projection via `awk`
- Sentinel matching with line-anchored regexes
- Prompt-marker extraction with a backwards-walking `awk` script
- Job-control gymnastics (`set -m`, process group signals) for cancellation
- Per-pid temp files + atomic moves for state writes

All of this is reimplemented in bash because the language is what was at hand. None of it is bash's strength. The same logic in Python is shorter, testable, type-checkable, and integrates with everything else the project needs (an HTTP server, an event store, observability tooling).

### 3. No programmable surface

relay is invokable only as a CLI today. There is no REST API, no MCP server, no way for another process (a scheduler, an external dashboard, Claude itself) to start a run, ask "what's the status," or stream events. The dashboard's SSE endpoint comes close, but it's read-only and limited to the per-project Flask app.

The cost of this is felt every time you want to do *anything* automated with relay — kick off a run from a script, schedule a nightly evaluation, watch a run from a different device. Today it's "shell out to `relay`" or nothing.

### 4. The wire-protocol contract is duplicated across four places

The sentinel grammar lives in:

1. `docs/sentinel-contract.md` (spec)
2. `skills/engineering-team/references/sentinels.md` (what the lead engineer reads)
3. `bin/relay` (the parser)
4. `tests/test-parsing.sh` (the fixtures)

Changes have to propagate to all four. This is documented as load-bearing in v1's `CLAUDE.md` ("duplicated by design"), but it's still duplication, and it slows iteration on the protocol itself.

### 5. The string-matching protocol is fragile

Sentinels are line-anchored patterns matched against assistant text projected from the JSONL stream. The matcher is robust today because of careful discipline: tool i/o is stripped before matching, fenced code blocks are forbidden around sentinels, indented sentinels don't match. Every one of those rules is a workaround for "we're matching strings in arbitrary model output." A structured-event channel (MCP tools, RPC events, JSON contract) eliminates the whole class of problem.

### 6. No room to grow

Concrete things v1 cannot easily become without significant restructuring:

- Remote-accessible (drive it from a phone, survive laptop sleep)
- Container-isolated per run (so a runaway loop can't damage the host)
- Multi-user (even just "share a link to a run with a colleague")
- Schedulable (run X at 2am every weekday)
- An MCP server callable by Claude itself

None of these are required for v2 MVP. But v2's foundations must not preclude them.

## Goals for v2

In rough priority order:

1. **Live observability of subagent internals.** The dashboard must show tool calls, intermediate text, and subagent activity in near-real-time. This is the single most important upgrade.
2. **First-class historical replay.** Past runs are inspectable as cleanly as live ones. Same UI surface, same event model, just bounded in time.
3. **Dashboard as primary control plane.** The dashboard is the default UI for everything: browsing projects, reading prompts and plans (rendered properly — markdown, diagrams, code), reviewing artifacts, *and* starting/pausing/cancelling runs. The "scary commit without preview" of the CLI default is replaced with browse-render-review-start. The CLI remains as a scripting/automation surface but is no longer the assumed default.
4. **One language for orchestration.** Python end-to-end. No bash in the loop.
5. **Programmable surface.** REST endpoints for run management. MCP server for agent-driven workflows. Both share a single service layer, the same one the dashboard uses.
6. **Harness-agnostic core.** The orchestrator should drive any reasonable harness — pi first, with claude or others added by writing a small adapter, not by rewriting the orchestrator.
7. **Architectural headroom.** Multi-user, container isolation, scheduled runs, remote access — none are built now, but none are precluded. Specifically: `user_id` is a first-class column from day one; runs are first-class DB rows; prompts are addressable entities, not blobs.
8. **A structured event store as the source of truth.** Every action (iter start, tool call, sentinel/signal emit, file edit, phase transition) is an event row. The dashboard tails it. Replay re-streams it. OTel mirrors it. One canonical record.

## Non-goals for v2

Explicit deferrals. Each one was considered and rejected for the v2 MVP:

- **Multi-user / RBAC.** Architect for it (the `user_id` FK); don't build it. The actual current need is "one user, remote-accessible from any device" — single-user-hosted gets ~80% of the multi-user value for ~15% of the cost.
- **Container-per-run isolation.** A post-MVP feature (Phase 10 in `plan.md`). Worth doing eventually because it solves both "blast radius" and "laptop-sleep durability" — but not on the critical path for MVP.
- **Prompt library UI.** Prompts are first-class entities in the data model, but there's no UI to browse / version / share them yet.
- **Scheduled runs.** The data model separates "submit a run" from "execute a run" (so this is additive), but there is no scheduler.
- **Cross-project orchestration.** The data model puts `project` as an FK on `run` (not the other way around), so this is additive.
- **Backward compatibility with v1.** v2 is a clean break. v1 stays at `~/projects/relay`, runs untouched, and gets deprecated when v2 ships.
- **Migrating v1 run history into v2.** The on-disk layouts will diverge; old runs stay accessible in v1.

## Hard constraints

Things v2 must respect, regardless of design preferences:

- **Auth via Claude Max subscription.** API-key billing is too expensive for the workload. The chosen harness (pi) must authenticate against the Max account.
- **No 30-second tool timeout.** Test suites, builds, and long lint runs routinely exceed 30 seconds. The Claude Agent SDK enforces this cap; pi does not — that's a primary reason pi is the chosen harness.
- **Single-user MVP scope.** Resist multi-user creep during build.

## Known risks (parked, but tracked)

These are not blockers, but they are unresolved and the architecture should acknowledge them:

1. **Pi + Max billing.** Pi's public docs state that Anthropic OAuth/subscription auth bills *per-token outside the plan cap* (the same separation Anthropic is creating for `claude -p` programmatic usage on 2026-06-15). The user has private context suggesting the Max subscription will cover relay usage; this needs verification before relay v2 is deployed for sustained use. If the assumption fails, the architecture survives (the harness is swappable) but the cost model doesn't.
2. **Pi version churn.** Pi releases weekly (current upstream v0.75.3 at the time of writing; v2 is initially pinned to v0.74.0, the version exercised by the de-risking suite). The event-stream schema and CLI flags may change between releases; v2 treats schema drift as a maintenance task on each pin bump.
3. **MCP-via-community-extension.** If the signaling strategy graduates from text-sentinels to MCP tools on pi, the dependency is `pi-mcp-adapter` (community, ~81k DL/mo). That's a real maintenance risk worth re-evaluating at the time of the switch.
4. **TypeScript creep.** Pi's extension system is TypeScript-only. Custom hooks, custom tools, or extensions all require TS. v2 contains TS to the extension layer (relay extension, MCP shim if used); orchestration stays Python.
5. **`relay-v2` superseding the engineering-team skill in `~/projects/relay`.** The skill source-of-truth currently lives in v1's repo. v2 must port it (with v2-shaped preamble and signaling) before the skill can be installed against v2.

## One-paragraph summary

relay v2 keeps the core idea (chained sessions with compressed handoffs) and replaces the implementation top-to-bottom. Python orchestrator instead of bash. Pi instead of `claude -p`. Vue + Pinia dashboard instead of HTMX + Flask. Structured event store as the source of truth, with OTel export to Langfuse for analytics. REST + MCP as programmable surfaces sharing one service layer. Harness adapters so swapping inference backends is mechanical. Built for single-user-localhost MVP, but architecturally ready to grow into hosted, multi-user, container-isolated, schedulable territory without a third rewrite.
