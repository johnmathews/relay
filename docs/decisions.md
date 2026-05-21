# relay v2 — architectural decisions

> ADR-style log of the major design decisions for relay v2, with the
> alternatives considered and the rationale that selected each option.
> Append-only — new decisions added at the bottom. Existing entries get
> a `**Status:** superseded by ADR-NN.` header rather than deletion.

The decisions are numbered in the order they were made, not the order
of importance.

---

## ADR-01 — Rewrite, not refactor

**Status:** Accepted (2026-05-19)

**Context.** v1 is bash + Flask + HTMX + sentinels. The pains documented in
`motivation.md` are largely structural — the dashboard's UI ceiling, the
absence of a programmable surface, the four-places-encode-the-same-rules
invariant, the absence of structured subagent observability. None are
fixable with incremental refactors of v1.

**Alternatives considered.**
1. Incremental refactor of v1 — port the Python parts to a richer stack
   in place; replace bash piece by piece.
2. Clean-break rewrite as v2 in a new repo.

**Decision.** Clean-break rewrite. New repo at `~/projects/relay-v2`.

**Rationale.** The v1 codebase's hot paths (bash driver, sentinel parser,
worktree handling, dashboard registry) are deeply intertwined. Replacing
the bash driver requires replacing the parser, which requires replacing
the dashboard state model, which requires replacing the registry. By the
time you've done all of that, v1's shell is just an empty husk. The
rewrite is the same amount of work as the in-place refactor, with the
upside that v1 keeps working until v2 ships.

**Consequences.**
- v1 stays runnable at `~/projects/relay` until v2 supersedes it.
- No backward compatibility for on-disk state, the sentinel contract, or
  CLI flags.
- The engineering-team skill (currently in v1's tree) ports to v2 with a
  revised preamble and possibly revised signaling format.

---

## ADR-02 — Python end-to-end for the orchestrator

**Status:** Accepted (2026-05-19)

**Context.** v1's driver is ~1000 lines of bash doing JSONL parsing, awk
prompt extraction, job-control signal handling, and atomic file writes.
Bash is doing none of these things at its strength.

**Alternatives considered.**
1. Keep bash for orchestration, add a Python sidecar for parsing.
2. Move to Python entirely.
3. Move to Go (fast binary distribution, good concurrency primitives).
4. Move to Rust (similar to Go, sharper type system).

**Decision.** Python (3.13, the project's standard).

**Rationale.** Python aligns with the user's existing global preferences
(`uv`, `pytest`, type annotations). The orchestrator needs to host a
FastAPI app, an MCP server, and an event store — all of which have
mature Python ecosystems. Go and Rust would buy faster startup and tighter
binaries, but the workload is I/O-bound (subprocesses + HTTP) and the
ecosystem advantages (FastAPI, Pydantic, the MCP SDK, Langfuse client) are
all in Python. No third language unless strongly justified — per
`CLAUDE.md` in v1.

**Consequences.**
- Dependency on `uv` for env management. Test runner is `pytest`.
- Single static binary distribution is harder than with Go; deploy via
  pipx / Docker.

---

## ADR-03 — Pi as primary harness, not Claude or SDK

**Status:** Accepted (2026-05-19), partially conditional on ADR-09.
Invocation form amended by ADR-16 — MVP uses `--mode json` rather than
the `--mode rpc` originally decided below.

**Context.** The orchestrator drives a headless agent harness. Three real
candidates: Anthropic's Claude Agent SDK, the Claude CLI (`claude -p`),
and the pi harness by Earendil.

**Alternatives considered.**

| Option | Pro | Con |
|---|---|---|
| Claude Agent SDK (Python) | Typed events, first-class hooks, session resume, OTel | **30-second hardcoded tool timeout** — fatal for test suites; June 15 2026 billing splits programmatic usage from Max quota |
| `claude -p` subprocess | No SDK tool timeout, mature stream-json schema, well-known | Same June 15 billing split applies; you reimplement what the SDK gives you |
| pi (`pi --mode rpc`) | No tool timeout, modern session model, RPC mode designed for tools like relay, Max subscription auth path | MCP and subagents are deliberately not built in; v0.x churn; community extension dependency if MCP is needed; auth/billing semantics need verification (see ADR-09) |

**Decision.** Pi as the v2 primary harness. Invocation form is
`PI_AGENT_SDK=1 pi --mode rpc` (env var set per the user's required
invocation; investigated further during de-risking).

**Rationale.** The 30s tool timeout in the Claude Agent SDK is a hard
incompatibility with relay's workflow — the engineering-team skill
routinely runs test suites and builds that exceed 30s in a single tool
call. `claude -p` avoids that timeout but doesn't avoid the June 15
billing split, which is the user's other hard constraint. Pi avoids both
issues (no tool timeout; the user has identified a Max-subscription
path, conditional on ADR-09's verification). Pi's design philosophy —
minimal harness, extensions for everything else — pairs cleanly with the
"relay is the orchestrator, not the harness" framing.

**Consequences.**
- MCP and subagents are not first-class on pi; both are handled outside
  the harness (signaling via text sentinels in MVP; subagents managed by
  relay's orchestrator as separate harness sessions).
- v2 must track pi's release cadence (weekly) and pin a specific version.
- Adding `claude` as a secondary harness later is mechanical via the
  `Harness` protocol (ADR-04).

---

## ADR-04 — Harness abstraction, pi-first implementation

**Status:** Accepted (2026-05-19)

**Context.** The user wants the ability to swap pi for a different harness
(e.g. `claude -p`) without rewriting relay's orchestrator.

**Alternatives considered.**
1. Hardcode pi calls throughout; refactor only if/when claude is needed.
2. Design a `Harness` protocol up-front; implement only pi now; add other
   harnesses by writing parallel implementations.
3. Build a full abstraction layer with two harness implementations on
   day one.

**Decision.** Option 2. Define the `Harness` protocol and event types
with the upcoming claude/other use-case in mind; implement only
`PiHarness` for v2 MVP.

**Rationale.** YAGNI argues against (3) — designing for two harnesses
when one suffices is over-engineering. But (1) leaves no shape to swap
into; when the second harness arrives, the orchestrator has to be
refactored from the inside out. (2) is the middle path: the *interface*
is shaped for swappability, but only one implementation is written.
Writing the second implementation later is the test that the abstraction
was right.

**Consequences.**
- A small `harness/` package with: `Harness` protocol, `HarnessSession`
  protocol, normalized `HarnessEvent` dataclasses, and a `PiHarness`
  concrete impl.
- The orchestrator only ever sees normalized events. Pi-specific JSONL
  schema lives inside `PiHarness` and nowhere else.
- Subprocess lifecycle, cancellation semantics, MCP/sentinel wiring all
  encapsulated per harness.

---

## ADR-05 — Signaling strategy is per-harness, not architectural

**Status:** Accepted (2026-05-19)

**Context.** The agent needs to signal state transitions to relay
(handoff, done, pause, phase-start, unit-done). Two viable mechanisms:
line-anchored text sentinels in assistant output (v1's approach), or MCP
tool calls invoked by the agent.

**Alternatives considered.**
1. Lock the project to sentinels.
2. Lock the project to MCP tools.
3. Make signaling a per-harness configuration (`SignalConfig.strategy`).

**Decision.** Option 3. The `Harness` accepts a `SignalConfig` that
selects between `text_sentinels` and `mcp_tools`. The orchestrator emits
normalized `SignalEmitted` events regardless of strategy.

**Rationale.** Pi's design explicitly omits MCP; claude has MCP
first-class. Tying the project to one strategy paints into a corner for
the other harness. A strategy pattern resolves this cleanly: each harness
adapter picks the strategy that fits, and the orchestrator doesn't care.

For the v2 MVP on pi, the chosen strategy is `text_sentinels`. Rationale:
zero extra dependencies (no `pi-mcp-adapter` community extension), the
parser model transfers cleanly from v1 (with field-name updates for pi's
JSONL schema), and the engineering-team skill's existing sentinel
discipline applies unchanged.

**Consequences.**
- Two parsers in `harness/signaling/`: one matches sentinels in
  `AssistantText` events; one watches `ToolUseStart` events for
  `relay__*` tool names.
- The same `SignalEmitted(kind, args)` upstream regardless of which
  parser produced it.
- A/B testing strategies on the same harness becomes possible if useful.

---

## ADR-06 — Subagents managed at the orchestrator layer

**Status:** Accepted (2026-05-19)

**Context.** v1's engineering-team skill heavily uses subagents (dispatched
via Claude's Task tool). Pi has no built-in subagent dispatch.

**Alternatives considered.**
1. Depend on the `pi-subagents` community extension to keep subagents
   inside the harness session.
2. Manage subagents at the relay orchestrator layer: relay spawns a
   *separate* harness session for each subagent, captures its result,
   feeds it back into the parent session.
3. Eliminate subagents from the engineering-team skill, restructure
   around purely sequential iters.

**Decision.** Option 2. Subagent dispatch is a `SignalEmitted` event
type. When the lead engineer signals a subagent dispatch, relay starts a
fresh harness session with the subagent's role-specific prompt, runs it
to completion in its own context, and surfaces the result.

**Rationale.** Relay is already a session orchestrator. Adding "spawn N
child sessions from one parent" is a small extension of the chained-iter
pattern, not a new capability. Doing it at the orchestrator level keeps
the harness adapter minimal (one harness call = one session = one
context window) and avoids depending on a community extension whose
maintenance isn't guaranteed.

**Consequences.**
- Subagent dispatch is `SignalEmitted(kind="subagent_dispatch", args={role, prompt, ...})`.
- The dashboard shows subagent sessions as children of the parent run —
  full observability per-subagent because each is its own session.
- The engineering-team skill ports with the subagent signaling mapped to
  the new mechanism.
- Loses Claude's built-in `parent_tool_use_id` plumbing if/when the
  claude harness is added; the parent/child relationship is tracked in
  relay's DB instead.

---

## ADR-07 — FastAPI + Pydantic v2 + Uvicorn for the backend

**Status:** Accepted (2026-05-19)

**Context.** The backend hosts: REST endpoints for run management, an
MCP server (FastMCP, mounted), an SSE endpoint for dashboard live feed,
and a long-running orchestrator task.

**Alternatives considered.**
- FastAPI — large community, MCP SDK examples align, mature
  streaming-response patterns
- Litestar — tighter primitives (msgspec), smaller community
- Starlette + raw — lowest level, most flexibility, most boilerplate
- BlackSheep — fast, smaller community, weaker docs

**Decision.** FastAPI ≥ 0.115 + Pydantic v2 + Uvicorn.

**Rationale.** The MCP Python SDK's reference examples are FastAPI-shaped
(`FastMCP.streamable_http_app()` mounts into a FastAPI app). FastAPI is
the safer ecosystem bet for greenfield Python web work in 2026. Litestar
is technically tighter but has a smaller community and weaker MCP story.

**Consequences.**
- Long-running orchestrator runs as an `asyncio.TaskGroup` task inside
  FastAPI's `lifespan` context manager — not via `BackgroundTasks` (wrong
  primitive) and not via Celery/Dramatiq (overkill for single-host).
- OpenAPI schema is auto-generated for the Vue dashboard's typed client.

---

## ADR-08 — Vue 3 + Pinia + SSE for the frontend

**Status:** Accepted (2026-05-19)

**Context.** v1's dashboard is Flask-rendered HTMX. Goals 1 and 2 from
`motivation.md` (live observability, replay) require a richer UI.

**Alternatives considered.**
- Keep HTMX + Alpine — cheap, fast to ship, lower JS ecosystem burden
- React + Tanstack Query — large pool, mature
- Vue 3 + Pinia — clean composition API, smaller bundle, Pinia Colada
  pairs cleanly with REST + SSE

**Decision.** Vue 3 + Pinia v3 + Pinia Colada for server-state caching.
SSE (via browser `EventSource`) for the live event feed.

**Rationale.** The dashboard needs multi-pane state (timeline, drill-down
on tool calls, subagent tree, filter-as-you-type, replay scrubber). HTMX
gets you ~70% of that with significant friction at the upper bound;
modern reactive frameworks pay back fast for this UI shape. Vue chosen
over React for smaller bundle, less ceremony, and the user's stated
preference. SSE over WebSocket because the transport is server→client
only (live event tail) and `EventSource` provides free reconnection via
`Last-Event-ID`.

**Consequences.**
- The frontend is a separate `frontend/` directory; build via Vite.
- Typed client generated from the FastAPI OpenAPI schema.
- The MVP UI shape is described in `spec.md`.

---

## ADR-09 — Pi + Max subscription auth (verification required)

**Status:** Provisional (2026-05-19)

**Context.** Hard constraint from `motivation.md`: relay must use the
user's Claude Max subscription, not pay-as-you-go API keys. Pi's public
documentation states that subscription-OAuth usage is billed per-token
outside the plan cap — structurally the same separation Anthropic is
introducing on 2026-06-15 for the Claude Agent SDK and `claude -p`. The
user has indicated a working path exists but has not elaborated.

**Alternatives considered.**
1. Accept pi's documented per-token billing and architect around it.
2. Assume the user's path holds (Max quota covers relay usage) and treat
   it as a verifiable assumption.
3. Re-evaluate harness choice (back to `claude -p` or another option).

**Decision.** Option 2, provisional. Architect for ADR-03 (pi as primary)
under the assumption that the user's Max-subscription path is viable.
This ADR is tagged `provisional` until the path is documented and
verified empirically with usage telemetry.

**Rationale.** The architecture survives if the assumption fails — the
harness is swappable per ADR-04, and `claude -p` remains a viable
fallback (at the cost of accepting the June 15 billing split). The
decision to delay verification is pragmatic: the user has the private
context and has not yet shared the mechanism; blocking design on this is
not cost-effective.

**Consequences.**
- The provisional tag stays until the path is verified.
- A note in `risks.md` (or motivation's risks section) tracks this until
  resolved.
- If verification fails, ADR-03 is reopened.

---

## ADR-10 — Owned event log + OTel export to Langfuse

**Status:** Accepted (2026-05-19)

**Context.** v1 stores per-iter JSONL files and re-parses them on demand.
v2 goal 7 calls for a structured event store as the source of truth.

**Alternatives considered.**
1. Roll-your-own structured event log in SQLite/Postgres only.
2. Use OpenTelemetry traces as the primary store (export to Jaeger/Tempo/
   Phoenix).
3. Use a vendor LLM-observability platform (Langfuse, Phoenix, Logfire,
   LangSmith) as the primary store.
4. Hybrid: own the event log; mirror to OTel as a bolt-on export.

**Decision.** Option 4. The event log in SQLite (MVP) / Postgres (later)
is the source of truth. Every action — `iter_start`, `tool_use_start`,
`tool_use_end`, `signal_emit`, `phase_change`, `subagent_dispatch`,
`file_edit`, etc. — is an append-only row. The dashboard's SSE feed
tails it. Replay re-streams by `run_id`.

Mirrored to OpenTelemetry spans, exported to Langfuse (self-hosted,
MIT-compatible) as a bolt-on. Langfuse chosen over Phoenix because
prompt-management is a first-class feature there and relay's roadmap
includes a prompt-library entity.

**Rationale.** Owning the hot path means sub-second latency for "what is
happening now" is a function of fsync cadence, not vendor ingestion
lag. The dashboard reads the same store the orchestrator writes to.
Vendor observability platforms are useful but not load-bearing — making
Langfuse required would create a deployment dependency the project
doesn't need. The export is opt-in (`RELAY_OTEL_EXPORT=langfuse|none`).

**Consequences.**
- An `events` table with `run_id`, `iter`, `seq`, `ts`, `type`, `payload`.
- Append-only — no in-place updates. Status transitions are new events.
- OTel SDK + a Langfuse exporter wired into the orchestrator at startup.
- The illustrative column names and event-kind enumeration above
  (`iter_start`, `phase_change`, `file_edit`, …) are non-canonical;
  the authoritative schema is `spec.md` §3.1 (column names: `run_id`,
  `iter_id`, `seq`, `ts`, `kind`, `payload`) and the authoritative
  event-kind taxonomy is `spec.md` §3.2.

---

## ADR-11 — SQLite for MVP, Postgres path later

**Status:** Accepted (2026-05-19)

**Context.** v2 needs a database for the event log, run table, prompt
table, etc. Single-user MVP means concurrent writer pressure is low.

**Decision.** SQLite via `sqlalchemy` (or `sqlmodel`) for MVP. Schema
designed to migrate cleanly to Postgres when multi-user or higher
concurrency arrives.

**Rationale.** Zero-config for the single-user case. File-based storage
matches the project's existing on-disk discipline (per-run namespacing
still applies — the SQLite file lives under the project's relay data
directory). Postgres adds operational burden that's wasted in single-user
mode; SQLAlchemy abstracts the SQL dialect cleanly.

**Consequences.**
- Schema must avoid SQLite-only features. Use `JSONB` (Postgres) /
  `JSON` (SQLite) for flexible columns via SQLAlchemy's `JSON` type.
- Multi-user / high-concurrency adds Postgres as a deployment step, not
  a rewrite.

---

## ADR-12 — Single-user localhost MVP

**Status:** Accepted (2026-05-19)

**Context.** Earlier discussion considered multi-user as the v2 target.
PM analysis pushed back: the real pain is "drive it from my phone,
laptop-sleep-proof" — that's single-user-hosted, not multi-user.

**Decision.** v2 MVP ships single-user, localhost-only. Multi-user is a
non-goal explicitly. Architecturally, `user_id` is a first-class FK from
day one — adding multi-user later is additive, not a rewrite.

**Rationale.** Multi-user adds RBAC, auth flows, isolation between users'
projects, credential management, audit logging, and per-user rate limits.
Three months of plumbing nobody currently needs. Single-user hosted
(post-MVP) gets ~80% of the multi-user value: remote access from any
device, laptop-sleep durability, share-a-link-to-a-run. Implementable in
a single subsequent phase.

**Consequences.**
- No auth in MVP. The orchestrator binds to localhost.
- `user_id` columns exist but default to a single sentinel value.
- Post-MVP Phase 9 (per `plan.md`) adds VPS deployment + GitHub OAuth
  gated to one user.

---

## ADR-13 — Container-per-run isolation deferred to post-MVP

**Status:** Accepted (2026-05-19), deferred

**Context.** v1 uses git worktrees inside the user's repo for run
isolation. v2 could move to container-per-run (ephemeral container,
clone-on-the-fly from GitHub).

**Decision.** Keep git worktrees for v2 MVP. Container-per-run is
post-MVP (Phase 10 in `plan.md`).

**Rationale.** Worktrees work today and the same orchestrator can drive
either model. Container-per-run unlocks blast-radius isolation and
multi-user, but neither is required for MVP. Building it adds Docker
infrastructure, GitHub PAT/App handling, and container lifecycle
management — all premature for single-user-localhost.

**Consequences.**
- Worktree handling ports from v1 to v2 with structural cleanups
  (driver-side `current.txt` mirror, no Phase-3-skill responsibility).
- The orchestrator's "spawn a run" abstraction is designed so swapping
  worktree-based isolation for container-based isolation later is a
  single-component change.

---

## ADR-14 — engineering-team skill ports to v2 with revised preamble

**Status:** Accepted (2026-05-19), implementation deferred

**Context.** v1's `engineering-team` skill is the source of truth for the
multi-phase orchestration. v2 needs a ported version with v2-shaped
preamble and signaling.

**Decision.** Port the skill to `relay-v2/skills/engineering-team/` with:
- Preamble fields: `RELAY_PHASE`, `RELAY_RUN_DIR` remain. Add any new
  fields needed by v2's run model (TBD during build).
- Signaling: text sentinels initially (per ADR-05), with the option to
  graduate to MCP tools later. Schema may evolve.
- Phase docs structure preserved (evaluate → plan → develop → wrap-up).
- v1's skill remains in v1's repo until v2 ships and deprecates v1.

**Consequences.**
- The skill is part of v2's repo. `relay install-skill` (or equivalent)
  deploys it to the user's Claude skills directory.
- v2's parser tests duplicate the contract (per ADR-05's strategy
  pattern); the test fixtures port from v1's `tests/test-parsing.sh`.
- The sentinel grammar may be revised — opportunity to clean up the
  prompt-marker contract while we're rewriting anyway.

---

## ADR-15 — Dashboard is the primary control plane

**Status:** Accepted (2026-05-19)

**Context.** Earlier drafts of the spec inherited v1's "dashboard never
writes" invariant. That invariant existed in v1 because the bash driver
and the Flask dashboard were separate processes, and the safety belt
prevented dashboard bugs from corrupting state the bash driver managed.
In v2, the orchestrator, REST routes, MCP server, and dashboard all
share one Python process; the safety belt is no longer needed.

Independently, the CLI is poor at the things relay users actually do
during the *review* phase of using the tool: reading rendered artifacts
(markdown, mermaid diagrams, code), previewing prompts before
committing to a run, comparing run outputs, and discovering what the
tool can do. A dashboard-driven flow — browse, render, preview, start —
is substantively safer and easier than the CLI default for new and
returning users alike.

**Alternatives considered.**
1. Keep the dashboard read-only; the CLI is the primary control
   surface (v1's posture).
2. Make the dashboard the primary control plane; the CLI is the
   secondary automation interface.
3. Hybrid: dashboard for reads, CLI for writes, both equally
   first-class.

**Decision.** Option 2. The dashboard is the primary user-facing
control plane. The CLI is preserved as a scripting / automation
interface but is no longer the assumed default.

**Rationale.**
- The dashboard substantively reduces the "commitment without preview"
  risk of starting runs.
- Plans, evaluation reports, and other artifacts are rendered properly
  (markdown, mermaid, syntax-highlighted code, diffs) — the form in
  which a human can actually evaluate them.
- Discoverable affordances replace "remember CLI commands" for
  returning users.
- Implementation cost is modest (~4 extra MVP days; phase 3 + phase 4
  in `plan.md`).
- The "all writes go through `RelayCore`" invariant from ADR-07
  already enforces the safety property that v1's read-only-dashboard
  rule was protecting. v2 keeps the property while removing the
  artificial restriction.

**Consequences.**
- `spec.md` section 2 narrative drops the "dashboard never writes"
  framing.
- New REST endpoints: file browser (read), project CRUD (write), run
  preview (read), prompt CRUD (already specified). All sandboxed and
  routed through `RelayCore`.
- The MVP dashboard scope grows: file browser pane, New Run wizard
  (with preview step), project / prompt CRUD forms, rendered-plan
  inspection.
- The CLI shrinks in scope to "things a human would script" — start a
  run from a prompt file, list runs, cancel by id. It does not
  disappear.
- The MVP plan's day budget grows from ~24 to ~28 days (phase 3 +1,
  phase 4 +3).

---

## ADR-16 — Use `--mode json` for the MVP pi invocation

**Status:** Accepted (2026-05-19). Amends ADR-03's invocation form.

**Context.** ADR-03 selected pi as the v2 harness and named
`PI_AGENT_SDK=1 pi --mode rpc` as the invocation form. During
de-risking (see `scratch/pi_derisk_workdir/findings.md`), `--mode json`
was used instead — one-shot subprocess with JSONL stdout — and proved
to be the better fit for relay's "one iter, one subprocess" model.

**Alternatives considered.**
1. `pi --mode rpc` — bidirectional JSONL on stdin/stdout, persistent
   session, suitable for tools that need to send commands to a running
   pi instance.
2. `pi --mode json` (used in de-risking) — one-shot, prompt on argv,
   JSONL stream on stdout, subprocess exits when the agent finishes.

**Decision.** Use `--mode json` for the MVP. Reserve `--mode rpc` for
future use cases that need bidirectional control (e.g., richer
cancellation, dynamic mid-session reconfiguration).

**Rationale.**
- Simpler subprocess lifecycle: spawn, read stream, wait, done. Maps
  directly onto v2's per-iter execution model.
- The de-risking suite was built on `--mode json`; all event-mapping
  decisions in `spec.md` §4.2 are grounded in its concrete behavior.
- `--mode rpc`'s primary value is interactive command flow during a
  session — relay doesn't need that within an iter's lifetime. Signals
  come via the signaling layer (sentinels in MVP); cancellation comes
  via process signals.

**Consequences.**
- ADR-03's invocation-form sentence is superseded by this ADR. ADR-03
  has been amended to point here; the rest of ADR-03 stands.
- `spec.md` §2 architecture diagram shows `subprocess(pi --mode json)`.
- `spec.md` §4.2 cites this ADR rather than ADR-03 for the invocation
  form.
- If a future need surfaces for `--mode rpc` (e.g., explicit RPC
  `abort` over a running session, dynamic settings change mid-session),
  a new ADR will reopen the choice.

---

## ADR-17 — Hand-rolled `create_all` schema management for the MVP

**Status:** Accepted (2026-05-19)

**Context.** `plan.md` Phase 0 lists `db/migrations/` with the note
"alembic or hand-rolled" — a decision explicitly delegated to
implementation. Phase 0's only schema-related verification criterion is
"first serve creates `<cwd>/.relay/relay.db` with schema migrated". The
schema is greenfield: there is no production data, no deployed instance,
and (per ADR-12) a single user on localhost SQLite.

**Alternatives considered.**
1. Adopt Alembic from Phase 0 — versioned migration scripts, autogenerate,
   an `alembic/` env and `alembic.ini`.
2. Hand-rolled `Base.metadata.create_all()` at startup, with a
   `db/migrations/` package reserved for future numbered upgrade scripts.

**Decision.** Option 2. `relay_v2.db.init_db()` calls
`Base.metadata.create_all()` on first serve. `db/migrations/` is a
documented placeholder for numbered `upgrade()/downgrade()` scripts; the
SQLAlchemy models in `db/models.py` are the schema source of truth,
faithfully porting spec.md §3.1 (which remains canonical).

**Rationale.** Until the schema changes under data that must be
preserved, a migration framework is pure overhead. `create_all` is
idempotent, satisfies the Phase 0 criterion exactly, and keeps the
scaffold minimal. The schema lives in typed SQLAlchemy models either
way, so adopting Alembic later is additive (autogenerate can diff
against the existing models) rather than a rewrite. This does **not**
change spec.md — §3.1's DDL stays canonical; the models mirror it.

**Consequences.**
- No migration history exists yet; the first schema change under live
  data triggers the migration story.
- `db/migrations/__init__.py` documents the convention for the first
  numbered script.
- A later switch to Alembic, if made, will be recorded as its own ADR
  (this entry is not edited).
- The Phase 0 engine is synchronous; the async engine arrives with the
  orchestrator (Phase 2) and stays encapsulated in `relay_v2.db`.

---

## ADR-18 — `message_update` sub-type handling; `AssistantText.kind`

**Status:** Accepted (2026-05-19). Amends the spec.md §4.2 mapping table
and the §4.1 `AssistantText` dataclass.

**Context.** spec.md §4.2's pi→`HarnessEvent` table only mapped
`message_update` events whose `assistantMessageEvent.type == "text_delta"`.
Inspecting `scratch/pi_derisk_workdir/test_event_shapes.jsonl` (the OQ-1
fixture) showed pi actually emits a per-content-block stream with three
families of sub-types, each framed `*_start` → `*_delta` → `*_end`:

- `text_*` — the user-visible assistant response.
- `thinking_*` — model chain-of-thought (extended-thinking deltas).
- `toolcall_*` — the streamed tool-call being assembled.

Concatenated `text_delta`s equal the block's `text_end.content` in every
captured stream (resolves OQ-2). `thinking_delta` carries reasoning text;
`toolcall_*` duplicates information already delivered authoritatively by
the separate `tool_execution_start/update/end` events (the events spec.md
§4.2 already maps). The Phase 1 risk note in plan.md anticipated this and
proposed "pass unknown sub-types through as `AssistantText` with a `kind`
tag" — but a naïve pass-through would feed model reasoning into the
sentinel parser, where a sentinel *mentioned while thinking* would fire a
false signal.

**Alternatives considered.**
1. Surface only `text_delta`; silently drop `thinking`/`toolcall`. Loses
   reasoning observability the dashboard (spec.md §9) will want.
2. Pass every sub-type through as `AssistantText` undifferentiated (the
   literal plan.md mitigation). Causes false sentinel matches from
   chain-of-thought — a correctness bug in the load-bearing signal path.
3. Surface `text` and `thinking` as `AssistantText` but tag each with a
   `kind`; consume `toolcall_*` and all `*_start`/`*_end` framing
   internally; restrict signal detection to `kind == "text"`.

**Decision.** Option 3.

- `AssistantText` gains `kind: Literal["text", "thinking"]` defaulting to
  `"text"` (keeps spec.md §4.1's two-field constructor working).
- `text_delta` → accumulate per turn → `AssistantText(kind="text")` at
  `turn_end`. `thinking_delta` → accumulate → `AssistantText(kind="thinking")`.
- `toolcall_*` and every `*_start`/`*_end` framing event, plus any
  *unrecognised* sub-type or top-level pi event, are consumed internally
  and surface nothing (graceful forward-compat with pi's weekly releases).
- The `text_sentinels` strategy (spec.md §5.1) inspects **only**
  `AssistantText` with `kind == "text"`. This is the v2 form of v1's
  anti-mention discipline: v1's `jq` filter stripped tool inputs before
  parsing; v2 additionally never lets `thinking` text reach the parser.

**Consequences.**
- spec.md §4.1 `AssistantText` carries `kind`; §4.2 gains the
  `thinking_delta` row and an explicit "framing + unknown sub-types
  consumed internally" note; §5.1 states the `kind == "text"`
  restriction. spec.md §13 OQ-1 and OQ-2 are marked resolved.
- pi *does* surface token + cost (`messages[].usage`, including
  `cost.total`) in `agent_end` — a partial answer to OQ-3, recorded here
  but not consumed until observability (Phase 7).
- The harness-isolation invariant (ADR-04) is unchanged: all of this
  lives in `harness/pi.py`; the orchestrator still sees only normalized
  events.

---

## ADR-19 — Orchestrator runtime: queue + supervised task set

**Status:** Accepted (2026-05-19). Refines plan.md Phase 2's
"`asyncio.TaskGroup` in lifespan" sketch.

**Context.** plan.md Phase 2 says the orchestrator task is "created via
`asyncio.TaskGroup` in `lifespan`, consuming an `asyncio.Queue` of
run-start requests from `RelayCore`". A literal
`async with asyncio.TaskGroup()` cannot stay open while continuing to
accept new tasks for the server's lifetime — the block only exits when
every child is done, the opposite of an open-ended daemon.

**Alternatives considered.**
1. Literal `async with TaskGroup()` wrapping the whole server lifetime —
   doesn't fit: can't accept new runs after entering; wrong exit
   semantics for a daemon.
2. One detached `asyncio.create_task` per run, untracked — leaks tasks,
   no clean shutdown, exceptions silently swallowed.
3. A long-lived **supervisor** task draining an `asyncio.Queue`, owning
   a tracked child-task set, bracketed by `RelayCore.start()` /
   `aclose()` and bound to FastAPI's lifespan.

**Decision.** Option 3. `RelayCore` owns the `asyncio.Queue`, a
supervisor coroutine, and a `set[asyncio.Task]` of in-flight runs.
`start()`/`aclose()` are driven by the app's `lifespan` (ADR-07). This
is the open-ended-server equivalent of plan.md's TaskGroup intent — same
structured-concurrency guarantees (every run tracked; shutdown cancels
and drains them) without the closed-scope mismatch.

**Consequences.**
- `RelayCore` is the single shared service object (ADR-07/ADR-15); the
  loop, and later REST/MCP, mutate state only through it. Route handlers
  are deliberately *not* anticipated in Phase 2.
- `aclose()` cancels the supervisor then every run task, swallowing both
  `CancelledError` and run exceptions so shutdown can't stall.
- The per-iter wall-clock cap (`runs.iter_timeout`) and external
  cancellation are the orchestrator's job (the harness reports only the
  lower-level stop_reason); a `try/finally` in the iter driver
  guarantees the pi subprocess is terminated even on shutdown
  cancellation.
- spec.md §6 gains a "Runtime model" subsection; the canonical loop
  pseudocode is unchanged.

---

## ADR-20 — Pause/resume persistence and resume-prompt composition

**Status:** Accepted (2026-05-19).

**Context.** A `pause` signal closes an iter and the run with
`status=paused`; the human later answers and the run must continue. The
saved next-prompt must survive a process restart, and the answer must
reach the agent *without* violating fresh-context-per-iter (it must not
arrive via pi session resume).

**Alternatives considered.**
1. Hold the paused next-prompt in memory only — lost on restart; resume
   after a crash impossible.
2. Add a dedicated `runs.next_prompt` column — schema churn for
   transient state the `iters` table already models.
3. Persist `{next_prompt, question, id}` in the pausing iter's existing
   `iters.signal_args` JSON (spec.md §3.1 already documents this column
   as `{next_prompt, summary, question, ...}`); recompose on resume.

**Decision.** Option 3. On `pause` the loop writes `signal_args =
{next_prompt, question, id}` and a `pause_requested` event. `resume_run`
reads the latest paused iter's `signal_args`, composes the resumed iter's
body as the saved `next_prompt` followed by a delimited
`Answer to the paused question (...)` block, sets `runs.status` back to
`running`, emits `pause_resolved`, restores the phase from
`$RELAY_RUN_DIR/phase`, and re-enqueues continuing at the next `seq`.

**Consequences.**
- Fresh-context-per-iter holds: the answer travels in the prompt body,
  never via `resume_from` (still always `None`).
- The check-and-enqueue in `resume_run` is serialised by an
  `asyncio.Lock` + an in-memory liveness guard so a duplicate resume
  cannot spawn two loops for one run (→ `UNIQUE(run_id, seq)` violation).
  Single-user MVP (ADR-12) makes contention rare; the guard is cheap and
  the correct pattern before Phase 3 wires HTTP.
- Projection-then-event ordering matches the loop's other transitions so
  ADR-10 consumers (Phase 3 SSE) see a consistent status when the event
  lands. No schema change; spec.md §6 documents the contract.

---

## ADR-21 — Async (`aiosqlite`) engine for orchestrator I/O

**Status:** Accepted (2026-05-19). Executes the consequence ADR-17
anticipated; recorded because it adds a runtime dependency.

**Context.** ADR-17's consequences state: "The Phase 0 engine is
synchronous; the async engine arrives with the orchestrator (Phase 2)
and stays encapsulated in `relay_v2.db`." The orchestrator runs in an
asyncio loop (ADR-19); synchronous SQLite calls there would block it.

**Decision.** Add a second engine — `create_async_engine` over
`sqlite+aiosqlite://` (30s busy timeout) plus an `async_sessionmaker` —
alongside the existing sync engine, both behind `relay_v2.db`. The
**sync** engine still does one job: idempotent `create_all` schema
bootstrap (ADR-17). Every orchestrator-driven read/write uses the
**async** engine. New deps: `aiosqlite` and `sqlalchemy[asyncio]`
(pulls `greenlet`).

**Consequences.**
- Nothing above `relay_v2.db` constructs an engine; harness isolation
  and "all writes through `RelayCore`" are unaffected.
- `EventStore` serialises its own appends with an `asyncio.Lock`
  (monotonic per-run `seq`), which also serialises SQLite's single
  writer; the busy timeout absorbs residual cross-run contention.
- This does **not** change spec.md §3.1 — the schema is unchanged, only
  the access path gains an async engine. ADR-17 is not edited (it
  foresaw this); this ADR records the dependency addition.

---

## ADR-22 — Resume guarantees forward progress past `max_iters`

**Status:** Accepted (2026-05-19). Fixes a Phase 2 boundary defect found
in the pre-Phase-3 evaluation (`evaluation-report.md`).

**Context.** `run_loop` bounds iters with `while seq < max_iters` and
`resume_run` re-enqueues a paused run at `start_seq = paused.seq`. When a
run pauses on its last budgeted iter (`paused.seq == max_iters`), the
resumed loop's condition `max_iters < max_iters` is immediately false:
the loop body never runs and the run ends `failed/max_iters` the instant
it is resumed — discarding the human's answer with no explanation. A
pause is an explicit human "continue" instruction; ending it as a
budget-exhaustion failure is wrong.

**Decision.** A resumed run is guaranteed *at least one* post-answer
iter. `run_loop` computes an effective cap
`effective_max = max(ctx.max_iters, ctx.start_seq + 1)` and bounds the
loop with that. For a fresh run (`start_seq == 0`) this is exactly
`max_iters` — behavior is unchanged. For a resume it is at least
`paused.seq + 1`, so the answer is always processed in at least one
iter.

**Rejected alternatives.**
- *Pause never counts against the cap* (decrement the budget on resume):
  a larger behavior change that makes the iter budget hard to reason
  about across multiple pauses; rejected as scope creep for the MVP.
- *Succeed-and-stop when resumed at the cap*: silently drops the user's
  answer; defeats the purpose of resume.

**Consequences.**
- A run that pauses at the cap and is resumed can run one iter beyond
  `max_iters`. This is intentional and bounded (one extra iter per
  resume) — a human chose to continue.
- No schema change. spec.md §6.2 documents the effective-cap contract;
  ADR-20 (pause/resume persistence) is unaffected.

---

## ADR-23 — SSE broadcaster + Last-Event-ID replay/cutover + finished-run close

**Status:** Accepted (2026-05-19). Implements the Phase 3 SSE feed
(`GET /api/events/:run_id`, spec.md §7/§9.3) on top of the existing
event store without violating ADR-10.

**Context.** The dashboard needs a live SSE tail of a run's events with
`Last-Event-ID` reconnect, and a replay of a finished run that closes.
ADR-10 makes the `events` table the single source of truth: SSE must be
a passive *reader*, never a writer, and must not perturb the
orchestrator loop, the harness, or per-run `seq` assignment. Two
correctness hazards: (1) the replay→live cutover can drop or duplicate
the event(s) committed while history is being read; (2) a slow SSE
client must not be able to stall the single `EventStore.append`
chokepoint (which also serialises SQLite's single writer).

**Decision.**

* **Post-commit passive observer.** A single in-process `Broadcaster`
  (per-`run_id` registry of subscriber `asyncio.Queue`s) is attached to
  the *one* `EventStore.append` chokepoint. `append` calls
  `broadcaster.publish(...)` **after** the row is committed and its
  `seq` is final, never before. A publish failure is caught and logged
  and never breaks the append. The broadcaster owns no event data,
  assigns no seq, and writes nothing — ADR-10 is preserved. It lives
  behind `RelayCore` (ADR-07/15); routes reach it via
  `app.state.core.broadcaster` and never construct one.

* **Slow-consumer policy: bounded queue, close-on-full.** Each
  subscriber gets a bounded `asyncio.Queue(maxsize=256)`. `publish` is
  non-blocking (`put_nowait`); the only `await` in `publish` is the
  registry lock, so a slow subscriber can never stall `append`. On a
  full queue the broadcaster evicts the oldest item and enqueues a
  `CLOSED` sentinel; the route ends that connection cleanly and the
  browser reconnects with `Last-Event-ID`, whereupon the replay path
  backfills the gap with zero loss. *Rejected: drop newest/oldest and
  keep streaming* — that is a silent, unrecoverable gap in a still-open
  stream, strictly worse than a clean close the client transparently
  recovers from.

* **Subscribe-before-replay + `seq > max_replayed_seq` cutover.** For a
  live run the route subscribes to the broadcaster **first**, then
  replays DB history with `seq > Last-Event-ID` (paginated) tracking
  `max_replayed_seq`, then drains the live queue forwarding only events
  with `seq > max_replayed_seq`. Subscribing first guarantees an event
  committed during replay lands in the queue (no gap); the cutover
  filter discards the queue copy of any event already emitted during
  replay (no duplicate). Result: contiguous, gap-free, duplicate-free
  ordering across reconnects.

* **Finished-run = paginated history then EOF; 204 only when empty.**
  A run whose status is terminal (`done`/`failed`/`cancelled`) will emit
  no further events. `paused` is **not** terminal (it can resume) and is
  treated as live. The plan's "204 on exhaustion" is interpreted
  precisely: a `StreamingResponse` cannot send a 204 mid-stream, so the
  route returns a real `204 No Content` **before** starting the stream
  *only when* a finished run has zero events at/after `Last-Event-ID`;
  if there ARE such events it streams them paginated then ends the
  generator (clean EOF — the browser stops reconnecting to a finished
  run on a clean close). A `?last_event_id=` query parameter is accepted
  as a fallback for non-browser clients that cannot set the header; the
  header wins when both are present.

* **`X-Accel-Buffering: no`.** Sent alongside `Cache-Control: no-cache`
  and `Connection: keep-alive`. Without it an nginx reverse proxy
  buffers the response and SSE events arrive in bursts or stall instead
  of live (plan.md Phase 3 risk note).

**Consequences.**
- ADR-10 holds: SSE adds no write path; the event store stays the single
  source of truth and the only seq authority.
- A wedged client costs at most one bounded queue then a forced clean
  reconnect; it cannot block `append` or other subscribers.
- One extra `refresh` per append surfaces the server-defaulted `ts` for
  the SSE payload; no schema change, append's return value and seq logic
  are unchanged (additive hook).
- `EventStore` remains usable with no broadcaster (default `None`) for
  headless orchestrator tests and scripts.
- spec.md §7 gains a pointer to this ADR for the replay/cutover/close
  contract (additive note, no rewrite).

---

## ADR-24 — `pytest-asyncio` (`asyncio_mode="auto"`) + `httpx.AsyncClient` for the API test suite

**Status:** Accepted (2026-05-19). Phase 3 toolchain addition; recorded
because it changes how tests are written and run.

**Context.** Phases 1–2 test async code by wrapping each case in
`asyncio.run()` and driving `RelayCore` directly — no HTTP. Phase 3 adds
an ASGI surface (FastAPI). Exercising routes (and the SSE stream) end to
end needs an async HTTP client driven inside the test's event loop, and
dozens of `async def` route tests would each need a manual loop wrapper.
`pytest-asyncio` and `httpx` were already declared dev-deps in Phase 0
but unused (`asyncio_mode` unset, so bare `async def test_*` was silently
skipped, not run).

**Alternatives considered.**
1. Keep the `asyncio.run()` wrapper pattern for API tests too — every
   route test carries boilerplate; SSE streaming tests become awkward.
2. FastAPI's sync `TestClient` (Starlette `TestClient`) — spins its own
   loop in a thread; fights the lifespan-owned `RelayCore` /
   `aiosqlite` async engine and the SSE generator. Poor fit for an
   async-native stack.
3. `pytest-asyncio` with `asyncio_mode="auto"` + `httpx.AsyncClient`
   over `ASGITransport`, entering `app.router.lifespan_context` so the
   real lifespan builds the shared `RelayCore`.

**Decision.** Option 3. Add `asyncio_mode = "auto"` to
`[tool.pytest.ini_options]`; the API suite (`tests/api/`) uses
`httpx.AsyncClient(transport=ASGITransport(app=app))`. Also add
`openapi-spec-validator` as a dev-dep to assert the auto-generated
schema is structurally valid OpenAPI v3 (plan.md Phase 3 verification),
rather than eyeballing `curl /openapi.json`.

**Rationale.** `auto` mode runs bare `async def test_*` with no
per-test marker, so API tests stay clean. It is **backward compatible**:
the Phase 1/2 suites call `asyncio.run()` *inside* sync `def test_*`
functions — pytest-asyncio does not touch sync tests, so those suites
are unaffected (verified: full suite green after the switch).
`httpx.AsyncClient` shares the test's loop with the lifespan-owned
`RelayCore`, so a scripted-harness run started via `POST /api/runs` and
the SSE generator both work without thread/loop seams.

**Consequences.**
- New dev-deps: `pytest-asyncio` and `httpx` graduate from declared-but-
  unused to load-bearing; `openapi-spec-validator>=0.7` added.
- New convention: `tests/api/` uses bare `async def` + `AsyncClient`;
  `tests/orchestrator/` and `tests/harness/` keep the `asyncio.run()`
  pattern. Both coexist under one `asyncio_mode="auto"` config.
- CLAUDE.md's toolchain section is updated to record the test-runner
  convention so it stays accurate (per the project doc policy).
- No production code or runtime dependency change — test tooling only.

---

## ADR-25 — Run-artifacts file browser as a second sandboxed root

**Status:** Accepted (2026-05-19). Phase 4 backend pre-step; recorded
because it adds a REST surface and a second sandbox root.

**Context.** spec §9.1's Artifacts pane browses a run's
`<data_dir>/runs/<run_id>/` directory — the agent's
`improvement-plan.md`, `evaluation-report.md`, `discussions/`, etc. —
"where the user reviews what did the agent actually do?" The Phase 3
file browser (`api/files.py`, ADR via plan.md W3) is sandboxed to a
project's `root_path`. Per spec §3.3 the artifacts dir is a *sibling of
the worktree*, deliberately outside any project root — so it is
unreachable through the existing file-browser endpoint. A gap analysis
for Phase 4 flagged this as the one hard blocker; the dashboard cannot
ship its core review surface without it.

**Alternatives considered.**
1. Widen the project file browser's sandbox to also allow the data dir
   — rejected: collapses two distinct trust roots into one, weakens the
   single-audited-sandbox property, and entangles project paths with
   relay-internal storage.
2. A general "read any path" endpoint with the run dir passed in —
   rejected: reintroduces exactly the traversal surface the sandbox
   exists to remove.
3. A second, run-scoped browser that derives its root from the run id
   and reuses the *same* audited resolver — chosen.

**Decision.** Add `GET /api/runs/{run_id}/artifacts` (listing) and
`GET /api/runs/{run_id}/artifacts/{file_path:path}` (content). The
sandbox root is `settings.data_dir / "runs" / <run_id>`, derived
server-side from the path segment — never client-supplied. The run must
exist (`RelayCore.get_run`) → 404 otherwise; if the run exists but its
artifacts dir does not, 404 `no artifacts for run`. The single audited
`resolve_within_sandbox` from `api/files.py` is reused unchanged, and
the directory-listing / file-content bodies are extracted into shared
`serve_listing` / `serve_file` helpers that *both* the project file
browser and the artifacts browser call — so there is exactly one
audited confinement function and one serving implementation, not two
(addresses the Phase 3 review's duplicate-logic finding proactively).
Same guards as the project browser: binary (NUL in first 8 KiB) → 415,
> 5 MiB → 413, read-only (no write/delete route).

**Consequences.**
- Two sandbox *roots* (project root; per-run artifacts dir) but one
  audited resolver and one serving path — security surface stays
  single-sourced.
- Read-only and run-scoped; no `RelayCore` write path, no new engine
  use. The route reads `app.state.settings.data_dir` (set by the
  lifespan) and resolves the run via the existing `get_core` →
  `get_run` (ADR-07/15 preserved — the route is a thin adapter).
- spec §7 gains the two routes; spec §9.1's Artifacts pane is now
  backed. The Worktree pane's live git status/diff remains a deliberate
  post-MVP gap (Phase 4 scoping decision — degrade to read-only
  `worktree_path`/`branch`).

---

## ADR-26 — Phase-4 frontend toolchain mandates + library currency calls

**Status:** Accepted (2026-05-19). Records the Phase-4 dashboard
implementation decisions; the plan.md stack was kept (no swaps) but
five libraries had moved since the plan was written and needed precise
usage, plus two implementation calls.

**Context.** `docs/plan.md` Phase 4 names a frontend stack (Vue 3,
Pinia, Pinia Colada, vue-router, markdown-it, shiki, mermaid,
diff2html, vitest, openapi-typescript). A 2026-05 currency review
(scoping discussion `260519-phase-4-dashboard-scope.md`) cleared both
load-bearing risks (Pinia Colada 1.3 ships native key invalidation;
openapi-typescript 7 has full OpenAPI-3.1 support with openapi-fetch as
the runtime companion) so the stack was kept whole. But five details
were under-specified or had drifted and would silently regress if not
pinned down, and two implementation choices had to be made.

**Decision — the five mandates (implemented, not deferred).**

1. **vue-router is v5**, not the v4 the plan implies. Adopted directly;
   non-breaking for plain `createRouter`/`createWebHistory`.
2. **shiki** is built with `createHighlighterCore` (`shiki/core`) + the
   JavaScript regex engine (`@shikijs/engine-javascript`) + per-language
   grammars dynamically `import()`ed and cached — **never** the
   convenience bundle / `getHighlighter` / `bundledLanguages` (it pulls
   megabytes of TextMate grammars into the main chunk). Type-only
   `import type` from `shiki/core` is fine (build-erased).
3. **mermaid** is loaded via dynamic `import('mermaid')` on first
   diagram render only, cached thereafter — **never** a static
   top-level import (it is the single heaviest dependency).
4. **Vite SSE dev-proxy**: the `/api` proxy sets an explicit long
   `proxyTimeout` and, for `text/event-stream` responses, disables
   buffering (`X-Accel-Buffering: no`, `flushHeaders()`), or the live
   SSE tail stalls in dev.
5. **vitest v4 has no `coverage.all` toggle.** The plan said "set
   `coverage.all` explicitly"; verified against vitest 4's
   `CoverageOptions` type the option was **removed** — v4
   unconditionally counts every file matched by `coverage.include`
   (the old `all: true` behavior is now the only behavior). The mandate
   intent is met by explicitly scoping `coverage.include` to
   `src/**/*.{ts,vue}` with generated/config/test excludes. The literal
   mandate text predates the v4 type change; recorded here so it is not
   "fixed" back to a now-invalid option.

**Decision — two implementation calls.**

- **diff2html kept** (not swapped for v-code-diff). spec §9.4 and
  plan.md both prescribe it; it is an already-pinned dep (`^3.4`); only
  its stable `html()` formatter is used (the unified patch is generated
  in-house via a local LCS, no extra `diff` dep). The
  "maintenance-inactive" concern (scope-doc mandate 5) carries little
  risk on a single-user localhost MVP and no concrete integration
  blocker was hit. v-code-diff remains the documented future
  alternative if the diff surface grows.
- **Routed views are keyed by `route.fullPath`** (`<RouterView
  v-slot>` + `:key`). Vue Router reuses a component instance on a
  param-only navigation (`/runs/run-1` → `/runs/run-2`); the dashboard
  carries per-run module-scope state (the run-detail "opened" guard, an
  SSE `EventStream`, per-source UI stores resolved in setup). Remounting
  on id change is the standard, lowest-risk fix and also closes an
  independent-review finding that a pause→resume re-`open()` on the same
  run could orphan an `EventSource` (the events store now also closes
  any existing stream before re-opening — defense in depth, ADR-23's
  no-reconnect-storm contract upheld on the client).

**Consequences.**
- Phase 4 stays a frontend-only phase; no backend contract change
  beyond ADR-25 (already accepted). The Worktree pane is degraded to
  read-only `worktree_path`/`branch` per the Phase-4 scoping decision —
  live git status/diff is a named post-MVP gap.
- The project gate is now two-sided: Python (`ruff`/`mypy`/`pytest`)
  **and** the frontend `npm run check` (`eslint --max-warnings 0` +
  `vue-tsc` + `vitest`). CLAUDE.md's Toolchain section and
  `frontend/README.md` carry the operational form; spec §9 gains a
  pointer here.
- These five points are easy to regress (a future `import` of the
  shiki bundle or a static mermaid import would silently blow the
  bundle budget); they live in code comments at each site **and** here.

---

## ADR-27 — Phase-5 MCP toolchain: bundled official SDK, pinned `<2`

**Status:** Accepted (2026-05-19). Records the Phase-5 MCP server
implementation decisions. Resolves the `docs/plan.md` Phase-5 named
risk ("Streamable HTTP transport tooling churn. Mitigation: pin the
`mcp` Python package version").

**Context.** Phase 5 mounts a FastMCP server at `/mcp` so external MCP
clients (Claude Code, Claude Desktop) can drive relay runs. Two
packages could provide FastMCP: (a) the **bundled** `mcp.server.fastmcp`
that ships inside the official `modelcontextprotocol/python-sdk`
(FastMCP 1.0, donated into the SDK in 2024, maintenance-stable), and
(b) the **standalone** `jlowin/fastmcp` 2.x (a separately-versioned,
fast-moving superset). The MCP SDK is fast-moving and the project plan
flags transport-tooling churn as the one Phase-5 risk.

**Decision.**

- **Use the bundled official SDK** — `mcp.server.fastmcp.FastMCP` +
  `mcp.streamable_http_app()`. Rejected: standalone `fastmcp` 2.x.
  Rationale: ADR-07 already specifies the FastAPI-shaped
  `FastMCP.streamable_http_app()` mount, which *is* the bundled SDK's
  API; using the bundled SDK keeps one fast-moving dependency instead
  of two; a smaller surface is itself the churn mitigation the plan
  asks for. The standalone package's richer FastAPI helpers
  (`http_app`, `combine_lifespans`) are not needed for a single-user
  localhost mount.
- **Pin `mcp>=1.27.1,<2`.** The `<2` upper cap is load-bearing: the
  official repo split at v1.25.0 — `v1.x` is maintenance-only, `main`
  is v2 which *rearchitects the transport layer we mount*. Official
  guidance is to pin `mcp>=1.25,<2`; we floor at `1.27.1` (latest
  stable, includes the Pydantic-2.13 output-schema-generation fix;
  note the observable 400→404 unknown-session-id status change landed
  at 1.26.0). `uv.lock` records the exact resolved version (`1.27.1`);
  the `<2` cap in `pyproject.toml` is the durable mitigation.
- **Lifespan session-manager wiring (the #1367 footgun).** A sub-app
  mounted via `app.mount()` does **not** get its lifespan auto-run by
  Starlette/FastAPI; `mcp.streamable_http_app()`'s
  `StreamableHTTPSessionManager` is started in that lifespan. The host
  `relay_v2.app` lifespan therefore wraps its existing body in
  `async with mcp.session_manager.run():`. Omitting this makes every
  `/mcp` request hang — so it gets an explicit integration test (W3).
- **Streaming-tool shape.** `spec.md §8` types `relay__tail_events` as
  `-> AsyncIterator[Event]`, but an MCP tool result is a single value;
  a live async generator cannot be a tool return. The faithful
  tool-shaped equivalent is a **bounded snapshot** of events after
  `since_seq` (a cursor the caller advances), which is exactly the
  data the SSE tail carries, just pull-paged instead of pushed. This
  spec-vs-impl delta is intentional and documented in `docs/mcp.md`
  and the tool docstring rather than left implicit. Live push remains
  available via the existing SSE endpoint (ADR-23); MCP clients poll.

**Consequences.**
- One new runtime dependency (`mcp`), pinned `<2`; `uv.lock` updated.
- Phase 5 stays backend-only and additive: a new `src/relay_v2/mcp/`
  package and one `app.py` lifespan wrap; no REST/SSE/orchestrator
  contract changes. All seven tools are thin `RelayCore` adapters
  reusing the `api/schemas.py` Pydantic models (ADR-07/15) — no new
  `RelayCore` capability.
- `spec.md §8` gains a toolchain note pointing here; the seven-tool
  contract is unchanged in intent (the `tail_events` return shape is
  clarified, not redesigned).
- Upgrading across the v1.x→v2 boundary is a deliberate, ADR-gated
  action — not an incidental `uv lock --upgrade`.

---

## ADR-28 — Phase-6 engineering-team skill port: single-session, repo-root + force-include, manual behavioral verification

**Status:** Accepted (2026-05-19). Implements ADR-14 / spec.md §12.
Records the Phase-6 skill-port decisions and resolves the
`docs/plan.md` Phase-6 named risk ("Skill prose quality … the v2 port
should not regress its quality").

**Context.** v1's `engineering-team` skill (11 files, ~1900 lines:
`SKILL.md`, four `phases/`, six `references/`) is mature and
prose-dense. Phase 6 ports it into relay-v2 and adds `relay
install-skill`. The skill must run under the **pi** harness (not
Claude Code) and inside relay-v2's already-built run model
(orchestrator-provisioned worktree, `.relay/runs/<run_id>/` artifacts,
no subagent dispatch). The risk is regressing the v1 prose by
rewriting for its own sake.

**Decision.**

1. **Port faithfully; adapt only deliberately.** v1 prose and the
   sentinel **grammar** are kept verbatim (spec.md §12 mandate). Six
   adaptations were made on purpose and no others:
   1. **Single-session, no subagent dispatch.** v1's Task-tool
      "Engineer N / Product Owner" subagents become *analysis lenses
      the one session works in sequence*. relay-v2's orchestrator has
      no `subagent_dispatch` signal handler (post-MVP, spec.md §12);
      writing the skill for parallel dispatch today would be fiction.
   2. **`$RELAY_RUN_DIR` = `.relay/runs/<run_id>/`** everywhere — the
      v1 `.engineering-team/runs/<utc>/` path is fully removed
      (spec.md §3.3; sibling of the worktree).
   3. **Worktree is relay-provisioned (ADR-13).** Phase 3 no longer
      runs `git worktree add`; it *verifies* the orchestrator's
      worktree. The v1 `current.txt` mirror is deleted (driver-side
      now, per ADR-13's stated consequence).
   4. **Phase 4 inlines the gate.** No `/done` or `/merge-push` exist
      under pi (Claude Code slash-skills). Phase 4 performs their
      steps inline — sanity → CI-config → docs → full tests →
      security review → lint+types → journal → FF-merge → ask before
      push — preserving v1's intent (lint/types/security are *not*
      run by the unit loop) without the missing tooling.
   5. Sentinel source-doc pointers repoint to `docs/spec.md`.
   6. Worked-example commands use relay-v2's `uv run` gate.
   These are locked by `tests/skills/test_skill_structure.py` so a
   later careless edit fails the gate instead of silently regressing.

2. **Repo-root canonical source + wheel force-include.** The skill
   lives at `skills/engineering-team/` (spec.md §12), outside the
   `src/relay_v2` wheel package. `[tool.hatch.build.targets.wheel.
   force-include]` maps it into built wheels as `relay_v2/skills/`.
   `install_skill.skill_source_dir()` prefers the packaged location
   and falls back to the repo-root tree, so both the editable/source
   install (the only mode used today) and a future wheel resolve a
   bundled copy. Rejected: vendoring the skill inside
   `src/relay_v2/` (contradicts spec.md §12's stated path);
   `importlib.resources`-only (breaks the editable layout where
   `relay_v2` resolves to `src/relay_v2`, which has no sibling
   `skills/`).

3. **Behavioral verification is a documented manual step, not an
   automated test.** plan.md Phase-6 verification ("run relay-v2
   against the v1 demo fixture; confirm evaluate→plan→develop fixes
   the bug across iters; dashboard renders cleanly") spawns real pi
   (Max subscription, network, multi-minute, non-deterministic) and
   its acceptance criteria are qualitative. That is exactly the
   profile of the three existing `PI_INTEGRATION=1`-gated e2e tests.
   Making it a CI unit test would be flaky and slow and could not
   assert "renders cleanly". Decision: keep the deterministic
   automated coverage (the `install_skill` CLI suite and the
   structural-invariant suite) and document the full pi run as a
   manual procedure in `docs/skills.md`, gated like the other pi
   e2e checks. Rejected: a `PI_INTEGRATION=1` automated test —
   non-deterministic LLM output makes the assertions either vacuous
   or flaky; the manual step with the v1 `inspect-eng-team-demo.sh`
   probe is the honest verification.

**Consequences.**
- New: `skills/engineering-team/` (11 files), `src/relay_v2/cli/`
  (`__init__.py`, `install_skill.py`), `relay install-skill`
  subcommand wired in `__main__.py`, hatch `force-include`,
  `tests/cli/` + `tests/skills/` (+25 tests; suite 158→183 passed,
  3 pi-e2e still gated; ruff/mypy clean, 35 source files, coverage
  91%). `docs/skills.md` is the new operational ref; `spec.md §12`
  gains a Phase-6 implementation note.
- The skill is single-session by design until relay grows
  `subagent_dispatch`; at that point the lenses become parallel
  dispatches again with **no change to phase structure or
  sentinels** — the port preserved that seam intentionally.
- `relay install-skill` is additive and offline; no orchestrator,
  REST, SSE, or MCP contract changed. Phase 6 is skill + CLI only.
- Behavioral acceptance is attested in the journal + `docs/skills.md`
  procedure, not by CI — consistent with how pi e2e is treated
  project-wide (ADR-24, CLAUDE.md).

---

## ADR-29 — Phase-7 OTel mirror: self-owned TracerProvider, deferred no-op, pinned `<2`, manual Langfuse acceptance

**Status:** Accepted (2026-05-19). Implements ADR-10's "mirror to OTel
as a bolt-on" consequence and `spec.md` §10. Resolves the `docs/plan.md`
Phase-7 named risk ("Pi may not surface token counts … record
`gen_ai.usage.*` only when present; gracefully omit") and the
no-overhead requirement.

**Context.** ADR-10 makes the SQLite `events` table the single source of
truth and calls for an opt-in OTel mirror exported to self-hosted
Langfuse (`RELAY_OTEL_EXPORT=langfuse|none`). Phase 7 wires that mirror
into the orchestrator: a `relay.run` → `relay.iter` → `relay.tool_call`
span tree, GenAI semantic-convention attributes where pi surfaces them.
The instrumentation must be **additive** — no change to the event-store
contract, the REST/SSE/MCP surface, or the loop's control flow — and
when export is off it must impose **provably zero** overhead and make
zero network calls (the plan's named risk surface).

**Alternatives considered.**

1. *No-op path = real OTel SDK `TracerProvider` with no span processor.*
   Rejected: still constructs a provider, still pays proxy/sampler
   machinery per span, and the natural way to wire it
   (`trace.set_tracer_provider`) mutates **process-global** OTel state —
   set-once, warns on override, and leaks across the test suite (every
   test would share one provider). Hard to assert "no exporter
   constructed".
2. *No-op path = OTel API default (`ProxyTracerProvider`).* Lighter than
   (1) but still routes every `start_span` through proxy objects and
   still depends on global state; the "zero overhead / no exporter"
   property becomes an assertion about OTel internals rather than about
   our own code.
3. *A thin in-project `Instrumentation` Protocol with two
   implementations* — `_NoopInstrumentation` (every method a literal
   no-op; constructs no provider, no exporter, touches no global state,
   makes no network call) and `_OtelInstrumentation` (owns a **local**
   `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter)`; tracers
   come from that instance via `provider.get_tracer()`, never from the
   global). `build_instrumentation(settings)` returns one or the other
   from `settings.otel_export`.

**Decision.** Option 3.

- **Self-owned, non-global `TracerProvider`.** The Langfuse path builds
  its own provider and reads tracers off it directly. Rationale: the
  process-global provider is set-once and would make the suite
  un-isolatable (each `test_otel_export` case needs its own
  `InMemorySpanExporter`); a self-owned provider is also the honest
  shape for a library that must not commandeer global OTel state from
  whatever embeds it.
- **No-op is a deferred literal no-op, not an SDK object.** With
  `RELAY_OTEL_EXPORT=none`, `build_instrumentation` returns
  `_NoopInstrumentation` and the OTLP exporter / SDK provider are
  **never constructed** — the OTel SDK import is paid only on the
  langfuse path. The span helpers are `@contextmanager`s yielding a
  shared sentinel; the hot path is an attribute lookup and a generator
  step. This is asserted directly (W-test monkeypatches `OTLPSpan
  Exporter` to raise if constructed; the no-op test trips nothing).
- **Pin `opentelemetry-{api,sdk,exporter-otlp-proto-http}>=1.27,<2`.**
  Unlike ADR-27's `mcp<2` (where v2 *exists* and rearchitects the
  mounted transport), OTel 2.0 does **not** exist yet — so this `<2`
  cap is *precautionary*, not load-bearing, and is recorded as such
  honestly rather than overclaimed. The floor `1.27` is a
  recent-stable baseline; `uv.lock` records the exact resolved
  versions. We deliberately do **not** depend on
  `opentelemetry-semantic-conventions` (its GenAI module is explicitly
  unstable/incubating); the four attribute keys we emit (`gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`) are written as stable string literals —
  these are exactly the keys Langfuse maps and they have been stable
  across OTel GenAI drafts. Cache/cost have no stable GenAI key, so
  they go under a `relay.usage.*` namespace.
- **Langfuse OTLP contract (researched, not guessed).** Endpoint is
  `{RELAY_LANGFUSE_HOST}/api/public/otel/v1/traces` (the traces-signal
  path; `OTLPSpanExporter(endpoint=…)` takes the full URL, no auto
  `/v1/traces` append). Auth is HTTP Basic:
  `Authorization: Basic base64("{public_key}:{secret_key}")`. Sourced
  from the current Langfuse self-hosted OpenTelemetry docs
  (langfuse.com/integrations/native/opentelemetry), not memory.
- **Span-tree placement (resolves the open questions).**
  - `relay.run` opens/closes inside `RelayCore._run(ctx)`'s
    `try/…/finally`, **not** `start_run` (which only enqueues). A
    crashed or supervisor-cancelled run therefore still closes its
    span (status `ERROR`), because `_run`'s existing finally always
    runs.
  - `relay.iter` is one span per `run_loop` while-iteration, child of
    the run span, attribute `relay.iter_seq = seq`. `seq` is the same
    integer written to the `iters` table, so a Langfuse trace lines up
    one-to-one with the dashboard timeline (spec.md §9).
  - `relay.tool_call` is created in `_drive_iter` on `ToolUseEnd`,
    child of the iter span. `ToolUseStart` (carries `name`, `args`)
    is buffered by `tool_id`; the span uses the start/end event `ts`
    so Langfuse shows accurate tool durations.
  - GenAI/usage attributes are set on the **iter** span from
    `SessionEnded.messages[].usage` (the only token/cost source —
    ADR-18), aggregated across assistant messages; each attribute is
    set **only when present** in the payload — absent fields are
    omitted, never zero-filled (the plan's named risk mitigation).
- **Option D — the harness gates the final `AssistantText` on
  `agent_end` (resolves a constraint the plan's premise missed).**
  Implementation review found that the plan/ADR-18 assumption
  "`SessionEnded.messages` is available at the integration point" is
  **false on the normal close path**: pi emits `…turn_end, agent_end`;
  the sentinel-bearing text is flushed at `turn_end`, the orchestrator
  detects the terminal sentinel there and `break`s `_drive_iter`, so
  `agent_end` (the *only* carrier of `messages[].usage`) is never
  consumed and `wait()` synthesises an empty `SessionEnded`. Without a
  fix, per-iter token/cost would be absent on every `done`/`handoff`/
  `pause` iter — i.e. the common case. Alternatives weighed:
  *(A)* ship best-effort, document the gap — usage almost always
  absent; *(B)* post-hoc drain of the stdout pipe in `wait()` after
  the break — **racy** against pi's process exit / our `terminate()`,
  CI-untestable; *(C)* drain-in-loop, storing `agent_end` — changes
  loop control flow **and** event-store contents (explicitly fenced
  off by the Phase-7 scope; bigger SSE/dashboard/MCP regression
  surface). **Chosen: D** — a one-event `AssistantText` lookahead in
  `PiSession.events()` (harness-package only, ADR-04 isolation): the
  most recent `AssistantText` is held and delivered immediately before
  the *next* mapper output, so when that next raw line is `agent_end`
  the harness consumes it and sets `_final` (real `messages`) **before**
  the held sentinel text reaches the orchestrator. The orchestrator's
  post-`break` `wait()` therefore returns pi's verbatim usage.
  Properties: **deterministic** (`agent_end` consumed in-stream, not
  raced — unlike B); external event order **unchanged** (held text is
  flushed before any other event; thinking→text intra-turn order
  preserved); the orchestrator still breaks before `SessionEnded`, so
  **no `agent_end` row is added to the event store** — the ADR-10
  contract and loop control flow are byte-for-byte intact (unlike C);
  crash/timeout (no `agent_end`) still flushes the buffered text at
  end-of-stream and `wait()` synthesises exactly as before. D dominates
  B (same blast radius, but deterministic and offline-testable) and is
  far cheaper than C. **Known separate follow-up (not in Phase 7
  scope):** `agent_end`/`SessionEnded` is still never persisted as an
  `events` row on the sentinel-close path — a pre-existing latent
  ADR-10 completeness gap that D neither widens nor closes; closing it
  is C's territory and deserves its own ADR + `spec.md §6` change.
- **Threading is by parameter, not global.** `run_loop` and
  `_drive_iter` gain an `otel: Instrumentation` keyword that **defaults
  to a module-level no-op singleton**, so every existing call site and
  test is unchanged and the addition is provably additive. The span
  context managers record the exception and set span status on error
  but **re-raise** — they never swallow, so loop control flow is
  byte-for-byte unchanged whether OTel is on or off.

**Verification — automated vs. manual (mirrors ADR-28 §3).**
plan.md Phase-7 verification has two halves:

1. *"verify span structure (no real network)"* — **automated**.
   `tests/observability/test_otel_export.py` drives the real
   `RelayCore` + `run_loop` against an extended `ScriptedHarness`
   (`EventScript`: arbitrary `ToolUseStart/End` + a `SessionEnded`
   with a real pi-shaped `messages` usage payload) with an
   `_OtelInstrumentation` whose exporter is an in-memory
   `InMemorySpanExporter`. It asserts the run→iter→tool_call parent
   chain, `relay.iter_seq` correlation, the GenAI/usage attributes
   (including the "absent → omitted, not zeroed" case), and the
   no-op guarantees (no provider, no `OTLPSpanExporter` construction,
   no network). Option D's harness guarantee is proved separately and
   offline by `tests/harness/test_pi_session_lookahead.py`: a fake
   subprocess replays a fixture-shaped `…turn_end, agent_end` stream;
   the test breaks on the sentinel `AssistantText` exactly as
   `_drive_iter` does and asserts `wait()` still returns the verbatim
   usage `messages`, that full consumption preserves external order,
   and that a no-`agent_end` (crash) stream still synthesises.
   Deterministic, offline, in CI.
2. *"with Langfuse running locally, a real run produces a trace tree
   in the Langfuse UI"* — **manual, journal-attested**. It needs real
   pi (Max subscription, network, multi-minute, non-deterministic) and
   a live Langfuse, and its acceptance ("the tree nests correctly in
   the UI") is qualitative — exactly the profile of the existing
   `PI_INTEGRATION=1` e2e checks and the ADR-28 §3 pi acceptance.
   Documented as a step-by-step procedure in `docs/observability.md`
   and attested in the journal, not run by CI. Rejected: a
   `PI_INTEGRATION=1` automated test — it could not assert "nests
   correctly in the UI" without reimplementing Langfuse's ingestion.

**Consequences.**
- New: `src/relay_v2/observability/` (`__init__.py`, `otel.py`),
  `tests/observability/test_otel_export.py`,
  `tests/harness/test_pi_session_lookahead.py`, `docs/observability.md`
  (operational ref, peer of `docs/mcp.md`/`docs/skills.md`),
  `docs/langfuse-compose.example.yml` (self-host snippet referenced by
  spec.md §10). Changed: `harness/pi.py` `PiSession.events()` gains the
  Option-D one-event lookahead (harness-only); `core.py` opens the run
  span in `_run`; `loop.py` wires iter/tool spans by defaulted
  parameter. `spec.md` §10 gains a Phase-7 implementation note
  pointing here.
- Three new runtime deps (`opentelemetry-api`, `-sdk`,
  `-exporter-otlp-proto-http`), pinned `>=1.27,<2`; `uv.lock` updated.
  ~35→37 source files; `mypy --strict` stays clean (OTel SDK typing is
  loose — precise local annotations + one narrowly-scoped, commented
  `type: ignore` for the exporter kwargs, never a config loosening).
- Additive only: the event store remains the single source of truth
  (ADR-10) — OTel mirrors it and never writes to it; no REST/SSE/MCP
  contract changed; `run_loop`/`_drive_iter` signatures gained one
  defaulted keyword and the loop's observable behavior with
  `RELAY_OTEL_EXPORT=none` is identical to pre-Phase-7. Option D is
  harness-internal (ADR-04) and order-preserving, so the event store /
  REST / SSE / MCP contracts are unchanged. Suite 183→192 passed, 3
  pi-e2e still gated; `mypy --strict` clean across 37 source files;
  backend coverage 93%.
- Langfuse remains strictly opt-in and non-load-bearing (ADR-10): an
  unreachable/misconfigured Langfuse degrades to dropped spans
  (BatchSpanProcessor swallows export errors), never a failed run.

---

## ADR-30 — Phase-8 verification: automated CI vs. journal-attested manual; additive prod static-serving

**Status:** Accepted (2026-05-19). Implements `docs/plan.md` Phase 8
and `spec.md` §11.2. Mirrors the established
automated-vs-manual split of ADR-24, ADR-28 §3, and ADR-29.

**Context.** Phase 8 is verification & polish: README rewrite,
`Dockerfile` + compose, the mandatory GHCR CI workflow, a docs
accuracy pass, and the one functional gap that blocked the packaging
story — spec §11.2 mandates the built Vue SPA be served by FastAPI in
production, but no `StaticFiles` mount existed. plan.md's Phase-8
verification has the same two-natured shape ADR-28 §3 / ADR-29 already
resolved: a deterministic, offline half (fresh `uv sync`; ruff + mypy
+ pytest; the frontend `npm run check`; the Docker image builds) and a
qualitative half needing real pi (Max subscription, network,
multi-minute, non-deterministic) or live external systems (the
end-to-end `relay start eng-team-demo.md` demo, "Docker image pulls
and runs" against a published image, "MCP callable from Claude Code",
the live-Langfuse trace tree carried from Phase 7). The constraint:
Phase 8 must be **additive/polish only** — no orchestrator / REST /
SSE / MCP / observability contract change.

**Alternatives considered.**

1. *Drive the e2e demo + live-Langfuse acceptance in CI behind
   `PI_INTEGRATION=1`.* Rejected for the same reason ADR-28 §3 and
   ADR-29 rejected it: non-deterministic LLM output makes assertions
   vacuous or flaky, CI cannot assert "dashboard renders cleanly" or
   "the tree nests correctly in the Langfuse UI", and it would couple
   CI to a Max subscription + a live Langfuse. Inconsistent with how
   pi-e2e is treated project-wide.
2. *Implement prod static-serving as an always-on mount / a hard
   dependency on a build step.* Rejected: the entire test tree and any
   un-built source checkout have no `frontend/dist/`; an
   unconditional mount would change the app surface in dev/test
   (a `/` route, altered 404s) and risk shadowing API paths — i.e. a
   contract change, violating the additive-only constraint.
3. *A conditional, last-mounted SPA catch-all + the
   automated/manual split applied verbatim from ADR-29.* The mount is
   a no-op (returns `False`, mounts nothing) when `frontend/dist/` is
   absent; when present it is appended **after** `/health`, the REST
   routers and `/mcp` so Starlette (which matches in registration
   order) never lets it shadow an API path; SPA history-mode fallback
   serves `index.html` only for extension-less misses so a broken
   asset reference still 404s.

**Decision.** Option 3.

- **Verification — automated (CI, `.github/workflows/ci.yml`).** The
  `gate` job runs `uv sync --frozen` then `uv run ruff check .`,
  `uv run mypy`, `uv run pytest -q` (the 3 pi-e2e tests stay gated —
  `PI_INTEGRATION` is **not** set), then the frontend gate (`npm ci`
  + `npm run check` in `frontend/`). The full gate is Python **and**
  frontend, by policy. The `docker` job (push to `main` only, `needs:
  gate`) logs in to GHCR with `${{ github.token }}` and pushes
  `ghcr.io/johnmathews/relay-v2:latest` + `:${{ github.sha }}`
  (`permissions: packages: write`); `workflow_dispatch` is present
  per policy. Only trusted contexts are interpolated — no untrusted
  event input reaches a `run:` step. The Docker image **building** is
  deterministic and is therefore a CI gate (it also exercises the
  fresh `uv sync` and the in-image frontend build).
- **Verification — manual, journal-attested (gated like
  `PI_INTEGRATION=1`).** The end-to-end `relay start eng-team-demo.md`
  demo (real pi, dashboard renders the full run, MCP callable from
  Claude Code) and the live-Langfuse-UI trace-tree acceptance carried
  from ADR-29 §verification-2. These are documented step-by-step
  procedures (`docs/skills.md` for the eng-team demo;
  `docs/observability.md` for the Langfuse tree) and attested in the
  journal, not run by CI — exactly the ADR-28 §3 / ADR-29 profile.
  The Phase-8 journal entry attests the deterministic half (suite,
  ruff/mypy, the local `docker build` + container boot smoke) and
  records the manual half as the outstanding owner-run procedures.
- **Prod static-serving is additive.** New
  `src/relay_v2/api/static.py` (`frontend_dist_dir()` resolver +
  `_SpaStaticFiles` 404→`index.html` fallback + `mount_frontend()`);
  `app.py` calls `mount_frontend(app)` in the lifespan immediately
  after the `/mcp` mount. With no build present the function mounts
  nothing and returns `False`, so the suite's 192 pre-Phase-8 tests
  are byte-for-byte unaffected; two new tests
  (`tests/api/test_static_frontend.py`) prove both the no-op and the
  built-frontend behaviours, and the real Docker image was booted
  locally to confirm `/`, a deep SPA route, `/health` and
  `/openapi.json` all behave correctly together. No event-store /
  REST / SSE / MCP / observability contract changed.

**Consequences.**
- New: `src/relay_v2/api/static.py`,
  `tests/api/test_static_frontend.py`, `Dockerfile`, `.dockerignore`,
  `docker-compose.example.yml`, `.github/workflows/ci.yml`. Changed:
  `README.md` (rewritten — Phases 0–8, install/run/dashboard/MCP/
  observability/Docker), `app.py` (one additive lifespan call + a
  docstring note), `CLAUDE.md` "Current state" + toolchain,
  `spec.md` §11.2 (Phase-8 implementation note), `docs/plan.md`
  Phase 8 (delivered). Suite 192→**194 passed**, 3 pi-e2e still
  gated; ruff + `mypy --strict` clean across **38** source files;
  backend coverage 93%.
- Two Phase-7 follow-ups remain open and are recorded, **not** closed
  by Phase 8 (closing either would be a contract change): (a) the
  live-Langfuse-UI acceptance has still not been run (manual,
  journal-attested when performed — ADR-29 §verification-2); (b) the
  latent ADR-10 gap that `agent_end`/`SessionEnded` is never persisted
  as an `events` row on the sentinel-close path — ADR-29 explicitly
  fences this as "C's territory, its own ADR + `spec.md` §6 change".
  Phase 8 neither widens nor closes them.
- The MVP is complete: every `docs/plan.md` "what done with MVP looks
  like" bullet is satisfied except the two owner-run manual
  acceptances above, which are documented procedures, not code work.

---

## ADR-31 — Internal-error finalisation: a failing run never stays `running`

**Status:** Accepted (2026-05-20). Post-MVP bugfix surfaced in the
field; tightens the run lifecycle contract established in ADR-19 / spec.md
§6 without changing the success path.

**Context.** A user registered a project with a leading `~/...` path
through the dashboard. `register_project` called
`Path(root_path).resolve()` — which makes a relative path absolute but
does NOT expand `~` — so the row stored `<cwd>/~/projects/...`. The
directory does not exist. When a run was started against that project
the orchestrator opened an iter, persisted `iter_started`, and called
`PiHarness.spawn(..., cwd=<bogus path>)`. The subprocess spawn raised
`FileNotFoundError` because the cwd does not exist.

That exception unwound through `run_loop` into `RelayCore._run`. The
inner `try/except` only caught `asyncio.CancelledError`; any other
exception propagated past the `finally` (which set `state.settled` but
made no DB write) and reached the supervisor's
`task.add_done_callback(self._tasks.discard)` — discarded. Result: the
run sat permanently `running` in the DB with no `iter_ended`, no
`run_ended`, no closing status update. The dashboard SSE-tailed an
event stream that would never produce another event, polling and
issuing cancels that finalised nothing.

Two separable bugs fall out: a user-facing one in `register_project`
(no `expanduser` / no existence check) and a latent lifecycle one in
`_run` (silent loss of any non-Cancelled exception). The latter is
this ADR; the former is a straightforward edit and is recorded here
only as the trigger.

**Alternatives considered.**

1. *Let the exception keep propagating (status quo) and document
   "operator must clean up stuck rows".* Rejected: the run loop is
   the single owner of run-level status transitions per spec.md §6 /
   ADR-19, and "operator cleanup" is exactly the v1 anti-pattern v2
   exists to replace. A run that can never observably terminate is a
   contract violation, not a known limitation.
2. *Re-raise the exception after recording, so the supervisor task
   still surfaces it.* Rejected: the supervisor already discards the
   task handle (ADR-19's tracked-task set is for cancellation, not
   error propagation), so re-raising only produces a
   "Task exception was never retrieved" line on GC — strictly noisier
   with no behavioural win. The recorded `run_ended` and the
   `logger.exception` log line are the actionable signals.
3. *Add a new `internal_error` run status separate from `failed`.*
   Rejected: the existing four statuses (`running`/`paused`/`done`/
   `failed`/`cancelled`) cover the surface (spec.md §3.2). The
   distinction the operator needs ("crash in our code vs. agent
   couldn't produce a signal") lives in the run-ended `summary`
   payload (`internal_error: <exc>`) and the matching `LoopResult.reason
   == "internal_error"`, mirroring how `max_iters` / `timeout` /
   `agent_end_no_signal` are surfaced today.

**Decision.** Option as written. In `RelayCore._run`, add an
`except Exception` peer to the existing `except asyncio.CancelledError`
that wraps both `run_loop` and the `_apply_result` call. On entry it:

- logs `logger.exception("run %s failed with internal error", ctx.run_id)`
  via the new module logger so the operator sees a stack trace in the
  uvicorn log instead of a silent GC notice;
- sets `state.result = LoopResult("failed", reason="internal_error",
  summary=str(exc))` so awaiters of `wait_for_run` unblock with a
  terminal verdict;
- best-effort writes `set_run_status('failed', ended=True)` and
  appends `run_ended` with `{"status": "failed", "summary":
  f"internal_error: {exc!s}"}` — wrapped in
  `contextlib.suppress(Exception)` mirroring the cancellation branch,
  because the engine may be mid-dispose during `aclose()`;
- does not re-raise.

The user-facing trigger is fixed in the same change by `register_project`:
`Path(root_path).expanduser().resolve()` plus an `is_dir()` precondition
that raises `ValueError`; the `POST /api/projects` route maps that to
400 via `http_error(exc, default_status=400)`.

**Consequences.**
- Changed: `src/relay_v2/core.py` (`_run` peer except; module logger;
  `import logging`), `src/relay_v2/orchestrator/lifecycle.py`
  (`register_project` expanduser + existence check), `src/relay_v2/api/
  projects.py` (catch `ValueError` → 400 via `http_error`),
  `docs/decisions.md` (this ADR).
- New tests: `tests/orchestrator/test_loop.py::
  test_internal_error_finalises_run_as_failed` (uses a `RaisingHarness`
  double whose `spawn` raises `FileNotFoundError`; asserts the run
  ends `failed`, the closing event is `run_ended` with
  `internal_error:` summary, and `wait_for_run` returns within 5 s);
  `tests/api/test_w2_routes.py::test_project_register_expands_tilde`
  and `::test_project_register_rejects_missing_path` cover the trigger
  fix. Suite **194 → 196 passed**; 3 pi-e2e tests remain gated behind
  `PI_INTEGRATION=1`. `ruff` / `mypy --strict` clean.
- The event-store / SSE / MCP / OTel-mirror contracts are unchanged.
  The run lifecycle gains exactly one new terminal-event payload shape
  (`run_ended` with `summary="internal_error: …"`), which existing
  consumers tolerate (the dashboard renders the summary verbatim).
- Distinct from the latent gap ADR-29 / ADR-30 fence off (no
  `agent_end`/`SessionEnded` row on the sentinel-close path): that one
  is about the *successful* close path; this ADR is about the
  *exception* close path. Closing the ADR-29 gap is still owner work
  with its own ADR.

---

## ADR-32 — Orphan recovery on `RelayCore.start()` + `cancel_run` safety net

**Status:** Accepted (2026-05-21). Extends ADR-31's "a failing run never
stays `running`" guarantee to a related failure mode: process death.

**Context.** ADR-31 closed the in-loop-exception path. A separate
scenario surfaced in the same field session: a run that became stuck
*before* ADR-31 shipped sat in `running` status; the user restarted the
server; clicking Cancel did nothing. The orchestrator's `cancel_run`
flips `state.cancel_event` and `await session.cancel()` on the
in-memory `_RunState`, but after a process restart `self._runs[run_id]`
is empty (it's a process-local dict). With `state is None` the function
returned silently — 200 OK, no DB write, the run stayed `running`
forever and the dashboard polling/cancel cycle continued.

Same root invariant as ADR-31: a run that the orchestrator can no
longer drive must reach a terminal status. Two distinct entry points:
the startup boundary (orphans from a prior process) and the cancel
boundary (defence in depth — the sweep races a Cancel click, or a
race we haven't thought of). Single-user, single-process MVP
(ADR-12) makes the orphan-detection rule trivial: at startup, every
`running` row is by definition owned by a process that is now gone.

**Alternatives considered.**

1. *Document "operator restarts the server; manually clean DB with SQL".*
   Rejected for the same reason ADR-31's status-quo branch was
   rejected — manual cleanup is the v1 anti-pattern v2 exists to
   replace. A button that returns 200 OK but does nothing is a worse
   UX than no button at all.
2. *Multi-process / multi-worker awareness (require a heartbeat /
   lease before sweeping).* Rejected as out of scope — single-user
   MVP (ADR-12), one `relay serve` per user, no horizontal
   replication. A heartbeat layer adds load-bearing complexity for a
   problem we don't have. Revisit if multi-process becomes a
   requirement.
3. *Sweep `paused` too.* Rejected: `paused` is a *recoverable*
   non-terminal status — `resume_run` rebuilds the loop from
   persisted `signal_args` and re-enqueues, no in-memory state needed.
   Sweeping paused would silently destroy a user's pending decision.
   Only `running` is process-owned.

**Decision.** Two surgical edits to `RelayCore`:

- **Startup sweep** (`_recover_orphans`, called from `start()` before
  the supervisor is spawned): `SELECT * FROM runs WHERE status =
  'running'`, then for each row `set_run_status('cancelled',
  ended=True)` + append `run_ended {"status": "cancelled", "summary":
  "orphaned: server restart"}`. The summary string is the load-bearing
  diagnostic — without it the operator can't tell a "real cancel" from
  a "we lost the process owning this run" entry in the timeline.
- **Cancel safety net** (`cancel_run`): when `state is None` AND the
  DB row exists AND is in a non-terminal status, perform the same
  finalisation with summary `"orphaned: process state lost"`. The
  distinct summary lets the operator tell the boundary apart from the
  startup-sweep case. The route still returns 200 — it always did —
  but now the 200 is accompanied by a real DB transition.

The orphan-sweep order is deliberate: schema bootstrap → `_recover_orphans`
→ supervisor `create_task`. Sweeping before the supervisor exists
guarantees no race with a fresh run that arrives between sweep and
supervisor start (no `start_run` can be served until the FastAPI
lifespan's `core.start()` returns).

**Consequences.**
- Changed: `src/relay_v2/core.py` (`start()` gains `_recover_orphans`
  call; new private method; `cancel_run` gains the orphan branch),
  `CLAUDE.md` "Current state" (ADR-32 note appended to the ADR-31
  paragraph), `docs/decisions.md` (this ADR).
- New tests: `tests/orchestrator/test_loop.py::
  test_start_finalises_orphaned_running_rows` (two-"process" pattern:
  insert a `running` row via direct SQL, `aclose` the first core,
  open a fresh core, verify the sweep ran) and
  `::test_cancel_orphaned_run_finalises_db` (bypass the sweep by
  popping `_runs` after registration; verify `cancel_run` still
  finalises). Suite **197 → 199 passed**; 3 pi-e2e remain gated.
- The dashboard sees no new event kinds — same `run_ended` shape as
  cancel/done/internal-error; the `summary` field's vocabulary grows
  by two strings. The frontend's `failureInfo` banner (ADR-31
  follow-up) renders the cancelled state with its existing copy
  (`"orphaned: …"` is shown verbatim under "Run cancelled —
  cancelled" — readable enough for the MVP; a dedicated `orphaned`
  case is a refinement, not a contract change).
- 'paused' rows are intentionally NOT swept. `resume_run` continues
  to work across restarts.
- Multi-process / multi-worker is out of scope (ADR-12) and remains
  so: this sweep assumes a single owner per database. If that
  assumption ever changes, this ADR must be revisited.

---

## ADR-33 — Bundled skill variants live under per-harness subdirectories

**Status:** Accepted (2026-05-21). Makes structurally explicit a
property that was previously implicit: the bundled
`engineering-team` skill is the *relay + pi* variant of a workflow
that could in principle be ported to other harnesses.

**Context.** The skill was ported in Phase 6 (ADR-28) with six
deliberate adaptations to fit the relay + pi shape: single-session
execution (no Task-tool subagent dispatch), role names as in-session
analysis lenses, `.relay/runs/<run_id>/` paths, relay-provisioned
worktree (the skill verifies, never creates), inlined wrap-up gate
(no `/done` / `/merge-push` slash commands), `uv run` examples.
Adaptations 1, 4, and 6 are not presentation tweaks — they change
what the skill tells the agent to do. But the bundle layout
(`skills/engineering-team/{SKILL.md, phases/, references/}`) made
none of this visible. A reader (or a future second harness port)
would have no structural cue that today's contents are pi-shaped.

Neither pressure forced a change today (one harness in production,
one bundled skill), but the rename is small and self-contained;
doing it now beats doing it later when a new variant would
otherwise mix the structural change with new content in one PR.

**Decision.** Bundled skills live at `skills/<name>/<harness>/`. A
shared `skills/<name>/README.md` describes the variant set for
humans (agents never load it). `relay install-skill --harness
<name>` selects the variant; the default is `pi`. The install
target path is unchanged
(`~/.claude/skills/engineering-team/` — no harness suffix at the
destination), because the agent reads `engineering-team`, not
`engineering-team-pi`; mixing harnesses in one Claude install
would be a configuration error worth a separate flag, not the
default shape.

**Alternatives considered.**

1. *Shared core + harness adapters (templated SKILL.md with
   `{{#if harness=pi}}` blocks).* Rejected: the differences are
   workflow shape, not formatting. Two parallel docs are strictly
   easier to keep correct than one templated doc when the
   adaptation set is small. Reconsider if the catalogue grows to
   ≥3 harnesses **and** ≥5 skills.
2. *Flat `engineering-team-pi/` peers (no nesting).* Rejected:
   loses the "variants of one skill" grouping. The shared README
   has nowhere to live, and `relay install-skill` would have to
   know the suffix convention rather than treating it as normal
   sub-selection. Nesting makes the variant relationship
   structural.
3. *Defer until a second variant actually exists.* Cheaper now
   (zero work), but at second-variant time you do both the rename
   **and** the new variant in one PR, mixing concerns. The
   structural change is small and self-contained; doing it once
   when the answer is obvious beats doing it later when there is
   also new content to review.
4. *Variant selection by env var (`RELAY_HARNESS=pi relay
   install-skill`).* Rejected: install-skill is invoked at project
   setup, not at runtime; there is no ambient relay process whose
   harness selection should propagate. Making it an explicit flag
   keeps the choice visible in the journal/history.
5. *Auto-detect (the running relay-v2 supports pi, install pi).*
   Rejected: couples install-skill to the orchestrator's harness
   selection — a non-goal until there is a real choice to
   disambiguate.

**Consequences.**

- Source-tree move: `skills/engineering-team/{SKILL.md, phases,
  references}` → `skills/engineering-team/pi/{SKILL.md, phases,
  references}` (history-preserving `git mv`). The single
  source-tree self-reference in `pi/references/sentinels.md`
  ("`See: skills/engineering-team/references/sentinels.md`") is
  updated to the new path. Every other internal cross-reference
  in the skill is relative and survives unchanged. Runtime error
  messages in `harness/signaling/sentinels.py` reference the
  *install-target* path (unchanged), not the source-tree path, so
  they stay correct.
- `skill_source_dir(harness="pi")` resolves
  `<bundle>/<name>/<harness>/`. An unknown harness raises
  `FileNotFoundError` listing the available variants.
- `install_skill` additionally copies the variant-selector
  `README.md` (one level above the variant directory) into the
  install target. Agents never read it; humans inspecting the
  install do.
- Install target path is unchanged
  (`~/.claude/skills/engineering-team/`). Bytes copied are
  byte-for-byte identical to before the rename. No agent
  behaviour change.
- Wheel packaging: the existing `force-include` maps the whole
  `skills/` tree, so new variant subdirectories are automatically
  bundled — no `pyproject.toml` change required.
- Tests: `tests/cli/test_install_skill.py` gains
  default-harness, explicit-`--harness pi`, unknown-harness, and
  parent-README-copy cases.
  `tests/skills/test_skill_structure.py` needs no changes —
  `SKILL = skill_source_dir()` now returns the `pi/` directory
  and all path joins inside it are unchanged.

**Related:** ADR-04 (harness isolation — the multi-harness
commitment this ADR extends down into bundled assets), ADR-28
(Phase 6 skill port — the six adaptations this ADR makes
structurally visible).

---

## ADR-34 — `awaiting_children` parents are cancelled on server restart (V1)

**Status:** Accepted (2026-05-21).
**Phase:** 9a (post-MVP fanout-join foundation).

**Context.** The fanout-join feature (proposal:
`docs/proposals/parallel-iters-fanout-join.md`) introduces a new
`awaiting_children` run status: a parent suspended pending completion
of children dispatched via fanout. ADR-31 / ADR-32 established that
orphan-recovery sweeps any `running` row to `cancelled` on startup
(single-process MVP per ADR-12). The new status creates a state-machine
gap: how should the sweep handle `awaiting_children`?

**Decision.** Sweep `awaiting_children` rows the same as `running` —
mark them `cancelled` with a `run_ended` event whose summary is
`"orphaned: server restart"`. Additionally, **cascade-cancel** the
parent's descendants (depth-first, recursively) with summary
`"orphaned: parent interrupted during fanout"`. Recovering an
in-flight fanout across a server restart is a deliberate V1 non-goal.

**Rationale.** Honest about the single-user, single-process MVP
limitation (ADR-12). Symmetric with the existing `running` sweep — no
new "preserve and reconcile" pathway. The cascade helper
(`_cascade_cancel_descendants`) is reused by 9d for runtime
cancellation. A future "preserve and reconcile" model can be added in
a later ADR if real workflows demand restart-survival.

**Alternatives considered.**

1. *Preserve `awaiting_children` and add a startup reconciler.* Check
   "have all children finished while we were down?" at boot and
   either resume the parent (synthesizer iter against now-known
   results) or finalise it. Rejected: strictly more code — new
   background task, child-state validation, partial-completion
   handling — for a benefit (restart survival) that single-user MVP
   users don't pay for. A future ADR can add this if real workflows
   demand it.
2. *Reuse the `paused` status with a discriminator.* Less surface
   area to add but conceptually muddled — `paused` is human-resolved
   (a saved `next_prompt` + `question`), `awaiting_children` is
   machine-resolved (child completion watcher). Rejected: distinct
   semantics deserve distinct status values; the frontend / MCP /
   OTel all want to render them differently.
3. *Sweep only the parent, leave children running.* Children would
   continue with no consumer for their `subagent_return` events;
   they'd produce a worktree diff nobody synthesises. Rejected as
   resource leak (zombie pi processes, orphan worktrees, orphan
   subscriptions). Cascade-cancel is the only consistent rule.

**Consequences.**

- Changed: `src/relay_v2/core.py` (`_recover_orphans` sweeps both
  `running` and `awaiting_children`; new private
  `_cascade_cancel_descendants` helper), `src/relay_v2/api/events.py`
  (`_TERMINAL` comment notes `awaiting_children` is not terminal —
  value unchanged), `frontend/src/stores/events.ts` and
  `frontend/src/views/RunDetailView.vue` (mirror comment update;
  values unchanged), `frontend/src/components/shared/StatusBadge.vue`
  (adds `awaiting_children` to `KNOWN`, dedicated CSS modifier),
  `docs/spec.md` §3.1 / §3.2 / §6, `docs/decisions.md` (this ADR).
- New tests: `tests/orchestrator/test_loop.py` gains
  `test_recover_orphans_sweeps_awaiting_children`,
  `test_recover_orphans_cascades_to_children`,
  `test_recover_orphans_cascades_recursively`,
  `test_cascade_skips_already_terminal_children`,
  `test_cascade_handles_cycle_safely`; `tests/api/test_sse.py` gains
  `test_sse_treats_awaiting_children_as_live`; the frontend
  `StatusBadge.spec.ts` adds the `awaiting_children` case.
- 9a does not parse the fanout sentinel, does not spawn child runs,
  does not extend the preamble. Production code paths cannot create
  an `awaiting_children` row yet — all new tests rely on direct DB
  seeding (the test fixture pattern the orphan-recovery tests
  already use). 9b/9c inherit a fully-functional recovery path.
- Multi-process / multi-worker is out of scope (ADR-12) and remains
  so: the sweep + cascade assume a single owner per database. If
  that assumption ever changes, this ADR must be revisited
  alongside ADR-32.

**Related:** ADR-12 (single-process MVP), ADR-31 (run finalisation
on internal errors), ADR-32 (orphan recovery on startup + cancel
safety net), proposal `docs/proposals/parallel-iters-fanout-join.md`,
`docs/plans/2026-05-21-fanout-join-9a.md`.

---

## ADR-35 — Fanout concurrency cap: `asyncio.Semaphore` in the supervisor (Option A)

**Status:** Accepted (2026-05-21)
**Phase:** 9b (fanout dispatch)

**Context.** The fanout-join feature dispatches N child runs when a parent iter
emits `[[engteam:fanout]]`. The proposal names `max_fanout_concurrent`
(default 4) as an operational guard against too many parallel pi sessions. Two
implementation options were considered:

- **Option A — Semaphore in `RelayCore`:** all N child run rows are created
  immediately as `running` rows; the supervisor acquires the semaphore before
  launching each child task and releases on task completion. Children waiting for
  a slot sit in the supervisor queue as `running` rows not yet started.
- **Option B — Queue-and-block at dispatch:** `RelayCore` creates only the first N
  rows; the rest are held in a pending queue (in-memory or a new DB table) and
  created/enqueued as slots free.

**Decision.** Option A.

**Rationale.** Option B requires a new persistent queue that must survive server
restart — replicating exactly the gap 9a closed for `awaiting_children`. An
in-memory queue is lost on restart; a DB queue requires a new table, a new status
value, and a new startup sweep. Option A avoids all of this: every child row
exists in the DB from the moment of dispatch. On restart, the existing
orphan-recovery sweep (ADR-32 / ADR-34) handles them correctly — children waiting
for a semaphore slot are swept as `running` orphans, giving them the
"parent interrupted during fanout" summary if their parent is
`awaiting_children`. Fairness across multiple parents is natural: one shared
semaphore pools all concurrent child tasks. The semaphore is created in
`RelayCore.start()` after the event loop exists, initialized from
`settings.max_fanout_concurrent`.

**Consequences.**
- Child rows created-but-not-yet-executing sit as `running` rows in the
  supervisor queue. On restart they are swept as any `running` orphan.
- The semaphore count is not persisted across restarts; acceptable for
  single-user MVP (ADR-12).
- `max_fanout_concurrent` is a `Settings` field
  (`RELAY_MAX_FANOUT_CONCURRENT` env var, default 4).
- `max_fanout_depth` is also a `Settings` field
  (`RELAY_MAX_FANOUT_DEPTH`, default 2, hard cap enforced at dispatch time).

**Rejected:** Option B — new persistent intermediate state, new startup sweep,
disproportionate complexity for a single-user MVP guard. An in-memory-only queue
is lost on restart and breaks the restart-recovery guarantee ADR-34 establishes.

**Related:** ADR-12 (single-process MVP), ADR-32 / ADR-34 (orphan recovery),
`docs/proposals/parallel-iters-fanout-join.md`.

## ADR-36 — Fanout-join watcher placement + synthesizer body shape

**Status:** accepted (2026-05-21)
**Phase:** 9c (fanout-join: synthesizer iter + parent resume)

**Context.** Phase 9b lands fanout dispatch — a parent emits
`[[engteam:fanout]]`, children spawn, parent enters `awaiting_children`.
Phase 9c closes the loop: when all children settle, append the
synthesizer iter on the parent's stream. Three design questions
resolved in this ADR:

- **OCQ-3 — where does the child-completion watcher live?**
- **OCQ-5 — what is the shape of the synthesizer iter's prompt body?**
- **OCQ-4 — does `join_prompt` move out of `iters.signal_args` into a
  dedicated column now that 9c has to read it?**

### Decision (OCQ-3: watcher placement)

**In-process direct call from the child's `_run` task, lock-guarded by
the existing `RelayCore._enqueue_lock`.** After `state.settled.set()`
in `_run`'s `finally`, if `ctx.parent_run_id is not None`, call
`core._maybe_resume_parent(ctx.parent_run_id)`. The helper takes
`_enqueue_lock`, re-reads the parent under the lock, returns silently
when the parent is no longer `awaiting_children` (cascade-cancel,
already resumed by a sibling, or never awaiting). When all siblings
are terminal: emit `subagent_return` × N + `child_runs_resolved`,
transition parent → `running`, enqueue synthesizer `RunContext`.

**Rationale.** The child's `_run` task already owns the child's
terminal write — it is the natural notification point with full local
context (`ctx.parent_run_id`) and zero new task plumbing. The existing
`_enqueue_lock` is the right serialiser: it is the same lock
`resume_run` uses for the look-then-decide-then-enqueue race, which is
exactly the race a near-simultaneous "last two children settle"
introduces. Single-user MVP (ADR-12) makes the lock's coarse scope
acceptable.

**Refinement (implementation).** Two structural fixes surfaced when
the watcher landed and were folded into the same patch:
(a) `_run`'s `finally` invokes the watcher *before* `state.settled.set()`,
not after — otherwise a caller awaiting a child's `wait_for_run()`
then immediately the parent's could race the watcher's swap of
`self._runs[parent_id]` for a fresh `_RunState` and observe the stale
`awaiting_children` result instead of the synthesizer's terminal
status. (b) `_dispatch_children` creates *all* child rows + their
`subagent_dispatch` events in one pass, then enqueues them in a
second pass — interleaving create/enqueue let a fast harness
(the scripted-test path) start child A and let it finish before
child B's row existed, at which point the watcher's "are all
children terminal?" check would short-circuit on the partial set
and resume the parent with one `subagent_return` instead of two.
Both fixes are pre-conditions for the watcher behaving correctly
under the asynchrony the design assumes.

**Rejected — `EventStore.append` post-commit hook.** Fires on every
event, requires kind/status filtering, and introduces reentrancy
concerns (the watcher itself appends events through the same store).
Strictly more code and more failure modes for the same observable
behaviour.

**Rejected — background polling task.** Wastes CPU; lags by the poll
interval; conflicts with the "everything routes through `RelayCore`"
invariant (ADR-07).

**Rejected — `Broadcaster` post-publish hook.** The broadcaster is a
read-only/UI-facing observer (ADR-23) — never the right place to land
orchestrator state transitions.

### Decision (OCQ-5: synthesizer body shape)

**`join_prompt` followed by a `---` separator and a YAML-ish
`RELAY_CHILD_RESULTS:` trailer (one `- id: …` entry per child, with
`role` / `status` / `summary` / `branch` / `worktree_path` indented
underneath). Multi-line summaries use YAML literal block
(`summary: |`).** Hand-rendered, no YAML library. Lives in the body,
NOT the preamble.

**Rationale.** Distinct from `compose_resume_prompt`'s text shape —
that helper is one question/one answer; fanout-join is N children,
structured. The skill reads it the same way it reads the `RELAY_*`
preamble lines (line-based `key: value`). Keeping it in the body
preserves ADR-14's invariant that the preamble carries exactly
`RELAY_RUN_DIR` and `RELAY_PHASE` and nothing else — bending that for
a one-iter-per-fanout-event feature would compromise the canonical
contract.

**Rejected — JSON in a fenced code block.** Heavier to read for the
skill (it'd need a JSON parser); the YAML-ish trailer is line-readable
with the same patterns the skill already uses.

**Rejected — extending the preamble with a third reserved field.**
Violates ADR-14. The synthesizer trailer is per-iter content, not
per-run frame.

### Decision (OCQ-4: join_prompt channel)

**Stays in `iters.signal_args["payload"]["join_prompt"]` (9b's
status-quo Option a).** Re-evaluated with the 9c read concrete, and
the implicit channel is no harder to use than a dedicated column —
one `select(Iter)` filtered by `signal_kind='fanout'`, ordered by
`seq desc`, exactly mirroring `latest_paused_iter` for the resume
path.

**Rationale.** A dedicated `iters.fanout_payload JSON` column would
require a schema bump (hand-rolled `create_all`, ADR-17), a model
edit, and a migration story for a single read-write pair both inside
`core.py`. The orchestrator owns both ends; the implicit dependency is
guarded by `test_fanout_loop.py::test_closing_iter_signal_args_contains_payload`
(9b) and the new `test_fanout_join_integration.py` (9c). Promote only
if 9d/9e need to read the payload from a non-orchestrator surface —
and even then, a `RelayCore.get_fanout_payload(run_id)` accessor is
cheaper than a column.

### Decision (OCQ-6: partial-failure)

**Synthesizer always runs once all children settle; the orchestrator
never auto-fails the parent on a child's failure.** Each child's
status appears in the trailer; the agent decides whether the partial
result is workable.

**Rationale.** Codifies the proposal §cancellation-semantics decision
("cancelled child counts as resolved with status=`cancelled`"). The
orchestrator does not have the domain context to decide whether a
single failed explorer makes the join unworkable — the agent that
wrote the `join_prompt` does. Honest separation of concerns.

### Consequences

- A child run task that crashes uncleanly (raises into `_run`'s outer
  except) still calls `_maybe_resume_parent` from the `finally` — the
  watcher's lock + status re-read make this safe (the parent observes
  the crashed child as `failed` via ADR-31's safety-net writes).
- Two children settling near-simultaneously may both invoke the watcher;
  the lock + re-read keeps exactly one of them through the happy-path
  branch (`test_maybe_resume_parent_idempotent_under_double_fire`).
- The synthesizer iter runs on the parent's existing worktree (no new
  worktree provisioned). Child branches survive in the data dir for the
  agent's perusal — the orchestrator never auto-merges (proposal
  §tradeoffs).

**Related:** ADR-07/15 (RelayCore single chokepoint), ADR-14 (preamble
reserved fields), ADR-20 (pause/resume — the mechanism this resume
mirrors), ADR-23 (broadcaster scope: read-only/UI-facing),
ADR-31/32/34 (run finalisation + orphan/cascade — the safety net the
watcher relies on for crashed children), ADR-35 (fanout concurrency
cap — 9b sibling), proposal `docs/proposals/parallel-iters-fanout-join.md`.
