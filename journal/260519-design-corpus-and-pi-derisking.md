# 2026-05-19 — Design corpus and pi de-risking

First day of v2. The work today established the design foundation before any
implementation begins.

## Why v2

v1 (bash driver + Flask dashboard + sentinels + `claude -p`) works but is
constrained by its implementation. The pains documented in `motivation.md`
are mostly structural — the dashboard's UI ceiling, the absence of a
programmable surface, the four-places-encode-the-same-rules invariant on
the sentinel contract, the absence of structured subagent observability.
None are fixable with incremental refactor. A clean-break rewrite in a new
repo (`relay-v2`, taking over the canonical `relay` GitHub name) became the
decision.

## How v2 differs

- **Python end-to-end**, no bash in the orchestration loop
- **pi harness** (`PI_AGENT_SDK=1 pi --mode json`) instead of `claude -p`
  — primary reason: pi has no 30-second tool timeout, which the Claude
  Agent SDK enforces and which is fatal for the engineering-team skill's
  long test suites
- **FastAPI + Vue 3 + Pinia** dashboard as the *primary control plane*,
  not just an observability surface (ADR-15) — the CLI becomes secondary
- **Owned SQLite event log** as source of truth, with optional OTel export
  to Langfuse (self-hosted)
- **REST + MCP + SSE** all behind one shared `RelayCore` service layer
- **Harness abstraction** designed for swappability (claude/other as
  secondary later), pi-first implementation
- **Sentinels survive** as the MVP signaling strategy (`text_sentinels` per
  ADR-05); MCP tools available as an alternative when needed

## What was decided (16 ADRs)

The full set is in `docs/decisions.md`. The load-bearing calls:

- ADR-01 — clean-break rewrite, new repo
- ADR-03 + ADR-16 — pi as primary harness, `--mode json` for MVP
- ADR-04 — `Harness` protocol with pi-first implementation
- ADR-05 — signaling-as-strategy (text_sentinels vs mcp_tools)
- ADR-06 — subagents managed at orchestrator layer, not harness
- ADR-09 — Max-subscription via `PI_AGENT_SDK=1` (provisional; pi's
  documented per-token billing for OAuth contradicts what's been
  observed, needs verification before sustained use)
- ADR-12 — single-user, localhost MVP; multi-user explicitly deferred
- ADR-15 — dashboard is the primary control plane (motivation goal 3)

## Pi de-risking — empirical evidence

A 5-test harness (`scratch/pi_derisk.py`) was written and run against
pi v0.74.0. Four passed; one "failed" due to my own harness wall-clock
timeout rather than any pi behavior. The architectural assumptions all
hold:

- ✅ `PI_AGENT_SDK=1` auth works against the Max subscription
- ✅ **No 30-second tool timeout** — a 70-second `sleep` Bash ran to
  completion; `DONE_AFTER_70S` appears in the event stream
- ✅ 11 pi event types documented (`session`, `agent_start/end`,
  `turn_start/end`, `message_start/update/end`, `tool_execution_start/
  update/end`); the `pi event → relay HarnessEvent` mapping in
  `spec.md` §4.2 is grounded in the real shapes, not speculation
- ✅ `--continue` resumes sessions correctly (recall of "ALPHA" across
  invocations confirmed)
- ✅ No `parent_tool_use_id` in pi's stream — confirms ADR-06's
  decision to manage subagents at the orchestrator layer

## What's next

- Phase 0 from `plan.md` — scaffold: `pyproject.toml`, FastAPI skeleton,
  SQLite schema, `relay serve` returning `/health`. Estimated 2 days.

## Other notes

- v1's local checkout had its `origin` remote still pointing at
  `github.com/johnmathews/relay.git`, which now resolves to v2 (since
  v1's GitHub repo was renamed to `johnmathews/relay-v1`). Fixed in v1's
  checkout by `git remote set-url origin https://github.com/johnmathews/relay-v1.git`.
- License: MIT, matches pi itself.
- The pi v0.74.0 pin is intentional — that's the version exercised by
  the de-risking suite. Upstream is at v0.75.3 at the time of writing.
