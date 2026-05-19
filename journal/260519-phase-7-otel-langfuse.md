# Phase 7 — OTel mirror + Langfuse export

**Date:** 2026-05-19
**Branch:** `worktree-eng-phase-7-otel` (engineering-team cycle, FF-merged to `main`)

## What shipped

An opt-in OpenTelemetry mirror of the event store
(`src/relay_v2/observability/`): a `relay.run` → `relay.iter` →
`relay.tool_call` span tree exported OTLP/HTTP to self-hosted Langfuse
when `RELAY_OTEL_EXPORT=langfuse`, and a strict literal no-op (no
provider, no exporter, no network) when `none`. The event store remains
the single source of truth (ADR-10) — OTel only mirrors it, never
writes to it. Backend-only, additive.

Files: `src/relay_v2/observability/{__init__,otel}.py`; wired into
`core.py` (run span in `_run`'s try/finally) and `loop.py` /
`_drive_iter` (iter + tool spans, by defaulted parameter); a one-event
lookahead added to `harness/pi.py` `PiSession.events()` (Option D);
`tests/observability/test_otel_export.py` +
`tests/harness/test_pi_session_lookahead.py`; `docs/observability.md` +
`docs/langfuse-compose.example.yml`; ADR-29 + a `spec §10`
implementation note; `pyproject.toml`/`uv.lock` add the three
`opentelemetry-*` deps.

## Key decisions (ADR-29)

1. **No-op is a deferred literal no-op, not an SDK object.** `none` →
   `NoopInstrumentation` (the OTel SDK import and any exporter/provider
   are never constructed; no global OTel state). The langfuse path
   builds its **own non-global** `TracerProvider` so embedding relay
   never commandeers a host app's OTel and the test suite stays
   isolated. Asserted directly (a test monkeypatches `OTLPSpanExporter`
   to raise if constructed).
2. **Span placement.** `relay.run` lives in `RelayCore._run`'s
   try/finally (not `start_run`, which only enqueues) so a crashed/
   cancelled run still closes its span. `relay.iter` carries
   `relay.iter_seq == iters.seq` so a Langfuse trace lines up with the
   dashboard timeline. `relay.tool_call` is timed from pi event `ts`.
3. **Pins `opentelemetry-*>=1.27,<2`.** The `<2` is *precautionary*
   (OTel 2.0 does not exist yet) — recorded honestly as such, not
   overclaimed as load-bearing à la ADR-27's `mcp<2`. No
   `opentelemetry-semantic-conventions` dep (unstable GenAI module);
   the four `gen_ai.*` keys are stable string literals.
4. **Langfuse OTLP contract researched, not guessed.** Endpoint
   `{host}/api/public/otel/v1/traces`, HTTP Basic
   `base64("{public}:{secret}")` — from the current Langfuse
   self-hosted OTel docs.
5. **Verification split (mirrors ADR-28 §3).** Span structure +
   Option-D guarantee are automated and offline (`InMemorySpanExporter`
   / a fake subprocess); the "real run → trace tree in the Langfuse UI"
   check is a documented, journal-attested manual step (needs real pi +
   live Langfuse, qualitative).

## Issue discovered during development (not in the plan) — Option D

The plan/ADR-18 premise "`SessionEnded.messages` is available at the
integration point" is **false on the normal close path**. pi emits
`…turn_end, agent_end`; the sentinel-bearing text flushes at
`turn_end`, the orchestrator detects the terminal sentinel there and
`break`s `_drive_iter`, so `agent_end` — the only carrier of
`messages[].usage` — is never consumed and `wait()` synthesises an
empty `SessionEnded`. Per-iter token/cost would be absent on every
`done`/`handoff`/`pause` iter (the common case).

This was surfaced to the user rather than silently picked, since every
fix touched a constraint the Phase-7 scope fenced off. Options weighed:
A (best-effort, usage almost always absent), B (post-hoc pipe drain in
`wait()` — racy, CI-untestable), C (drain-in-loop — changes loop
control flow + event-store contents). The user proposed and chose **D**:
a one-event `AssistantText` lookahead in `PiSession.events()`. The
harness holds the most recent `AssistantText` and delivers it
immediately before the *next* mapper output; when that next raw line is
`agent_end` the harness consumes it and sets `_final` (real `messages`)
**before** the held sentinel text reaches the orchestrator. Properties:
deterministic (`agent_end` consumed in-stream, not raced — unlike B);
external event order unchanged; the orchestrator still breaks before
`SessionEnded`, so no `agent_end` row is added to the event store —
ADR-10 + loop control flow byte-for-byte intact (unlike C); harness-
only (ADR-04). D dominates B and is far cheaper than C.

**Known separate follow-up (out of Phase-7 scope):**
`agent_end`/`SessionEnded` is still never persisted as an `events` row
on the sentinel-close path — a pre-existing latent ADR-10 completeness
gap that D neither widens nor closes. Closing it is C's territory and
deserves its own ADR + `spec.md §6` change.

## Process note

Two file-path slips early in the cycle wrote edits into the main
checkout instead of the worktree; caught via `git status` on both
trees, relocated via `git diff | git apply` + file copies, and the main
checkout restored to clean `ae6690e`. No work lost; a reminder that
file-tool paths must be worktree-absolute even when the shell cwd is
the worktree.

## Test coverage

`uv run pytest`: **192 passed**, 3 skipped (pi-e2e, `PI_INTEGRATION=1`,
unchanged) — +9 vs the 183 baseline (6 observability + 3 Option-D
harness). `ruff` clean; `mypy --strict` clean across 37 source files
(35→37). Backend coverage 91% → 93%. No regressions:
event-store/REST/SSE/MCP contracts unchanged; `RELAY_OTEL_EXPORT=none`
behavior identical to pre-Phase-7.

## Manual acceptance (pending, journal-attested per ADR-29)

The live "real run → Langfuse trace tree" check (real pi + a running
Langfuse) is the documented manual step in `docs/observability.md`. It
was **not** run in this cycle (no live Langfuse in the build
environment); to be attested in a follow-up journal note when run,
consistent with how pi e2e is treated project-wide (ADR-24).

## Follow-up items

- Run the manual Langfuse-UI acceptance and attest it (date, pi
  version, Langfuse version, trace ID).
- Consider a future phase to persist `agent_end`/`SessionEnded` as an
  event (the latent ADR-10 completeness gap noted under Option D).
