# relay v2 — implementation plan

> Phased build sequence for the v2 MVP, with concrete deliverables and
> verification criteria per phase. Updated as work progresses.
>
> Companion docs: `motivation.md` (why), `spec.md` (what), `decisions.md`
> (ADR log with rationale).

## Phase ordering and dependencies

```
Phase 0 (scaffold) ──→ Phase 1 (harness) ──→ Phase 2 (orchestrator) ──┐
                                                                      │
                       Phase 3 (REST + persistence) ─────────────────┤
                                                                      │
                       Phase 4 (dashboard MVP) ─── (parallel-able) ──┤
                                                                      ↓
                                          Phase 5 (MCP server) ──→ Phase 6 (skill port)
                                                                      ↓
                                          Phase 7 (OTel + Langfuse) ──┐
                                                                      ↓
                                                              Phase 8 (verification & polish)
                                                                      ↓
                                                                     MVP
```

Phase 4 (dashboard) can be developed in parallel with Phase 3 if
two-person split. Single-developer order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8.

## Pre-phase: prerequisites done

These are completed before phase 0 begins:

- ✅ `motivation.md`, `spec.md`, `decisions.md` drafted
- ✅ Pi de-risking harness run; findings captured in `scratch/pi_derisk_workdir/findings.md`
- ✅ Pi event schema confirmed (`session`, `agent_start`, `turn_start`,
  `message_start/update/end`, `tool_execution_start/update/end`,
  `turn_end`, `agent_end`)
- ✅ No 30-second tool timeout confirmed (70-second Bash completed)
- ✅ Pi `--continue` session resume confirmed
- ✅ `PI_AGENT_SDK=1` auth path confirmed working

Outstanding risks parked (don't block start):
- Pi billing claim (ADR-09 provisional)
- Pi version churn — pin to v0.74.0 initially

---

## Phase 0 — scaffold (2 days)

**Goal.** A pip-installable relay-v2 package with a runnable FastAPI app
serving a placeholder route. SQLite schema created on first run.

**Deliverables:**

```
relay-v2/
├── pyproject.toml          # uv + entry-point: `relay`
├── src/relay_v2/
│   ├── __init__.py
│   ├── __main__.py         # `relay` CLI dispatch
│   ├── app.py              # FastAPI factory
│   ├── config.py           # env-driven config (RELAY_* vars from spec §11)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py       # SQLAlchemy models matching spec §3.1
│   │   └── migrations/     # alembic or hand-rolled
│   └── version.py
├── tests/
│   ├── __init__.py
│   └── test_smoke.py       # app boots, /health returns 200
├── README.md               # one paragraph + dev quickstart
└── .gitignore              # .relay/, __pycache__/, dist/, etc.
```

**CLI shape (subset):** `relay serve`, `relay --version`.

**Verification:**
- `uv sync && uv run pytest` passes
- `uv run relay serve` starts the daemon on `127.0.0.1:7800`
- `curl http://127.0.0.1:7800/health` returns `{"status": "ok"}`
- First serve creates `<cwd>/.relay/relay.db` with schema migrated

**Out of scope this phase:** orchestrator, harness, dashboard, MCP.

**Risks:** none material. Standard scaffolding.

---

## Phase 1 — harness layer (3 days)

**Goal.** A `Harness` protocol with a working `PiHarness` implementation
that can spawn `pi -p` and emit normalized `HarnessEvent` objects.

**Deliverables:**

```
src/relay_v2/harness/
├── __init__.py
├── protocol.py             # Harness, HarnessSession, HarnessEvent dataclasses (spec §4.1)
├── pi.py                   # PiHarness, PiSession (spec §4.2)
├── signaling/
│   ├── __init__.py
│   ├── config.py           # SignalConfig
│   ├── sentinels.py        # text_sentinels strategy
│   └── mcp_tools.py        # stub for now (raises NotImplementedError)
└── tests/
    ├── test_pi_event_mapping.py    # unit tests on JSON → HarnessEvent
    ├── test_signaling_sentinels.py # sentinel parser tests (port v1 fixtures)
    └── test_pi_integration.py      # end-to-end: spawn pi, run a prompt, assert events
                                    # (skipped unless PI_INTEGRATION=1 in env)
```

**Key implementation notes:**

- `PiHarness.spawn()` constructs the pi command line per spec §4.2.
- `PiSession.events()` reads pi stdout line-by-line, parses JSON, maps to
  `HarnessEvent` subclasses. Accumulates `message_update` deltas into
  `AssistantText` events emitted at `message_end` or `turn_end` time.
- `PiSession.cancel()` calls `proc.terminate()` then waits up to 5s,
  then `proc.kill()`.
- `PiSession.wait()` joins the subprocess, returns final `SessionEnded`.

**Verification:**
- Unit tests on the event mapping pass (using fixture JSONL captured
  during de-risking).
- Sentinel parser tests pass — port v1's `tests/test-parsing.sh`
  fixtures to Python `pytest` cases.
- `PI_INTEGRATION=1 uv run pytest` runs a real pi invocation and
  verifies the full event flow (handoff sentinel → `SignalEmitted`).

**Risks:**
- Pi's `assistantMessageEvent` sub-discriminator may have unexpected
  shapes (e.g., `thinking` deltas). Mitigation: inspect
  `test_event_shapes.jsonl`, handle unknown sub-types by passing them
  through as `AssistantText` with a `kind` tag.
- Tool result payloads may be large. Mitigation: truncate at the
  `EventStore` write layer in Phase 2, not in the harness.

---

## Phase 2 — orchestrator (3 days)

**Goal.** Run a real multi-iter pi session end-to-end against a fixture
project. Sentinels signal handoff; relay starts iter N+1 with the
extracted next-prompt; `done`/`pause` terminate cleanly.

**Deliverables:**

```
src/relay_v2/
├── orchestrator/
│   ├── __init__.py
│   ├── loop.py             # run_loop() per spec §6
│   ├── lifecycle.py        # start_run, end_run, pause_run, resume_run
│   ├── preamble.py         # build the RELAY_PHASE/RELAY_RUN_DIR preamble
│   └── tests/
│       └── test_loop.py    # end-to-end against a fixture prompt
├── core.py                 # RelayCore service layer (single shared object)
└── events.py               # EventStore writer (append-only)
```

**Key implementation notes:**

- `RelayCore` is a single object held by the FastAPI app via lifespan.
  Methods: `start_run`, `cancel_run`, `pause_run`, `resume_run`,
  `store_event`, `list_runs`, `get_run`.
- The orchestrator task is created via `asyncio.TaskGroup` in `lifespan`,
  consuming a `asyncio.Queue` of run-start requests from `RelayCore`.
- The chained-iter loop is in `loop.py`; the loop is intentionally
  short and readable per spec §6.
- `last_session_id` is always `None` between iters per ADR's intent
  (fresh context per iter). Pi resume is reserved for crash recovery.

**Verification:**
- Fixture prompt that emits a single `phase-start` + `handoff` → relay
  iterates once and starts iter 2.
- Fixture that emits `done` → relay terminates with status=`done`.
- Fixture that emits `pause` → relay terminates with status=`paused`,
  writes the saved next-prompt; `resume_run(answer)` re-spawns with the
  composed prompt.
- A run with a `[[engteam:handoff]]` sentinel inside a fenced code block
  in the iter's output but no closing sentinel → orchestrator records
  `exit_reason="agent_end_no_signal"` and fails the run cleanly.

**Risks:**
- Signal-detection timing: parsing on accumulated text vs on streaming
  deltas. Spec §6 reads deltas as they arrive; this may cause false
  positives if the agent partially writes a sentinel during streaming.
  Mitigation: only run signal detection on text *at turn boundaries*
  (after `turn_end` in pi's stream).

---

## Phase 3 — REST API + persistence (3 days)

**Goal.** All endpoints from spec §7 implemented and tested, including
the file browser and run-preview surfaces that ADR-15's dashboard
depends on. The dashboard can talk to a real backend.

**Deliverables:**

```
src/relay_v2/
├── api/
│   ├── __init__.py
│   ├── runs.py             # POST /api/runs, GET /api/runs, /preview, etc.
│   ├── events.py           # GET /api/runs/:id/events + SSE /api/events/:run_id
│   ├── projects.py         # CRUD on projects
│   ├── prompts.py          # CRUD on prompts (versioned)
│   ├── files.py            # GET /api/projects/:id/files + /files/*
│   ├── schemas.py          # Pydantic request/response models
│   └── tests/              # pytest-asyncio + httpx
└── sse.py                  # SSE broadcaster: in-process channel + db tail
```

**Key implementation notes:**

- All routes go through `RelayCore`. No direct DB access in route
  handlers.
- SSE: each subscriber gets a fresh `asyncio.Queue`; the EventStore
  writer broadcasts new events to all subscribers for the matching
  `run_id`. On reconnect with `Last-Event-ID`, the route first replays
  missing events from the DB, then subscribes to live.
- For finished runs, SSE returns history paginated and closes (204 on
  exhaustion).
- The **file browser** is sandboxed to the project's `root_path` —
  paths are normalized and `..` traversal is rejected with HTTP 400.
  Binary files (detected by null-byte probe in the first 8KB) return
  415; the frontend offers a download link instead.
- The **run preview** endpoint (`GET /api/runs/:id/preview`) renders
  the prompt + preamble that *would* be sent, without side effects.
  Used by the dashboard's New Run wizard to give the human a final
  review step before commitment.
- OpenAPI tags grouped by resource. The auto-generated schema is used
  by the frontend to produce a typed client.

**Verification:**
- Full API integration test suite via `httpx.AsyncClient`. Each
  endpoint has at least a happy-path test.
- File browser refuses `../`, `/etc/passwd`, and symlink-out attacks
  (verified by negative tests).
- Binary detection returns 415 for a known binary fixture; markdown
  returns 200 with text body.
- SSE test: connect, spawn a run via REST, assert events arrive in
  order; reconnect mid-stream with `Last-Event-ID`, assert no gap, no
  duplicate.
- `curl http://127.0.0.1:7800/openapi.json` returns a valid OpenAPI v3
  schema.

**Risks:**
- SSE buffering / proxy interaction on reverse-proxy deployment.
  Mitigation: document the `X-Accel-Buffering: no` header
  requirement for nginx.
- File-browser security. Mitigation: explicit normalization +
  negative test coverage; sandboxing implemented in a single audited
  function.

---

## Phase 4 — dashboard MVP (8 days)

**Goal.** A functional Vue 3 dashboard implementing the *primary
control plane* per ADR-15 and spec §9.1: hub, project view (runs +
prompts + files panes), New Run wizard with preview, and run detail
view (timeline + iters + artifacts + worktree panes).

**Deliverables:**

```
relay-v2/frontend/
├── package.json            # vue 3, pinia, pinia-colada, vite,
│                           # vue-router, markdown-it, shiki, mermaid,
│                           # diff2html, plus dev: vitest, vue-test-utils,
│                           # openapi-typescript
├── vite.config.ts          # dev proxy to backend on :7800
├── tsconfig.json
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── api/
│   │   ├── client.ts       # generated typed client from openapi.json
│   │   └── sse.ts          # EventSource wrapper with Last-Event-ID
│   ├── stores/             # Pinia stores: projects, runs, events,
│   │                       # prompts, files, worktree
│   ├── views/
│   │   ├── HubView.vue
│   │   ├── ProjectView.vue
│   │   ├── NewRunWizard.vue
│   │   └── RunDetailView.vue
│   ├── components/
│   │   ├── runs/           # TimelinePane, ItersPane, ArtifactsPane,
│   │   │                   # WorktreePane, ToolCallCard, SignalCard,
│   │   │                   # PauseAnswerForm
│   │   ├── files/          # FileTree, FileViewer, MarkdownRender,
│   │   │                   # CodeRender, MermaidRender, DiffRender
│   │   ├── prompts/        # PromptList, PromptEditor, PromptVersions
│   │   ├── projects/       # ProjectList, RegisterProjectForm
│   │   └── shared/         # StatusBadge, ActionButton, etc.
│   ├── lib/
│   │   ├── render.ts       # markdown/code/mermaid pipeline
│   │   └── routes.ts       # router config
│   └── styles/
└── tests/                  # vitest + vue test utils
```

**Key implementation notes:**

- **API client** generated via `openapi-typescript` from the running
  backend's `/openapi.json`. Re-generated on backend changes via a
  small `package.json` script.
- **Pinia Colada** for REST cache; SSE pushes invalidate matching
  keys. Pinia stores hold ephemeral UI state (selection, toggles).
- **Timeline** renders events oldest-to-newest with virtualized
  scrolling for runs > 1000 events. `signal_emit` events get
  distinctive styling (banner color, anchor link). Tool call cards
  collapse args/result to <8 lines by default with a "show full"
  toggle.
- **File browser** (per spec §9.4):
  - File tree on the left, content on the right
  - Markdown rendered via `markdown-it` with table / footnote / task-list plugins
  - Code blocks highlighted via `shiki` (lazy-loaded grammars)
  - Mermaid code fences rendered via `mermaid.js` to inline SVG
  - Binary files: "binary content (N bytes) — download" link
  - Diff view for two-file comparison via `diff2html`
- **New Run wizard** is a 4-step flow:
  1. Pick prompt (existing or write inline)
  2. Set options (max_iters, iter_timeout, model)
  3. Preview (`GET /api/runs/:id/preview`) — read carefully
  4. Start (`POST /api/runs`)
  Step 3 is the "not scary" review step; nothing has happened yet at
  that point.
- **Prompts CRUD**: list / view / create / edit / delete / version
  history. Edits bump version; old versions remain readable.
- **Projects CRUD**: register / view / unregister (does not delete
  files on disk). Registration is a single form: path + display name.

**Verification:**
- A run with mixed event types renders all of them readably.
- Live tail keeps up with a real pi run (verified by eye + by
  comparing event count in the DB to the count rendered).
- Reconnect after browser tab sleep works (`Last-Event-ID` resume).
- Pause / resume flow is exercisable from the UI.
- File browser:
  - Markdown with mermaid renders correctly (use a fixture file with
    a flowchart)
  - Code highlighting works for Python, TypeScript, Vue, Bash, SQL,
    JSON, YAML
  - Diff renders correctly for two versions of a file
- New Run wizard:
  - Preview step shows the full prompt + preamble before commit
  - Start button is disabled until preview has been viewed
  - Cancellation from the wizard creates no row in `runs`
- Prompts:
  - Create / edit / version-history flow works
  - Version history is read-only

**Risks:**
- **Scope creep.** This is the largest single phase. Mitigation: the
  feature list above is the MVP cap. New ideas go to post-MVP unless
  they remove scope from this list.
- **Bundle size** from Vue + Pinia + Pinia Colada + markdown-it +
  shiki + mermaid + diff2html. Target <800KB gzipped (revised up from
  500KB to accommodate shiki + mermaid). Mitigation: lazy-load views
  and renderers; tree-shake.
- **Subagent visibility** — the data isn't there in MVP. Leave the
  UI ready for it (`parent_run_id` rendering) but don't block on the
  dispatch implementation.

---

## Phase 5 — MCP server (2 days)

**Goal.** External MCP clients (Claude Desktop, Claude Code) can
manage relay runs via the standard MCP transport.

**Deliverables:**

```
src/relay_v2/mcp/
├── __init__.py
├── server.py               # FastMCP setup with tools per spec §8
└── tests/
    └── test_mcp_tools.py
```

**Key implementation notes:**

- FastMCP mounted at `/mcp` (default path — avoids issue #1367).
- Each tool maps 1:1 to a `RelayCore` method.
- Output schemas use the same Pydantic models as the REST API for
  consistency.
- A `mcp-config.example.json` snippet in `docs/` documents how to
  register relay-v2 as an MCP server in Claude Desktop / Code.

**Verification:**
- Add the local relay-v2 to a Claude Code project's `.mcp.json`; invoke
  `relay__list_runs` from a Claude conversation and observe the result.
- Unit tests against the MCP server in-process.

**Risks:**
- Streamable HTTP transport tooling churn. Mitigation: pin the `mcp`
  Python package version.

---

## Phase 6 — engineering-team skill port (3 days)

> **Superseded delivery model (2026-05-25, ADR-44).** This phase
> originally shipped `relay install-skill` to copy the skill into
> `<target>/.claude/skills/`. That path is a Claude Code discovery
> root pi never reads — the command was silently inert. Relay now
> injects the bundled skill into pi via `pi --skill <bundled-path>`
> at spawn time; `relay install-skill` was deleted outright. The
> per-iter execution model below (RELAY_PHASE/RELAY_RUN_DIR
> preamble, sentinel grammar, single-session-per-phase, worktree
> provisioning) is unchanged.

**Goal.** A v2-flavored `engineering-team` skill, bundled into the
`relay` package and injected into pi automatically (ADR-44), that runs
a real evaluation + plan + develop cycle against a fixture project.

**Deliverables:**

```
relay-v2/skills/engineering-team/
├── SKILL.md                # router; reads RELAY_PHASE + RELAY_RUN_DIR
├── phases/
│   ├── phase-1-evaluation.md
│   ├── phase-2-planning.md
│   ├── phase-3-development.md
│   └── phase-4-wrap-up.md
├── references/
│   ├── sentinels.md        # ported from v1 with optional cleanups
│   ├── team-structure.md
│   ├── workflows.md
│   ├── worktree.md
│   └── general-guidelines.md
└── (v2 version of any phase-specific templates)
```

Also (current — ADR-44):

```
src/relay_v2/harness/skills.py      # bundled_skill_dir() resolver
src/relay_v2/harness/pi.py          # _build_argv appends --skill <path>
src/relay_v2/config.py              # Settings.pi_skill_paths + RELAY_PI_SKILLS env
```

**Key implementation notes:**

- Preamble fields from spec §12: `RELAY_PHASE`, `RELAY_RUN_DIR`,
  plus any new fields revealed during the port.
- The signaling format starts identical to v1 (text sentinels with
  prompt-markers); cleanups, if any, are made deliberately in this
  phase, not by accident.
- Subagent dispatch is *not* implemented in MVP — the skill operates
  with one long iter per phase. (Subagent support is a post-MVP
  feature when relay's orchestrator gains the subagent_dispatch
  signal handler.)
- Delivery (ADR-44): relay's pi harness appends one `--skill <path>`
  pair per `Settings.pi_skill_paths` entry on every spawn; default is
  the bundled tree at `skills/engineering-team/pi/` (resolved by
  `bundled_skill_dir()`); env override `RELAY_PI_SKILLS` is
  colon-separated; empty string opts out (pi then sees only its own
  auto-discovered skills under `<cwd>/.pi/skills/` and
  `~/.pi/agent/skills/`).

**Verification:**
- Run relay-v2 against the v1 demo fixture (the deliberately broken
  factorial(5) == 24 code). Confirm the agent evaluates, plans, and
  fixes the bug across multiple iters.
- The dashboard renders the full multi-phase run cleanly.

**Risks:**
- Skill prose quality. The v1 skill is mature; the v2 port should not
  regress its quality. Mitigation: do a side-by-side diff during the
  port and don't rewrite for the sake of rewriting.

---

## Phase 7 — OTel + Langfuse export (2 days)

**Goal.** When `RELAY_OTEL_EXPORT=langfuse`, every iter and tool call
appears as a Langfuse trace with proper nesting and token-cost
attribution (where pi surfaces those fields).

**Deliverables:**

```
src/relay_v2/observability/
├── __init__.py
├── otel.py                 # tracer setup, span helpers
└── tests/
    └── test_otel_export.py # verify span structure (no real network)
```

**Key implementation notes:**

- Use `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`.
- Spans: `relay.run` (root per run), `relay.iter` (per iter, child of
  run), `relay.tool_call` (per `ToolUseEnd`, child of iter).
- GenAI semantic conventions where applicable (`gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.usage.*`).
- When `RELAY_OTEL_EXPORT=none`, the tracer is a no-op — no overhead.

**Verification:**
- With Langfuse running locally, a real run produces a trace tree in
  the Langfuse UI with the expected nesting.
- With `RELAY_OTEL_EXPORT=none`, no network calls are made and there
  is no measurable runtime overhead.

**Risks:**
- Pi may not surface token counts in its events. Mitigation: relay
  records `gen_ai.usage.*` only when present; gracefully omits when
  absent.

---

## Phase 8 — verification & polish (2 days) — **COMPLETE (2026-05-19, ADR-30)**

**Goal.** MVP-quality polish. CI passes. Docker image builds. README
covers install + run.

> **Delivered.** README rewritten (Phases 0–8; install/run/dashboard/
> MCP/observability/Docker); additive conditional `StaticFiles` SPA
> mount (`src/relay_v2/api/static.py`, spec §11.2) + 2 tests; multi-
> stage `Dockerfile` + `.dockerignore` + `docker-compose.example.yml`;
> `.github/workflows/ci.yml` (full Python + frontend gate, GHCR
> publish on push to `main`, `workflow_dispatch`); ADR-30 + spec §11.2
> note + CLAUDE.md update. Suite 192→**194 passed**, 3 pi-e2e gated;
> ruff + `mypy --strict` clean (39 source files); coverage 94%.
> `docker build` + container-boot smoke verified locally (`/health`,
> `/`, deep SPA route, `/openapi.json`). The automated/manual split is
> ADR-30. **Outstanding (manual, owner-run, journal-attested):** the
> end-to-end `relay start eng-team-demo.md` demo and the live-Langfuse
> trace-tree acceptance (carried from Phase 7); and the latent ADR-10
> `agent_end`-not-persisted gap, which is deliberately deferred to its
> own ADR + spec §6 change (ADR-29/ADR-30).

**Deliverables:**

- `README.md` rewrite covering install, run, dashboard URL, MCP setup.
- `Dockerfile` + `docker-compose.example.yml` (with Langfuse compose).
- `.github/workflows/ci.yml`: pytest + ruff + mypy + Docker build &
  publish to `ghcr.io/johnmathews/relay` (per user's global Docker
  policy).
- `journal/` initial entry per global instructions.
- Final review pass on all phase 0–7 deliverables.

**Verification:**
- Fresh checkout → `uv sync && uv run relay serve` works.
- Docker image pulls and runs.
- CI is green on a fresh PR.
- One end-to-end demo: `relay start eng-team-demo.md` against the v1
  fixture, dashboard shows the full run, MCP server is callable from
  Claude Code.

## Total estimated effort

| Phase | Days |
|---|---|
| 0 — scaffold | 2 |
| 1 — harness | 3 |
| 2 — orchestrator | 3 |
| 3 — REST + persistence | 3 |
| 4 — dashboard | 8 |
| 5 — MCP | 2 |
| 6 — skill port | 3 |
| 7 — observability | 2 |
| 8 — polish | 2 |
| **Total** | **28 days** |

Single-developer realistic: 5–6 calendar weeks (with task-switching,
debugging slack, and the inevitable surprises). Parallel work on
phases 3 & 4 could compress by 1–2 days. The dashboard is the largest
phase by design (per ADR-15, it is the primary control plane).

## Post-MVP phases (sketch)

Not part of the MVP plan, but architecturally enabled:

### Phase 9a–9g — fanout-join + session-ended persistence (post-MVP) — **complete**

Out-of-band from the original sketch: the **post-MVP** orchestrator
gained parallel-iter fanout/join via the proposal at
`docs/archive/parallel-iters-fanout-join.md`. The slot was previously
projected as "subagent dispatch" (Phase 13 below, now superseded); the
fanout-join arc absorbed it and was tracked as sub-phases of 9 to keep
the work visibly sequential against Phases 0–8.

- **9a** (ADR-34) — schema + events: `awaiting_children` status,
  `child_runs_resolved` kind, startup cascade-cancel helper.
- **9b** (ADR-35) — fanout dispatch: sentinel parser
  (`[[engteam:fanout-start]]…[[engteam:fanout-end]]` JSON + closing
  `[[engteam:fanout]]` verb), child-run creation, concurrency
  semaphore, depth bound.
- **9c** (ADR-36) — join watcher + synthesizer iter. `subagent_return`
  + `child_runs_resolved` emitted on parent; parent re-enqueues with
  `compose_join_prompt` body + `RELAY_CHILD_RESULTS:` trailer.
- **9d** (ADR-37) — runtime cancel-cascade. Parent-first ordering;
  in-flight descendants get fire-and-forget signals; DB-only
  descendants get direct status flips.
- **9e** — dashboard "Children" pane + Parent chip + Show-child-runs
  toggle + Cancel cascade copy.
- **9f** (ADR-38) — OTel span parenting across runs: one connected
  trace tree in Langfuse. Automated `InMemorySpanExporter` test +
  manual live-Langfuse-UI acceptance (gated like `PI_INTEGRATION=1`,
  journal-attested per ADR-30).
- **9g** (ADR-39) — `harness_session_ended` persisted as an event row
  (closes the latent ADR-10 gap that `SessionEnded` was captured by
  Option-D and surfaced to OTel but never written to the events
  table). New event kind in spec §3.2; `UsageRow.vue` renders
  stop_reason + summed token counts inline in the timeline.

**Post-9g bug-fix sweep (2026-05-23).** Three independent regressions
filed in the 9f live-acceptance journal; each shipped as its own
commit chain. (1) `UsageRow.vue` was reading Anthropic-API token
names; fixed to pi-flavoured ADR-18 keys (`input`/`output`/
`cacheRead`/`cacheWrite`/`totalTokens` + `cost.total`). (2)
`provision_workspace` was using `settings.data_dir` for the worktree
path (spec §3.3 violation); fixed to `<project_root>/.relay/...` via
`project_data_dir`. (3) `KNOWN_EVENT_TYPES` was missing
`harness_session_ended` + `child_runs_resolved`; the browser
`EventSource` only fires listeners for registered named events, so
live events of those kinds were silently dropped (refresh worked
because it hit REST replay — masking the bug). The dual-list
invariant (`KNOWN_EVENT_TYPES` + `INVALIDATING_KINDS` must agree)
is now documented in `docs/dashboard.md`. No new ADR — all three
are pure bug fixes restoring existing contracts.

### Phase 14a–14f — pause-for-review (post-MVP) — **complete**

The pause-for-review arc landed on top of the existing `pause` /
`resume` mechanism. Source proposal: `docs/archive/pause-for-review.md`.

- **14a** (ADR-40) — `PUT /api/runs/:id/artifacts/{path}` write
  endpoint + `artifact_edited` event kind + `PauseReviewError` codes
  → HTTP mapping. Single write entry point on the run artifacts dir;
  coupled to `paused` + `signal_args.review_path` (set by 14b).
- **14b** (ADR-40 grammar half) — sentinel parser gains the optional
  `review_path="<rel>"` attribute on `pause-for-input` lines.
- **14c** — dashboard `PauseAnswerForm` inline review pane: textarea
  + lazy markdown preview + Save button + 404 "Create at this path"
  / 415 binary branches.
- **14d** — engteam Phase-2 template emits
  `review_path="improvement-plan.md"` on its closing pause sentinel
  (skill-template + journal only; no backend / frontend change).
- **14e** — audit polish: `[ Preview | Diff ]` toggle on the right
  pane; timeline `artifact_edited` rows click-to-navigate;
  `relay.pause.artifacts_edited_count` scalar OTel attribute on the
  resumed iter span; engteam Phase-2 → fanout reference cross-link
  (closes deferred 9e follow-up).
- **14f** (ADR-41) — plural `review_paths` via repeated-attribute
  grammar; dashboard renders a tab per path when N > 1 (per-tab
  dirty state, single Save in flight at a time); engteam template
  unchanged (still emits a single `review_path` — plural is opt-in
  for future skills or non-engteam callers).

### Numbering note

The entries below ("Phase 9 — remote access" onward) are
pre-existing post-MVP sketches and were NOT renumbered when the
fanout-join arc took the 9a–9g sub-numbering; "remote access" is
functionally Phase 10-ish now. Phase 13 (subagent dispatch) is
**superseded by 9a–9g**. Phase 14 (pause-for-review) is **shipped
as 14a–14f**. The sketch below preserves the original numbering for
the items that have not been started.

### Phase 9 — remote access (1 week)

- Containerize for deployment to a VPS
- TLS termination via Caddy
- GitHub OAuth gated to the user's GitHub ID
- Session cookies / bearer tokens
- Same backend; new auth middleware

Goal: "drive relay from any device; laptop-sleep doesn't kill runs."

### Phase 10 — container-per-run isolation (2 weeks)

- Each run executes inside an ephemeral Docker container
- GitHub clone-on-the-fly via PAT (or GitHub App later)
- Workspace lives inside the container; no shared host filesystem
- Per-run blast radius isolation; sets up multi-user when it arrives

### Phase 11 — multi-user (when actually needed)

- Per-user API keys / OAuth tokens
- Per-user concurrent-run limits
- Audit logging
- Real RBAC (probably via Casbin or similar)

### Phase 12 — scheduled runs (later)

- A scheduler that POSTs runs to the existing `/api/runs` endpoint
- Cron-style schedule definition
- The data model already separates "submit a run" from "execute a run"

### Phase 13 — subagent dispatch (medium-term) — **SUPERSEDED**

Superseded by Phase 9a–9g (fanout-join). The `subagent_dispatch` /
`subagent_return` / `child_runs_resolved` events are live; the
orchestrator spawns child runs with `parent_run_id`; the dashboard
Children pane renders them; the engteam skill can adopt the fanout
sentinel when ready (currently opt-in).

### Phase 14 — pause-for-review (medium-term) — **SHIPPED**

Shipped as Phase 14a–14f. See above.

### Phase 15 — prompt library UI (later)

- Browse / version / share prompts across projects via the dashboard
  (the MVP supports per-project prompts; this extends to a cross-
  project / shared library).
- Langfuse's prompt-management feature lit up for real.

---

## Risk tracking (live)

| Risk | Severity | Mitigation | Status |
|---|---|---|---|
| Pi + Max billing per-token | High | Verify with user before sustained deployment | OPEN (ADR-09) |
| Pi version churn breaks events | Medium | Pin to v0.74.0; test on upgrade | OPEN |
| Pi billing change affects `--mode rpc` differently | Low | MVP uses `--mode json`; `--mode rpc` deferred | parked |
| Subagent absence regresses skill quality | Medium | Single-iter-per-phase initially; subagent phase later | accepted for MVP |
| Bundle size on dashboard | Low | Lazy-load + tree-shake | accepted as phase-4 verification |
| SSE + reverse-proxy buffering | Low | Documented header workaround | parked |

## What "done with MVP" looks like

- `relay serve` runs the daemon.
- `relay start prompt.md` against a real project starts a multi-iter
  run.
- The Vue dashboard at `http://127.0.0.1:7800/` shows the hub, the
  project, and the run timeline live.
- Pause/resume works from the dashboard.
- An MCP client (Claude Desktop) can list runs and start a new one.
- The engineering-team skill v2 successfully fixes the v1 demo fixture
  bug across multiple iters.
- Langfuse (if configured) shows the full trace tree.
- CI is green; Docker image is published.

When all of these are true, v1 is deprecated and v2 is the project's
primary tool.
