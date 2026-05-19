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
