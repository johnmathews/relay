# relay v2 — design spec

> Canonical design document. Architecture, data model, harness
> abstraction, signaling, REST + MCP surface, dashboard, observability.
>
> Companion docs: `motivation.md` (why), `decisions.md` (ADRs with
> rationale), `plan.md` (phased implementation sequence).
>
> This doc is updated as design evolves. ADRs in `decisions.md` are
> append-only; this doc reflects the current consensus.

## 1. Overview

relay v2 is a Python service that orchestrates chained agent sessions
against a swappable headless harness, with a structured event store as
the source of truth and a Vue dashboard for live + replay observability.

**One paragraph:** A FastAPI service hosts the orchestrator, a REST API,
an MCP server, an SSE event feed, and a single-file SQLite event store.
The orchestrator drives a pluggable harness (`PiHarness` for MVP)
spawning one subprocess per iter. Each iter's events flow into the
store; the dashboard tails them over SSE and reads history from the
same store. A small wire protocol (text sentinels in MVP, MCP tools
optional later) signals state transitions between the agent and the
orchestrator. Single-user, localhost-only.

## 2. Architecture

```
┌─ FastAPI Python process (relay-v2 daemon) ──────────────────────────┐
│                                                                     │
│   ┌─ HTTP/REST routes ─┐                                            │
│   │  /api/runs         │                                            │
│   │  /api/runs/:id     ├──→ RelayCore service layer ────────────┐   │
│   │  /api/events       │      (single shared in-process object) │   │
│   │  /api/projects     │                                        │   │
│   └────────────────────┘                                        │   │
│                                                                 │   │
│   ┌─ MCP server (/mcp) ──────┐                                  │   │
│   │  relay__start_run        ├──→ same RelayCore ───────────────┤   │
│   │  relay__pause_response   │                                  │   │
│   │  relay__cancel_run       │                                  │   │
│   │  relay__tail_events      │                                  │   │
│   └──────────────────────────┘                                  │   │
│                                                                 ↓   │
│   ┌─ SSE /api/events/:run_id ──→ EventStore (sqlite) ←─┐      ┌────┐│
│   │  Last-Event-ID resume       (single writer)        │      │    ││
│   └────────────────────────────────────────────────────┘      │ O  ││
│                                                  ↑            │ r  ││
│   ┌─ Orchestrator (asyncio TaskGroup, lifespan-managed) ──────┤ c  ││
│   │  one task per active Run                                  │ h  ││
│   │    • spawn harness session                                │ e  ││
│   │    • stream HarnessEvents into store                      │ s  ││
│   │    • detect signals (text_sentinels|mcp_tools)            │ t  ││
│   │    • iterate / pause / done                               │ r  ││
│   │    • spawn subagents (new harness sessions)               │ a  ││
│   └───────────────────────────────────────────────────────────┴────┘│
│                              ↑                                       │
│                      Harness adapter ──→ subprocess(pi --mode json)  │
│                      (PiHarness)                                     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ (optional bolt-on)
                  OpenTelemetry export → Langfuse (self-hosted)
                              ↓
                  Vue 3 + Pinia dashboard (separate dev server in dev,
                  static bundle served by FastAPI in prod)
```

**Process model.** Single FastAPI process. The orchestrator runs as an
`asyncio.TaskGroup` task started in FastAPI's `lifespan` context
manager. REST routes, MCP tools, and the orchestrator all share one
`RelayCore` instance — no IPC, no broker. Per ADR-07.

**All writes flow through `RelayCore`.** REST routes, MCP tools, and
the orchestrator all mutate state via `RelayCore` methods. Routes never
touch the database directly; the orchestrator is the sole writer of
event rows, but `RelayCore` is the sole authority for starting runs,
registering projects, creating/updating prompts, cancelling, resuming.
This replaces v1's "dashboard never writes" invariant with the stronger
"one service layer, one set of mutation paths" — the safety property
v1 was protecting is preserved without artificially restricting the
dashboard's role. The dashboard is a first-class control surface
(ADR-15), not a read-only spectator.

## 3. Data model

SQLite via SQLAlchemy in MVP. Schema designed to migrate cleanly to
Postgres later. JSON columns use SQLAlchemy's portable `JSON` type.

### 3.1 Tables

```sql
-- Projects (1 row per relay-managed project root).
CREATE TABLE projects (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  root_path     TEXT NOT NULL UNIQUE,    -- absolute project root on disk
  name          TEXT NOT NULL,           -- display name
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id       INTEGER NOT NULL DEFAULT 1   -- FK reserved for multi-user
);

-- Users (single sentinel row in MVP; multi-user is additive).
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  external_id   TEXT UNIQUE,             -- GitHub login etc. (nullable)
  display_name  TEXT NOT NULL DEFAULT 'me'
);

-- Prompts (reusable; referenced by runs, not copied).
CREATE TABLE prompts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id    INTEGER REFERENCES projects(id),
  name          TEXT NOT NULL,           -- slug, unique per project
  version       INTEGER NOT NULL DEFAULT 1,
  body          TEXT NOT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id       INTEGER NOT NULL DEFAULT 1,
  UNIQUE(project_id, name, version)
);

-- Runs (one row per `relay start` invocation).
CREATE TABLE runs (
  id            TEXT PRIMARY KEY,        -- "20260519-113054" or "...-abcd"
  project_id    INTEGER NOT NULL REFERENCES projects(id),
  prompt_id     INTEGER REFERENCES prompts(id),  -- nullable: ad-hoc prompts allowed
  prompt_body   TEXT NOT NULL,                   -- snapshot at run start
  user_id       INTEGER NOT NULL DEFAULT 1,
  status        TEXT NOT NULL,           -- 'running'|'done'|'failed'|'paused'|'cancelled'|'awaiting_children'
                                         -- ``awaiting_children`` — parent run is suspended pending
                                         -- completion of child runs dispatched via fanout. Not
                                         -- terminal; transitions back to ``running`` when all
                                         -- children settle (9c). Set under the S1 cancel-with-cascade
                                         -- convention on server restart (ADR-34, 9a).
  started_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at      TIMESTAMP,
  max_iters     INTEGER NOT NULL DEFAULT 12,
  iter_timeout  INTEGER NOT NULL DEFAULT 1800,   -- seconds
  worktree_path TEXT,                            -- absolute, nullable
  branch        TEXT,                            -- per-run branch name
  parent_run_id TEXT REFERENCES runs(id)         -- for subagent runs
);

-- Iters (one row per iter within a run).
CREATE TABLE iters (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  seq           INTEGER NOT NULL,        -- 1-indexed within run
  phase         TEXT,                    -- 'evaluation'|'planning'|'development'|'wrap-up'|NULL
  pi_session_id TEXT,                    -- pi's session UUID for this iter
  prompt        TEXT NOT NULL,           -- the prompt sent to pi
  preamble      TEXT NOT NULL,           -- the RELAY_* preamble (snapshot)
  signal_kind   TEXT,                    -- terminal signal that closed this iter: 'handoff'|'done'|'pause'|'fanout'|NULL
                                         -- (mid-iter signals like 'phase_start' / 'unit_done' are recorded only in the events table)
  signal_args   JSON,                    -- {next_prompt, summary, question, ...}
  started_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at      TIMESTAMP,
  exit_reason   TEXT,                    -- 'signal'|'agent_end_no_signal'|'crash'|'timeout'|'cancelled'
  UNIQUE(run_id, seq)
);

-- Events (append-only; the source of truth for observability).
CREATE TABLE events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  iter_id       INTEGER REFERENCES iters(id),    -- nullable: run-level events
  seq           INTEGER NOT NULL,        -- monotonic per run
  ts            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  kind          TEXT NOT NULL,           -- see event taxonomy below
  payload       JSON NOT NULL,
  UNIQUE(run_id, seq)
);

CREATE INDEX idx_events_run_seq ON events(run_id, seq);
CREATE INDEX idx_iters_run     ON iters(run_id);
CREATE INDEX idx_runs_project  ON runs(project_id);
```

### 3.2 Event taxonomy

The `events.kind` column is a discriminator. Payloads are JSON.

| kind | when emitted | payload shape |
|---|---|---|
| `run_started` | `relay start` | `{project_id, prompt_body, max_iters}` |
| `iter_started` | iter N begins | `{seq, prompt, preamble, phase}` |
| `assistant_text` | accumulated text from a turn | `{text, turn_seq, kind}` — `kind ∈ {"text", "thinking"}` discriminator (ADR-18) so replay can distinguish visible assistant output from extended thinking |
| `tool_use_start` | agent invokes a tool | `{tool_id, name, args}` |
| `tool_use_end` | tool returns | `{tool_id, result, is_error, duration_ms}` — `result` is truncated to `TOOL_RESULT_CAP` (16 KiB) at write time; oversize content is preserved on the harness side but not in the event store |
| `signal_emit` | parser detects handoff/done/pause/phase_start/etc. | `{kind, args}` |
| `subagent_dispatch` | orchestrator spawns a subagent run | `{child_run_id, role, prompt}` |
| `subagent_return` | subagent run finishes; emitted on the parent's stream by the join watcher (9c). | `{child_run_id, status, summary}` |
| `child_runs_resolved` | after all children of an `awaiting_children` parent reach a terminal status; immediately before the parent's synthesizer iter is enqueued (9c). Optional but recommended for replay diffing — derivable from the preceding `subagent_return` events. | `{children_count, terminal_statuses}` (`terminal_statuses` is `dict[run_id, status]`) |
| `harness_session_ended` | iter's harness session terminates (every close path) — appended **before** `iter_ended` (spec §6, ADR-39) | `{stop_reason, messages, summary}` — `stop_reason ∈ {clean, crash, timeout, cancelled}`; `messages` is `SessionEnded.messages` verbatim (ADR-18 opaque); `summary` populated only on the `done` close path, `null` otherwise |
| `artifact_edited` | dashboard (or other REST client) writes content to a run's artifact during a `paused` review (§6.2, §7; ADR-40). Iter-scoped to the **paused iter** so replay can group edits under the pause that motivated them. Never terminal — `runs.status` stays `paused`. | `{path, size_before, size_after, sha256_before, sha256_after, editor}` — `path` relative to the run artifacts dir; hashes are hex SHA-256 strings (`sha256_before` is `null` on create); `editor` is a free-form identifier for the writer (default `"dashboard"`). The edited content lives on disk, not in the payload — ADR-40 §B1 names this audit gap; the hashes give an integrity check. |
| `iter_ended` | iter N closes | `{seq, signal_kind, exit_reason}` |
| `pause_requested` | pause signal handled | `{question}` |
| `pause_resolved` | answer received | `{answer}` |
| `run_ended` | run terminates | `{status, summary}` |

This is a deliberately small set. New event kinds are added as needed.

### 3.3 On-disk layout

Per-project, under `<project_root>/.relay/`:

```
<project_root>/.relay/
├── relay.db                       SQLite event store (single writer: orchestrator)
├── worktrees/<run_id>/            per-run git worktree (code workspace — agent does its
│                                  file work here; branch named per-run, never on main)
└── runs/<run_id>/                 per-run artifacts directory — improvement-plan.md,
                                   evaluation-report.md, discussions/, automation logs.
                                   This is where `RELAY_RUN_DIR` resolves (§12), and what
                                   the dashboard's Artifacts pane (§9.1) browses.
```

The artifacts directory (`runs/<run_id>/`) is deliberately a sibling of
`worktrees/`, **not nested inside the worktree**. Artifacts are
cross-phase (e.g. the evaluation report from Phase 1 is referenced by
Phase 3); decoupling them from any individual worktree's branch
lifecycle keeps the dashboard's view of artifacts stable across phases
and across worktree teardowns.

Pi sessions persist under `~/.pi/agent/sessions/...` (pi's own
location); relay tracks `pi_session_id` in the `iters` table so the
session file can be located if needed for crash recovery.

## 4. Harness layer

Per ADR-04. The harness is the only component that knows about pi
specifically.

### 4.1 Protocols and event types

```python
# relay_v2/harness/protocol.py

from typing import Protocol, AsyncIterator, Literal
from dataclasses import dataclass
from pathlib import Path

@dataclass
class HarnessEvent:
    """Base class — see subclasses below."""
    seq: int      # monotonic within the session
    ts: float     # unix epoch seconds

@dataclass
class SessionStarted(HarnessEvent):
    session_id: str
    cwd: str

@dataclass
class AssistantText(HarnessEvent):
    text: str
    turn_seq: int   # which turn within the session
    kind: Literal["text", "thinking"] = "text"  # ADR-18; signaling sees "text" only

@dataclass
class ToolUseStart(HarnessEvent):
    tool_id: str
    name: str
    args: dict

@dataclass
class ToolUseUpdate(HarnessEvent):
    tool_id: str
    partial_result: dict

@dataclass
class ToolUseEnd(HarnessEvent):
    tool_id: str
    result: dict
    is_error: bool
    duration_ms: int

@dataclass
class SessionEnded(HarnessEvent):
    messages: list   # final compiled message list
    stop_reason: str  # 'clean'|'crash'|'timeout'|'cancelled'

# Strategy config for signaling
@dataclass
class SignalConfig:
    strategy: Literal["text_sentinels", "mcp_tools"]
    # text_sentinels: patterns to match in AssistantText events
    # mcp_tools: tool-name prefix to watch (e.g. "relay__")
    mcp_tool_prefix: str = "relay__"

class HarnessSession(Protocol):
    session_id: str

    async def events(self) -> AsyncIterator[HarnessEvent]: ...
    async def cancel(self) -> None: ...
    async def wait(self) -> SessionEnded: ...

class Harness(Protocol):
    name: str

    async def spawn(
        self,
        prompt: str,
        cwd: Path,
        env: dict[str, str],
        signal_config: SignalConfig,
        resume_from: str | None = None,
    ) -> HarnessSession: ...
```

### 4.2 PiHarness — concrete implementation

`PiHarness` translates between pi's JSONL schema and relay's
`HarnessEvent` types. Mapping (confirmed by de-risking runs):

| pi event | relay `HarnessEvent` |
|---|---|
| `session` | `SessionStarted(session_id=id, cwd)` |
| `message_update` w/ `assistantMessageEvent.type == "text_delta"` | `AssistantText(text, turn_seq, kind="text")` (accumulated per turn, flushed at `turn_end`) |
| `message_update` w/ `assistantMessageEvent.type == "thinking_delta"` | `AssistantText(text, turn_seq, kind="thinking")` (accumulated per turn; ADR-18) |
| `tool_execution_start` | `ToolUseStart(tool_id=toolCallId, name=toolName, args)` |
| `tool_execution_update` | `ToolUseUpdate(tool_id, partial_result)` |
| `tool_execution_end` | `ToolUseEnd(tool_id, result, is_error=isError, duration_ms)` |
| `agent_end` | `SessionEnded(messages, stop_reason="clean")` |
| `agent_start`, `turn_start`, `message_start`, `message_end`, `turn_end`; all `assistantMessageEvent` `*_start`/`*_end` framing; `toolcall_*` sub-types; any unrecognised sub-type or top-level event | consumed internally for accounting; not surfaced (ADR-18) |

Invocation form (per ADR-16, which amends ADR-03's original choice of `--mode rpc`):

```
PI_AGENT_SDK=1 pi -p "<prompt>" \
  --mode json \
  --provider anthropic \
  --model <model> \
  [--continue | --session <path> | --fork <path>]
  [extra flags...]
```

Use `--mode json` (one-shot, JSONL stdout) rather than `--mode rpc` for
MVP — simpler subprocess lifecycle, matches relay's "one iter, one
subprocess" model. `--mode rpc` is held in reserve for future bidirectional
control needs.

Cancellation: `proc.terminate()` followed by `proc.wait(timeout=5)` then
`proc.kill()` if needed. Pi has explicit RPC `abort` commands but those
require `--mode rpc`; for `--mode json`, signal-based cancellation is the
documented approach.

## 5. Signaling

Per ADR-05. Two strategies. The orchestrator emits normalized
`SignalEmitted(kind, args)` regardless of strategy.

### 5.1 `text_sentinels` (MVP strategy on pi)

Inherits v1's sentinel grammar with optional schema cleanup. The parser
watches `AssistantText` events **with `kind == "text"` only** (accumulated
per turn at `turn_end`), matches line-anchored patterns against the text,
and emits `SignalEmitted`. Restricting to `kind == "text"` is the v2 form
of v1's anti-mention discipline (ADR-18): v1's `jq` filter stripped tool
inputs before parsing; v2 additionally never feeds `kind == "thinking"`
(chain-of-thought) text to the parser, so a sentinel mentioned while the
model is reasoning cannot fire a false signal. The full grammar lives in `signaling/sentinels.md`
inside the v2 repo (TBD — port from v1 with optional revisions).

Signal kinds: `phase_start`, `unit_start`, `unit_done`, `unit_abandoned`,
`handoff`, `done`, `pause` (matching v1 except naming convention may
shift to snake_case in the args).

New in 9b: `fanout` closes the iter and requests N child runs. The JSON
payload is carried between `[[engteam:fanout-start]]` and
`[[engteam:fanout-end]]` markers; `[[engteam:fanout]]` is the closing
verb. See §5.4 for the full grammar.

The prompt-marker pair (`[[engteam:prompt-start]]` … `[[engteam:prompt-end]]`)
remains the mechanism for carrying the next-iter prompt before `handoff`
and `pause`. See `references/signaling.md` (TBD).

`pause-for-input` accepts an **optional** `review_path="<relative-path>"`
attribute (added 14b, ADR-40). The attribute **may repeat on the same
line** (14f, ADR-41) to declare multiple reviewable artifacts:
`review_path="a.md" review_path="b.md"`. The line-anchored `_PAUSE_RE`
is unchanged; the parser collects all matches via `re.finditer` and
stores them as `signal_args["review_paths"]: list[str]` on the paused
iter. The dashboard's `PauseAnswerForm` (14c/14f) reads it to switch to
inline-editor mode — single-path is a single-pane layout, multi-path
adds a tab bar with per-tab dirty state. Each path is **relative to
`$RELAY_RUN_DIR`**; absolute paths, `..` components, empty strings, or
NUL bytes are rejected at parse time with `MarkerError` naming the
offending value. Omitting the attribute is byte-identical to the
pre-14b grammar — skills emitting plain `pause-for-input` continue to
work unchanged. Iters paused under 14a–14d carry the legacy scalar
`signal_args["review_path"]` key; readers fall back to that key during
the migration window when the plural key is absent.

### 5.2 `mcp_tools` (alternative; not built in MVP)

A small in-process MCP server registers tools `relay__handoff`,
`relay__done`, `relay__pause`, `relay__phase_start`, `relay__unit_done`,
etc. When the agent invokes one of these (via `ToolUseStart` events
with `name.startswith("relay__")`), the orchestrator emits the
corresponding `SignalEmitted`.

On pi, this requires the `pi-mcp-adapter` community extension. Not built
in MVP per ADR-05's MVP recommendation, but the strategy hook is there.

### 5.3 Hybrid (future)

Nothing precludes using both: agent can use sentinels for the canonical
contract and MCP tools as a richer event channel (e.g., dashboard
annotations, audit logs). Out of scope for MVP.

### 5.4 Fanout sentinel (9b)

Closes the iter and requests parallel child runs. Full grammar:

    [[engteam:fanout-start]]
    {
      "children": [
        { "role": "<label>", "prompt": "<child prompt body>" },
        ...
      ],
      "join_prompt": "<prompt body for the synthesizer iter — used in 9c>"
    }
    [[engteam:fanout-end]]

    [[engteam:fanout]]

The JSON body between `fanout-start` and `fanout-end` must parse and
validate as a `FanoutPayload` (at least one child; `join_prompt`
present). The `[[engteam:fanout]]` verb line must follow after the end
marker (intervening blank lines allowed), at column 0 with no indentation.
A malformed body, missing markers, or a `join_prompt`-less payload is
treated as `agent_end_no_signal` and fails the run.

Depth is limited by `RELAY_MAX_FANOUT_DEPTH` (default 2, hard cap 4).
Concurrent child tasks are bounded by `RELAY_MAX_FANOUT_CONCURRENT`
(default 4, Option A semaphore — ADR-35).

## 6. Orchestrator

One async task per active Run, managed by an `asyncio.TaskGroup` in
FastAPI's lifespan. The task implements the chained-iter loop:

```python
async def run_loop(run: Run, core: RelayCore) -> None:
    seq = 0
    last_session_id: str | None = None
    while seq < run.max_iters:
        seq += 1
        iter_row = await core.start_iter(run, seq=seq)
        prompt = await core.build_prompt(run, iter_row)  # preamble + body
        session = await harness.spawn(
            prompt=prompt,
            cwd=run.worktree_path or run.project.root_path,
            env={},  # PI_AGENT_SDK is set inside PiHarness
            signal_config=SignalConfig(strategy="text_sentinels"),
            resume_from=last_session_id,  # None for fresh iters; chained sessions intentionally use fresh contexts
        )
        signal: SignalEmitted | None = None
        async for ev in session.events():
            await core.store_event(run, iter_row, ev)
            if isinstance(ev, AssistantText):
                signal = signaling.detect_in_text(ev.text, signal_config)
            elif isinstance(ev, ToolUseStart):
                signal = signaling.detect_in_tool(ev, signal_config)
            if signal:
                break
        await session.cancel() if signal else None
        result = await session.wait()
        await core.end_iter(iter_row, signal, result)
        if signal is None:
            # No closing sentinel — ambiguous exit (v1's exit-1 case).
            await core.fail_run(run, reason="agent_end_no_signal")
            return
        if signal.kind == "done":
            await core.end_run(run, status="done", summary=signal.args.get("summary"))
            return
        if signal.kind == "pause":
            await core.pause_run(run, question=signal.args["question"],
                                  next_prompt=signal.args["next_prompt"])
            return   # caller resumes via API
        # signal.kind == "handoff"
        run.next_prompt = signal.args["next_prompt"]  # for the next iter
        last_session_id = None  # intentionally fresh context per iter — see motivation.md
    await core.fail_run(run, reason="max_iters")
```

**Crucially:** `last_session_id` is intentionally `None` between iters.
Pi's resume preserves context; relay's value proposition is *fresh*
contexts per iter, with the lead engineer's compressed handoff carrying
state forward. Pi's session resume is reserved for crash recovery, not
inter-iter chaining.

**Subagent dispatch / fanout (9a–9f, shipped post-MVP).** The agent's
`fanout` sentinel (closing verb paired with a `[[engteam:fanout-start]]
… [[engteam:fanout-end]]` JSON marker block — §5.4) spawns N child
runs with `parent_run_id` set; the parent transitions
`running → awaiting_children` and waits. When every child reaches a
terminal status (`done` / `failed` / `cancelled`) the join watcher
emits one `subagent_return` per child + one `child_runs_resolved`,
transitions the parent back to `running`, and re-enqueues it with a
synthesizer prompt composed from `join_prompt` and a `RELAY_CHILD_
RESULTS:` trailer (9c). Concurrency is capped by an `asyncio.Semaphore
(max_fanout_concurrent)`; depth is bounded by `max_fanout_depth`
(ADR-35). Runtime cancel on an `awaiting_children` parent flips the
parent first then cascades to descendants (parent-first ordering,
ADR-37). On server restart, parents in `awaiting_children` are
treated as orphans: cancelled, with their children cascade-cancelled
(ADR-34) — recovering an in-flight fanout across a restart is a
deliberate V1 non-goal.

**Join (9c).** When all children of an `awaiting_children` parent reach
a terminal status (`done`, `failed`, or `cancelled`), the orchestrator:

1. Appends one `subagent_return` event per child on the parent's stream
   (`{child_run_id, status, summary}` — `summary` is the child's
   closing `run_ended` payload `summary`, empty string when absent).
2. Appends one `child_runs_resolved` event
   (`{children_count, terminal_statuses}` — `terminal_statuses` is a
   `dict[run_id, status]`).
3. Transitions the parent run `awaiting_children` → `running`.
4. Re-enqueues the parent with a synthesizer `RunContext`. The
   synthesizer iter's body is `join_prompt` (recovered from the closing
   fanout iter's `iters.signal_args["payload"]["join_prompt"]`) followed
   by a `---` separator and a YAML-ish `RELAY_CHILD_RESULTS:` trailer
   listing the per-child `id` / `role` / `status` / `summary` /
   `branch` / `worktree_path`. Multi-line summaries use YAML literal
   block (`summary: |`).

The trailer lives in the body, not the `RELAY_*` preamble (ADR-14 reserves
the preamble for `RELAY_RUN_DIR` and `RELAY_PHASE`). The synthesizer iter
runs on the **parent's existing worktree** (no new worktree provisioned —
the join is supposed to see the parent's pre-fanout state, not a sibling).
Recursive fanout from the synthesizer is permitted up to `max_fanout_depth`.

Partial-failure semantics: the synthesizer ALWAYS runs once all children
settle, regardless of how many failed or were cancelled. The orchestrator
does not auto-fail the parent on a child's failure — the agent decides via
the trailer (ADR-36).

A child-completion watcher (ADR-36) is an in-process direct call from the
child's `_run` task, lock-guarded by `RelayCore._enqueue_lock`. The watcher
is a no-op when the parent is not `awaiting_children` (already resumed by
a sibling, or cascade-cancelled).

### 6.x Iter close-time persistence (ADR-39)

Every iter close path (terminal signal, cancelled, timed-out,
no-signal, crash) appends a `harness_session_ended` event to the
events table **before** the paired `iter_ended` event. The payload
carries `SessionEnded.stop_reason`, `SessionEnded.messages` verbatim
(ADR-18 opaque-messages convention), and a `summary` populated only
on the `signal.kind == "done"` close path. This closes the latent
ADR-10 invariant gap parked since Phase 7: the OTel mirror sees
usage via the ADR-29 Option-D harness lookahead, but until ADR-39
the event store itself never received the close-time row.
Consumers that derive from the event log alone (SSE replay, future
analytics, audit) now have a complete record.

### 6.1 Runtime model (ADR-19, ADR-21)

The pseudocode above is the *behavioural* contract, illustrative not
literal: the production loop factors the event stream into `_drive_iter`
(stream + per-turn detection + timeout/cancel) and `_finish_iter` (close
the iter row + `iter_ended`), and the bound is
`max(max_iters, paused_seq+1)` not `max_iters` (ADR-22, §6.2).
`docs/orchestrator.md` documents the as-built structure (and defers to
this section on any disagreement).
`RelayCore` is the single shared service object — the loop, and (later)
REST routes and MCP tools, mutate state only through it (ADR-07/ADR-15).
It owns an `asyncio.Queue` of run-start requests and a long-lived
**supervisor** task that drains the queue, launching one tracked child
task per run. `RelayCore.start()` / `aclose()` bracket this and are
driven by FastAPI's `lifespan`. This is the open-ended-server form of
"`TaskGroup` in lifespan" (ADR-19): a literal `async with TaskGroup()`
cannot keep accepting work for the daemon's lifetime.

Concerns the pseudocode elides, all owned by the orchestrator:

- **Per-iter timeout.** `runs.iter_timeout` is enforced by the
  orchestrator (the harness has no internal timeout); a `try/finally`
  around the event stream guarantees the pi subprocess is terminated
  even if the run task is cancelled at shutdown.
- **Cancellation.** `cancel_run` has three branches (ADR-37):
  - **In-flight** (normal): set the run's `cancel_event` + cancel the
    harness session; the iter closes with `exit_reason="cancelled"`.
  - **`awaiting_children`** (9d): under `_enqueue_lock`, flip the
    parent to `cancelled` *first* (so the 9c join watcher cannot race
    a resume — both `cancel_run` and `_maybe_resume_parent` serialise
    on the same lock), then `_cascade_cancel_runtime` walks descendants
    depth-first: in-flight ones get a fire-and-forget
    `cancel_event` + `session.cancel()` (their own `_run.CancelledError`
    writes the `run_ended`); descendants with no in-memory `_RunState`
    (queued, or state lost) get DB-finalised in place.
  - **Orphan**: no in-memory state and DB row not terminal → finalise
    the row directly so the Cancel button is never a silent no-op
    (ADR-31 safety net).
  - `_run` carries a cancelled-before-start guard: a queued descendant
    whose row was DB-flipped by the cascade exits immediately, so no
    `iter_started` appears on an already-terminal run.
- **No usable closing signal.** A clean `agent_end` with no column-0
  closing sentinel (including a fenced/indented one — the matcher is
  line-anchored) *and* a marker-contract violation (`MarkerError`) both
  close the iter with `exit_reason="agent_end_no_signal"` and fail the
  run; this keeps `iters.exit_reason` within §3.1's set (the marker
  headline is preserved in `signal_args` / the `run_ended` summary).
- **DB access.** All orchestrator I/O uses an async (`aiosqlite`) engine
  encapsulated in `relay_v2.db` (ADR-21); the sync engine survives only
  for `create_all` schema bootstrap (ADR-17). The event log is the
  single source of truth (ADR-10): every status transition also appends
  an event; `runs.status` / `iters.*` are a projection updated in step.

### 6.2 Pause / resume (ADR-20)

`pause` closes the iter and the run with `status=paused` and persists
`{next_prompt, question, id}` in that iter's `iters.signal_args` (the
column §3.1 already reserves) plus a `pause_requested` event. No
dedicated column is added. `resume_run(answer)` reads the latest paused
iter's `signal_args`, composes the resumed iter's body as the saved
`next_prompt` + a delimited answer block, flips `runs.status` to
`running`, emits `pause_resolved`, restores the phase from
`$RELAY_RUN_DIR/phase`, and re-enqueues at the next `seq`. Fresh
context per iter still holds — the answer travels in the prompt, never
via pi session resume (`resume_from` stays `None`). The artifacts dir
(`RELAY_RUN_DIR`, §3.3) and a best-effort per-run git worktree (ADR-13;
degrades to the project root when it is not a git work tree, e.g.
fixture runs) are provisioned at `start_run`. The loop bound on resume
is `max(max_iters, paused_seq + 1)`, not `max_iters` — a resumed run is
guaranteed at least one post-answer iter even if it paused on its last
budgeted iter (ADR-22). For a fresh run (`start_seq == 0`) this is
exactly `max_iters`.

## 7. REST API surface

OpenAPI auto-generated. Routes:

```
# Runs ─────────────────────────────────────────────────────────────────
POST   /api/runs                  start a run        body: {project_id, prompt_body|prompt_id, max_iters?, iter_timeout?}
GET    /api/runs?project_id=N     list runs          query: status, limit, offset, include_children (default false — child runs hidden)
GET    /api/runs/:id              get run detail     includes iters[], current status
GET    /api/runs/:id/children     list direct child runs  returns list[Run] ordered by started_at
POST   /api/runs/:id/cancel       cancel a run
POST   /api/runs/:id/resume       resume a paused run  body: {answer}
GET    /api/runs/:id/events       paginated events for replay
GET    /api/runs/:id/preview      preview the rendered prompt + preamble that WOULD be sent
                                  (no side effects — used by the dashboard's "New Run" wizard)

# Live event stream ───────────────────────────────────────────────────
GET    /api/events/:run_id        SSE live stream    headers: Last-Event-ID for resume

# Projects ────────────────────────────────────────────────────────────
GET    /api/projects              list projects
POST   /api/projects              register a project body: {root_path, name}
GET    /api/projects/:id          get project detail
DELETE /api/projects/:id          unregister (does not delete files on disk)

# File browser (read-only, sandboxed) ─────────────────────────────────
GET    /api/projects/:id/files    list files         query: path=<relative> (default: project root)
                                                     returns: {entries: [{name, is_dir, size, modified}], path}
GET    /api/projects/:id/files/* get a file's content  text only; 415 for binary
                                                     path is the URL-encoded relative path

# Run artifacts browser (read-only listing/read; single write entry — ADR-25, ADR-40) ─
GET    /api/runs/:id/artifacts    list artifact files  query: path=<relative> (default: run artifacts root)
                                                     returns: {entries: [{name, is_dir, size, modified}], path}
GET    /api/runs/:id/artifacts/*  get an artifact's content  text only; 415 for binary
                                                     sandbox root = <project_root>/.relay/runs/<run_id>/ (spec §3.3),
                                                     reuses the §7 file-browser audited resolver (ADR-25)
PUT    /api/runs/:id/artifacts/*  write text content to a sandboxed artifact (pause-for-review, §6.2, ADR-40/ADR-41)
                                                     body: {content: str, editor?: str}
                                                     returns 200 {path, size, sha256}; 400 sandbox violation,
                                                     404 unknown run, 409 not paused / no review_path /
                                                     path mismatch / missing parent dir, 413 oversize,
                                                     415 binary or malformed body. Single write entry on the
                                                     artifacts dir; coupled to runs.status == 'paused' AND
                                                     set-membership in iters.signal_args.review_paths (14b/14f;
                                                     legacy scalar review_path read as a one-element list).
                                                     Every successful write appends one `artifact_edited` event
                                                     (§3.2) iter-scoped to the paused iter.

# Prompts ─────────────────────────────────────────────────────────────
GET    /api/prompts?project_id=N  list prompts (latest version of each)
GET    /api/prompts/:id           get a prompt (specific version)
POST   /api/prompts               create a prompt    body: {project_id, name, body}
PUT    /api/prompts/:id           update (bumps version, snapshots old version)
DELETE /api/prompts/:id           delete a prompt (and all versions)
GET    /api/prompts/:id/versions  list all versions of a prompt
```

The file browser is **read-only and sandboxed** to the project root.
Paths are normalized; `..` traversal is rejected with 400. Binary files
return 415 (frontend offers a download instead). Markdown and code are
returned verbatim — rendering happens client-side.

`PUT /api/runs/:id/artifacts/*` is the **single write entry point** on
the run artifacts dir (ADR-40/ADR-41). It is strictly coupled to
`runs.status == 'paused'` AND the requested path being a member of the
latest paused iter's `iters.signal_args.review_paths` (14f, plural —
the legacy scalar `review_path` is read as a one-element list during
the migration window). Writes are only permitted during a declared
pause review. The event store records every write as an
`artifact_edited` event (§3.2) with content hashes; the file content
itself lives on disk (the artifacts dir is the authoritative artifact
store per ADR-25, not the event store). Replay can verify *that* an
edit happened and the integrity hashes; the content at replay time may
differ if the file was edited again or removed out of band.

The `GET /api/events/:run_id` SSE feed is a passive post-commit tail of
the event store (ADR-10 — it never writes). Its exact replay/cutover/
close contract is **ADR-23**: subscribe-before-replay with a
`seq > max_replayed_seq` cutover filter (gap-free, duplicate-free across
`Last-Event-ID` reconnects); a finished run streams paginated history
then EOF and returns `204` only when it has no events at/after
`Last-Event-ID`; a bounded-queue close-on-full slow-consumer policy; and
the `X-Accel-Buffering: no` header for nginx. See ADR-23 for the full
rationale and rejected alternatives.

Versioning: URL-prefixed (`/api/v1/...` if/when breaking changes
arrive). MVP uses `/api/...` without explicit version.

## 8. MCP server surface

FastMCP server mounted at `/mcp`. Tools (all callable by external MCP
clients — Claude Desktop, Claude Code, etc.):

```
relay__list_runs(project_root?: str) -> list[Run]
relay__get_run(run_id: str) -> Run
relay__start_run(project_root: str, prompt: str, max_iters?: int) -> Run
relay__cancel_run(run_id: str) -> Run
relay__pause_response(run_id: str, answer: str) -> Run
relay__tail_events(run_id: str, since_seq?: int) -> AsyncIterator[Event]
relay__read_artifact(run_id: str, path: str) -> str
```

Implementation: each tool calls a `RelayCore` method directly. Same
service layer that backs the REST routes. No proxying.

Authentication: deferred to MVP+1 (single-user localhost). When multi-
user arrives, MCP tools authenticate via a bearer token in the
Streamable HTTP headers — same auth path as REST.

**Toolchain (ADR-27).** Implemented with the **bundled** official MCP
SDK (`mcp.server.fastmcp.FastMCP` + `mcp.streamable_http_app()`), not
the standalone `jlowin/fastmcp`. Pinned `mcp>=1.27.1,<2` — the `<2`
cap is load-bearing (v2 rearchitects the transport). The server is
mounted in `relay_v2.app`'s lifespan with `async with
mcp.session_manager.run():` wrapping the existing body (a sub-app
mount does not auto-run its lifespan). `relay__tail_events` is
implemented as a bounded snapshot of events after `since_seq` (a
caller-advanced cursor): an MCP tool returns a single value, so the
`AsyncIterator[Event]` signature above is realized as pull-paged
snapshots — same data as the SSE tail (ADR-23), polled rather than
pushed. See ADR-27 and `docs/mcp.md`.

## 9. Dashboard (Vue 3 + Pinia)

`frontend/` directory. Vite-built static bundle served by FastAPI in
prod; dev server proxies to FastAPI for hot reload.

The dashboard is the **primary user-facing control plane** (per
ADR-15) — not a read-only spectator. It is the default surface for
starting, inspecting, pausing, and cancelling runs, as well as browsing
artifacts, managing prompts, and registering projects.

### 9.1 MVP views

- **Hub view** (`/`): list of registered projects with active run
  indicators. Each project card shows the most recent run's status.
  Top-level actions: "Register project" (form), "New run on …" (jumps
  into the New Run wizard scoped to that project).
- **Project view** (`/projects/:id`):
  - **Runs pane** — list of runs (active + recent) with status badges
    (running / done / failed / paused). Click a run to enter its
    detail view. Child runs are hidden by default; a "Show child runs"
    toggle reveals them (9e).
  - **Prompts pane** — list of saved prompts for this project. CRUD
    actions: create, edit (bumps version), delete, view version
    history. Click a prompt to render it.
  - **Files pane** — file browser scoped to the project root. Tree on
    the left, rendered content on the right. Markdown rendered via
    `markdown-it`, mermaid via `mermaid.js`, code highlighted via
    `shiki`. Diffs rendered via `diff2html` when comparing two files.
  - **"New Run" button** — launches the New Run wizard.
- **New Run wizard** (`/projects/:id/new-run`):
  1. **Prompt selection** — pick an existing prompt from the project
     library, or write one inline (textarea with markdown preview).
  2. **Options** — `max_iters`, `iter_timeout`, model override (defaults
     from server config).
  3. **Preview** — `GET /api/runs/:id/preview` returns the rendered
     prompt with the preamble that *would be* prepended. The user reads
     it; nothing has happened yet. This is the "not scary" step.
  4. **Start** — `POST /api/runs`; the wizard redirects to the run
     detail view.
- **Run detail view** (`/runs/:id`):
  - Header: status, prompt name + version, started_at, iter count,
    current phase, action buttons (pause-response / cancel). When
    `parent_run_id != null` a **Parent chip** links back to the parent
    run's detail view.
  - **Timeline pane** — chronological event feed. Each event row is
    collapsible. Tool calls show args + result inline (highlighted).
    `signal_emit` events stand out (banner color, anchor link). Live
    updates via SSE.
  - **Iters pane** — list of iters with seq, phase, signal_kind. Click
    to filter the timeline.
  - **Children pane** — shown only when the run has `parent_run_id == null`
    and at least one child run exists (i.e. parent runs only). Each row
    shows `status · short-id · role · branch · summary`. The pane
    revalidates on `subagent_dispatch`, `subagent_return`, and
    `child_runs_resolved` events via `['runs','children',runId]` Colada
    invalidation triggered from the events store.
  - **Artifacts pane** — the run's `.relay/runs/<id>/` directory
    browsed inline via the `GET /api/runs/:id/artifacts[/*]` endpoints
    (ADR-25 — a second sandboxed root reusing the §7 audited resolver).
    The `improvement-plan.md`, `evaluation-report.md`, and any other
    markdown artifacts render with proper formatting. Diffs of edited
    files render via `diff2html`. This is where the user reviews "what
    did the agent actually do?"
  - **Worktree pane** — git status, changed files, ability to diff
    individual files (uses git CLI under the hood via the
    orchestrator). **MVP status:** degraded to a read-only
    `worktree_path` + `branch` view (from the run-detail response); the
    live git-status / per-file-diff endpoints are a deliberate post-MVP
    gap (Phase-4 scoping decision; ADR-25/ADR-26). The pane is built
    ready for the richer data.
  - **Pause action** (when status=paused) — the agent's question is
    shown rendered; the answer textarea supports markdown; submit
    → POST `/api/runs/:id/resume`. When the paused iter's
    `signal_args.review_paths` is non-empty (14b/14f), the answer form
    renders an inline review pane above the question/answer block:
    it fetches the active artifact via
    `GET /api/runs/:id/artifacts/{path}`, shows a textarea (left) and
    the existing markdown/shiki/mermaid preview pipeline (right), and
    exposes a Save button that fires
    `PUT /api/runs/:id/artifacts/{path}` (ADR-40/ADR-41). For a single
    review path the layout is one pane (no tab bar — byte-identical to
    14c). For multiple review paths (14f) a tab bar across the top
    renders one tab per path with independent per-tab dirty state;
    `*` flags an unsaved tab. One Save is in flight at a time and
    targets the active tab; the Resume button is disabled only while
    that Save is in flight. Unsaved changes on non-active tabs surface
    as a soft warning but **do not block Resume** (an abandoned tab
    must not strand the operator). The pane is absent (and the
    existing minimal form renders unchanged) when the paused iter does
    not declare any review path. Each save lands an
    `artifact_edited` event row in the timeline (path + pre/post
    sha256 short hashes + editor). 14e: the right pane of the review
    block carries a `[ Preview | Diff ]` toggle — Diff is disabled
    while the textarea is byte-equal to the loaded baseline and
    renders a unified diff of dirty-vs-loaded-baseline via the lazy
    `DiffRender` entry (no extra eager bundle weight); a successful
    Save updates the baseline so the Diff tab returns to disabled
    until the next edit. Clicking an `artifact_edited` row in the
    timeline navigates the artifacts pane to that file's *current*
    on-disk content (not a historical diff — ADR-40 §B1 deliberately
    does not preserve before-content; pre/post hashes remain row
    metadata).
  - **Cancel action** — always available while `status ∈ {running,
    awaiting_children}`. When `awaiting_children` with N children, the
    label reads "Cancel run and N children"; cancellation cascades
    through descendants (ADR-37, 9d).

### 9.2 State management

- `Pinia` stores per concern: `projects`, `runs`, `currentRun`,
  `events`, `prompts`, `files`, `worktree`.
- `Pinia Colada` for REST cache + automatic revalidation on SSE
  invalidation pushes.
- One `EventSource` subscription per open run detail view, with
  `Last-Event-ID` resume on reconnect.

### 9.3 Replay mode

When a run is no longer `running`, the SSE endpoint returns the
historical event list (paginated) and closes. The frontend renders the
same UI from the static event list. No special "replay mode" toggle —
the dashboard treats live and historical the same.

### 9.4 File browser rendering pipeline

- **Markdown** → `markdown-it` + plugins for tables, task lists, footnotes
- **Code blocks** → `shiki` for VS Code-quality syntax highlighting
  (TextMate grammars; lazily loaded per language)
- **Mermaid diagrams** in markdown code fences (`mermaid` lang) →
  `mermaid.js` renders to inline SVG
- **Plain text / unknown** → monospace preformatted block
- **Binary files** → "binary content (N bytes) — download" link
- **Diff** (when comparing two files) → `diff2html` side-by-side or
  unified view

The pipeline is client-side. The backend serves raw bytes. The same
`FileTree`/`FileViewer` + pipeline serves both the project Files pane
and the run Artifacts pane via one `BrowserSource` abstraction (one
tree/viewer, two data sources — mirrors ADR-25's single-sourced
backend). **Toolchain mandates** for this pipeline and the wider
frontend (vue-router v5; shiki core + JS engine + lazy grammars, never
the bundle; mermaid dynamic-import only; the Vite SSE dev-proxy; the
vitest-4 coverage reality; the diff2html call) are recorded in
**ADR-26**; `frontend/README.md` has the operational form.

## 10. Observability

Per ADR-10.

- **Event store** is the source of truth. Every observable action is an
  `events` row. Sub-second latency for live updates is a function of
  SQLite WAL fsync cadence (default ~1ms) — well below the
  human-perceptible threshold.
- **SSE feed** is a straight tail of the events table for a given
  `run_id`, polling every ~100ms (or driven by an in-process broadcast
  channel — implementation detail).
- **OpenTelemetry mirror.** The orchestrator emits OTel spans wrapping
  each iter and each tool call (configurable via `RELAY_OTEL_EXPORT=langfuse|none`).
  Spans carry `run_id`, `iter_seq`, GenAI semantic conventions where
  applicable (model, token counts on `tool_use_end` if pi surfaces them
  — TBD).
- **Langfuse** is the default export target when OTel is enabled. Self-
  hosted; relay ships a docker-compose example for the user's home
  server. Langfuse's prompt-management feature is unused in MVP but
  available for the future prompt-library feature.

> **Phase-7 implementation note (ADR-29).** The mirror lives in
> `src/relay_v2/observability/` and is wired into the orchestrator as an
> `Instrumentation` object passed by parameter (default: a literal
> no-op). Span tree: `relay.run` (opened/closed in `RelayCore._run`'s
> `try/finally`, so a crashed run still closes its span) → `relay.iter`
> (per `run_loop` iteration; attribute `relay.iter_seq` = the `iters`
> table `seq`, so a Langfuse trace lines up with the dashboard
> timeline) → `relay.tool_call` (per `ToolUseEnd` in `_drive_iter`,
> timed from event `ts`). GenAI attributes (`gen_ai.system`,
> `gen_ai.request.model`, `gen_ai.usage.input_tokens` /
> `output_tokens`; cache/cost under `relay.usage.*`) are set on the
> iter span from `SessionEnded.messages[].usage` (ADR-18 — the only
> token/cost source) **only when present**, never zero-filled.
> `RELAY_OTEL_EXPORT=none` constructs no provider/exporter and makes no
> network call. Langfuse OTLP target:
> `{RELAY_LANGFUSE_HOST}/api/public/otel/v1/traces`, HTTP Basic
> `base64("{public}:{secret}")`. Span-structure verification is
> automated (`tests/observability/`, `InMemorySpanExporter`, no
> network); the live-Langfuse-UI check is a manual journal-attested
> step. Operational ref: `docs/observability.md`.

> **Phase-9f cross-run parenting (ADR-38).** With fanout-join, the
> orchestrator runs more than one `relay.run` span per logical
> workflow: the parent's pre-fanout phase, each child, and the
> parent's synthesizer phase (the second `_run` invocation triggered
> by `_maybe_resume_parent`'s `_RunState` swap). 9f connects them
> into one trace tree: the dispatching iter's OTel `Context` is
> captured inside the closing iter's `with` block in `run_loop`,
> threaded as an opaque `IterSpanContext` through
> `LoopResult.fanout_parent_ctx` → `RelayCore._dispatch_children`
> → each child's `_RunState.parent_iter_ctx`, and consumed by
> `_run` calling `self._otel.run_span(ctx.run_id, parent_iter_ctx=…)`.
> Each child's `relay.run` therefore opens *under* the dispatching
> iter (same `trace_id`; `parent.span_id == dispatching_iter.span_id`).
> The synthesizer-phase `relay.run` is parented on the *same*
> dispatching iter alongside the children — the watcher preserves
> the carrier from the old `_RunState.result.fanout_parent_ctx`
> across the line-618 swap, so the join phase visually descends from
> the dispatch that triggered it. Recursive fanout preserves the
> same pattern at each level: a grandchild's `relay.run` chains back
> through two iter spans to the original parent. The connected tree
> reads as:
>
> ```
> relay.run            (parent, pre-fanout phase)
> ├── relay.iter (seq=1, normal)
> └── relay.iter (seq=2, fanout-closing)         ← dispatching iter
>     ├── relay.run (child A)
>     │   ├── relay.iter (child A seq=1)
>     │   └── relay.iter (child A seq=2, done)
>     ├── relay.run (child B)
>     │   └── relay.iter (child B seq=1, done)
>     └── relay.run (parent, synthesizer phase)  ← re-enqueued post-join, parented on the dispatching iter
>         └── relay.iter (seq=3, synthesizer)
> ```
>
> Therefore: `relay.run` is the trace root *except* when the run is
> a fanout child OR a synth-phase parent re-enqueue, in which case
> it is parented on the dispatching iter. The carrier lives in
> memory only (no `traceparent` column, no shared dict on
> `RelayCore`); span linkage is lost across a server restart — the
> 9a startup cascade (ADR-34, V1 non-goal of cross-restart fanout)
> finalises the descendant tree with `cancelled` + `run_ended`, and
> the post-restart Langfuse view shows the surviving rows as
> disconnected ERROR-status `relay.run` trees. The OTel mirror
> invariant (ADR-10) is unchanged: spans only mirror the event
> store, never act as a second source of truth — 9f reshapes
> *parentage*, not *content*. Rationale, rejected alternatives, and
> the restart caveat are recorded in ADR-38.

## 11. Configuration & deployment

### 11.1 Environment variables

| var | default | meaning |
|---|---|---|
| `RELAY_DATA_DIR` | `<cwd>/.relay` | server-global SQLite event store dir (relay.db lives here). Per-run worktrees & artifacts live under each project's own `<project_root>/.relay/` (§3.3), not here. |
| `RELAY_PI_BIN` | `pi` | pi binary path (override for testing) |
| `RELAY_PI_EXPECTED_VERSION` | `0.74.0` | pinned pi version (OQ-5). `PiHarness.spawn` warns when the installed pi reports a different version; relay does not refuse to run. |
| `RELAY_PI_MODEL` | `claude-opus-4-7` | default model |
| `RELAY_PI_PROVIDER` | `anthropic` | default provider |
| `RELAY_PI_STDOUT_LIMIT` | `8388608` (8 MiB) | asyncio `StreamReader` buffer size for pi's stdout. Pi emits one JSON object per line; a large tool result or `agent_end.messages` payload can exceed asyncio's 64 KiB default and crash the read with `LimitOverrunError`. Raise if pi emits even larger lines. |
| `RELAY_MAX_ITERS` | `12` | default per-run iter cap |
| `RELAY_ITER_TIMEOUT` | `1800` | per-iter wall-clock cap (seconds) |
| `RELAY_OTEL_EXPORT` | `none` | `langfuse` or `none` |
| `RELAY_LANGFUSE_HOST` | unset | required when OTel export is langfuse |
| `RELAY_LANGFUSE_PUBLIC_KEY` | unset | " |
| `RELAY_LANGFUSE_SECRET_KEY` | unset | " |
| `RELAY_HOST` | `127.0.0.1` | server bind address |
| `RELAY_PORT` | `7800` | server port |
| `RELAY_MAX_FANOUT_DEPTH` | `2` | maximum parent→child recursion depth (hard cap 4) |
| `RELAY_MAX_FANOUT_CONCURRENT` | `4` | concurrent child-run task semaphore pool size |

Pi-side env vars (passed through to subprocess):
- `PI_AGENT_SDK=1` — always set by `PiHarness` per ADR-09.

### 11.2 Packaging

- Backend: `pyproject.toml` with `uv`. Installed via `uv tool install`
  or `pipx`. Console script: `relay`.
- Frontend: `frontend/package.json`. Build to `frontend/dist/` and
  serve as static via FastAPI's `StaticFiles`.
- Container image: published to `ghcr.io/johnmathews/relay` via
  GitHub Actions, per the user's global Docker/CI policy.

> **Phase-8 implementation note (ADR-30).** The static-serving and
> packaging above are implemented. `relay_v2.api.static.mount_frontend`
> conditionally mounts the built SPA at `/` (a `StaticFiles` subclass
> with vue-router history-mode fallback), appended **after** the REST
> routers and `/mcp` in the app lifespan so it never shadows an API
> path, and a **no-op when `frontend/dist/` is absent** (dev/test) so
> the addition is provably additive. The multi-stage `Dockerfile`
> builds the SPA (Node stage) and runs the `uv`-synced backend from
> `/app/.venv/bin/relay` (Python stage); `docker-compose.example.yml`
> wires the `RELAY_*` surface and points at
> `docs/langfuse-compose.example.yml` for the (un-vendored) Langfuse
> stack. `.github/workflows/ci.yml` runs the full Python + frontend
> gate and publishes the image on push to `main`. The qualitative
> verification (real-pi e2e demo, "image pulls and runs", "MCP from
> Claude Code", live-Langfuse trace tree) is manual + journal-attested,
> gated like the `PI_INTEGRATION=1` e2e tests — see ADR-30.

### 11.3 Operational commands

```
relay serve                        # start the daemon
relay start <prompt-file|->        # start a run in the current project
relay status                       # show active runs
relay cancel <run_id>              # cancel
```

> **Accuracy note (Phase-8 review, ADR-30; updated 2026-05-25 for
> ADR-44).** This is the *target* command surface. As of the MVP, only
> `relay serve` and `relay --version` are implemented in
> `relay_v2.__main__`. `relay start` / `status` / `cancel` are a
> post-MVP CLI convenience — in the MVP, run create/list/cancel is
> done through the dashboard, the REST API (§7), or the MCP server
> (§8), which is the documented and tested path. The earlier
> `relay install-skill` subcommand was retired by ADR-44 (relay now
> injects the bundled engineering-team skill into pi via `--skill` at
> spawn time — no per-project install needed).

## 12. Engineering-team skill port

Per ADR-14. The skill lives at `skills/engineering-team/` inside
`relay-v2`. Differences from v1:

- **Preamble format** keeps `RELAY_PHASE` and `RELAY_RUN_DIR` lines.
  `RELAY_RUN_DIR` resolves to `<project_root>/.relay/runs/<run_id>/`
  per §3.3 — the canonical artifacts directory, **sibling of the
  worktree, never nested inside it**. This is the only correct
  location for `improvement-plan.md`, `evaluation-report.md`, and any
  other phase artifacts; the worktree contains the code workspace,
  not the artifacts.
- **Signaling** initially keeps the v1 sentinel grammar verbatim
  (`text_sentinels` strategy). Schema revisions deferred — easier to
  port unchanged then evolve.
- **Phase docs** structure preserved.
- **Subagent dispatch** — in v1 the lead engineer invokes the Task
  tool. In v2 (with pi), subagents are emitted as a signal that the
  orchestrator catches and spawns a child run for. The fanout-join
  arc (phases 9a–9g, ADRs 34–39) shipped this: the closing sentinel
  `[[engteam:fanout]]` paired with a `[[engteam:fanout-start]] … [[engteam:fanout-end]]`
  JSON marker block drives `RelayCore._dispatch_children` →
  per-child runs joined under the parent via `parent_run_id` →
  synthesizer iter resumed with a `RELAY_CHILD_RESULTS:` trailer.
  See `docs/fanout.md` for the operational reference.

**Phase-6 implementation note (ADR-28, delivery model superseded by
ADR-44).** The skill is built at `skills/engineering-team/` (repo
root, outside the `src/relay_v2` wheel; a hatch `force-include` maps
it into built wheels as `relay_v2/skills/`). As of ADR-44 (2026-05-25)
the skill is **injected directly into pi via `--skill <bundled-path>`**
on every spawn — `Settings.pi_skill_paths` resolves to the bundled
tree by default, `PiHarness._build_argv` appends one `--skill` pair
per configured path, and the earlier `relay install-skill` command
(which had been writing to `.claude/skills/` — a Claude Code
discovery root pi never reads) was deleted outright. Six deliberate
port adaptations were applied (and are locked by
`tests/skills/test_skill_structure.py`): (1) the v1 Task-tool subagent
roles became single-session *analysis lenses* in the initial Phase-6
port; the fanout-join arc (9a–9g, ADRs 34–39) subsequently added a
`subagent_dispatch` handler at the orchestrator layer
(`RelayCore._dispatch_children`) and the Phase-2 template now emits
`[[engteam:fanout]]` to drive it — see `docs/fanout.md`;
(2) every artifact path moved to `$RELAY_RUN_DIR` =
`.relay/runs/<run_id>/`; (3) Phase 3 no longer creates a worktree —
relay's `provision_workspace` does (ADR-13: "no Phase-3-skill
responsibility"); the `current.txt` mirror is gone; (4) Phase 4
inlines the quality gate (lint + types + tests + security review +
journal) and the FF-merge because the pi harness has no `/done` or
`/merge-push` Claude Code slash-skill; (5) sentinel source-doc
pointers repoint to this spec; (6) worked-example commands use
relay-v2's `uv run` gate. The sentinel **grammar** is unchanged from
v1 (this section's mandate). Behavioral acceptance (a multi-iter pi
run against the v1 `eng-team-demo` fixture) is a documented manual
step, gated like the other `PI_INTEGRATION=1` e2e checks — see
`docs/skills.md` and ADR-28; automated coverage is the CLI +
structural-invariant suites.

## 13. Open questions

Carried from `motivation.md` risks; resolved as design progresses.

- **OQ-1.** *Resolved (2026-05-19, ADR-18).* `agent_end` payload is
  `{type, messages}`; `messages` is the full compiled conversation —
  a flat list of message dicts, roles `user`/`assistant`/`toolResult`,
  assistant messages carrying `content` blocks plus `usage`
  (`input`/`output`/`cacheRead`/`cacheWrite`/`totalTokens`/`cost`),
  `stopReason`, `model`, `responseId`. Sufficient for the final per-run
  summary. The harness passes `messages` through verbatim in
  `SessionEnded.messages` and never interprets it.
- **OQ-2.** *Resolved (2026-05-19, ADR-18).* Streamed text arrives as
  `message_update.assistantMessageEvent` with per-block framing
  (`*_start`/`*_delta`/`*_end`). Concatenated `text_delta`s equal the
  block's `text_end.content` in every captured fixture, so the deltas
  are authoritative. The harness accumulates `text_delta` per turn and
  flushes one `AssistantText` at `turn_end`; sentinel detection runs at
  that turn boundary — no need to wait for `message_end`.
- **OQ-3.** *Partially answered (2026-05-19, ADR-18).* pi **does**
  surface usage: each assistant message in `agent_end.messages` (and in
  `message_end`) carries `usage` with `input`/`output`/`cacheRead`/
  `cacheWrite`/`totalTokens` and a `cost` sub-object (`cost.total` in
  USD). Open part: per-iter aggregation strategy for the OTel/Langfuse
  export (Phase 7) — not consumed in Phase 1.
- **OQ-4.** *Resolved (2026-05-19, W7; path corrected 2026-05-23
  post-9g bug-fix sweep).* `provision_workspace`
  (`orchestrator/lifecycle.py`, ADR-13) creates a best-effort per-run
  git worktree at `<project_root>/.relay/worktrees/<run_id>` on branch
  `relay/<run_id>`, degrading to the project root when the root is not
  a git work tree (e.g. fixture runs). The v1 per-run-branch pattern
  ports; the worktree is **per-project** (under the project's
  `.relay/` dir, the same place the artifacts live — spec §3.3),
  *not* under the relay-global `data_dir` (the pre-9g layout was a
  spec §3.3 violation; corrected in the post-9g bug-fix sweep — see
  CLAUDE.md "Bug 2"). `data_dir` now holds only the multi-tenant
  `relay.db`. Both the success and fallback branches are covered by
  `tests/orchestrator/test_lifecycle.py`.
- **OQ-5.** *Resolved (2026-05-19, W4 / ADR pre-phase).* Pi is pinned to
  **0.74.0** via a committed `.tool-versions` file (the human-facing
  pin) plus `RELAY_PI_EXPECTED_VERSION` (`Settings.pi_expected_version`,
  default `0.74.0`). `PiHarness` runs a best-effort `pi --version` probe
  once on first spawn and logs a non-fatal warning on mismatch
  (`pi_version_mismatch_warning`). The pin is intentionally below
  upstream; bumping it is a deliberate maintenance task (re-run the
  de-risking fixtures, then change `.tool-versions` + the default).
- **OQ-6.** Pi auth.json refresh — does relay need to monitor
  expiration, or does pi handle silently?

---
