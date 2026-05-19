# Observability — OTel mirror + Langfuse (Phase 7)

Operational reference for relay-v2's OpenTelemetry mirror. Design
contract: `docs/spec.md §10`; rationale and rejected alternatives:
**ADR-29** (and ADR-10, which makes the event store the *single source
of truth* — OTel only mirrors it). This doc is the how-to.

## What it is

The orchestrator emits an OpenTelemetry span tree that **mirrors** the
append-only event store. It is a bolt-on, opt-in export — never a
second source of truth, never load-bearing. When it is off there is
provably zero overhead and zero network.

```
relay.run                       (one per run; RelayCore._run try/finally)
└── relay.iter                  (one per run_loop iteration)
    ├── relay.tool_call          (one per ToolUseEnd in _drive_iter)
    └── relay.tool_call
```

| Span | Opened/closed | Key attributes |
|---|---|---|
| `relay.run` | `RelayCore._run()` `try/…/finally` — a crashed or cancelled run still closes its span (status `ERROR`) | `relay.run_id` |
| `relay.iter` | per `run_loop` while-iteration | `relay.run_id`, `relay.iter_seq` (= the `iters` table `seq`), `relay.phase`, `relay.exit_reason`, GenAI/usage (below) |
| `relay.tool_call` | `_drive_iter` on `ToolUseEnd`, timed from event `ts` | `relay.tool_id`, `relay.tool_name`, `relay.tool_is_error`, `relay.tool_duration_ms` |

`relay.iter_seq` is the same integer the dashboard timeline shows, so a
Langfuse trace and the dashboard line up one-to-one (spec.md §9).

### GenAI / usage attributes

Set on the **iter** span, aggregated across the assistant messages in
`SessionEnded.messages` (ADR-18 — pi's `agent_end` payload is the only
token/cost source). **Each attribute is set only when pi surfaced it;
absent fields are omitted, never zero-filled.**

| Attribute | Source (`messages[].usage` / message) |
|---|---|
| `gen_ai.system` | message `provider` (e.g. `anthropic`) |
| `gen_ai.request.model` | message `model` (e.g. `claude-sonnet-4-6`) |
| `gen_ai.usage.input_tokens` | Σ `usage.input` |
| `gen_ai.usage.output_tokens` | Σ `usage.output` |
| `relay.usage.cache_read_tokens` | Σ `usage.cacheRead` |
| `relay.usage.cache_write_tokens` | Σ `usage.cacheWrite` |
| `relay.usage.total_tokens` | Σ `usage.totalTokens` |
| `relay.usage.cost_usd` | Σ `usage.cost.total` |

`gen_ai.*` keys are written as stable string literals; relay does **not**
depend on the (unstable/incubating) `opentelemetry-semantic-conventions`
GenAI module. Cache/cost have no stable GenAI key, hence `relay.usage.*`.

> **Why usage is available on the normal close path (Option D, ADR-29).**
> pi emits `…turn_end, agent_end`; the orchestrator detects the
> terminal sentinel in the `turn_end` text and stops the iter before
> `agent_end` (the only carrier of `messages[].usage`) would normally
> be read. The pi harness therefore holds the most recent
> `AssistantText` by one event so `agent_end` is consumed — and the
> usage `messages` captured — *before* the sentinel text reaches the
> orchestrator. This is harness-internal and order-preserving: the
> event store, SSE, and MCP surfaces are unchanged. On a genuine
> crash/timeout (no `agent_end`) there is simply no usage to record and
> the attributes are omitted (never zero-filled).

## Configuration

All via `RELAY_*` env vars (spec.md §11; `Settings` already carries the
fields — Phase 7 added no config surface):

| var | default | meaning |
|---|---|---|
| `RELAY_OTEL_EXPORT` | `none` | `langfuse` enables the mirror; `none` is a strict no-op |
| `RELAY_LANGFUSE_HOST` | unset | Langfuse base URL, e.g. `http://localhost:3000` (required when export = `langfuse`) |
| `RELAY_LANGFUSE_PUBLIC_KEY` | unset | Langfuse project public key (`pk-…`) |
| `RELAY_LANGFUSE_SECRET_KEY` | unset | Langfuse project secret key (`sk-…`) |

`RELAY_OTEL_EXPORT=langfuse` with any of the three Langfuse vars missing
is a startup `ValueError` (fail fast, not silent no-export).

### Langfuse OTLP contract

- **Endpoint:** `{RELAY_LANGFUSE_HOST}/api/public/otel/v1/traces` (the
  traces-signal path; the exporter is given the full URL — there is no
  automatic `/v1/traces` append when constructing `OTLPSpanExporter`
  programmatically).
- **Auth:** HTTP Basic — `Authorization: Basic <b64>` where `<b64>` is
  `base64("{public_key}:{secret_key}")`.

Sourced from the current Langfuse self-hosted OpenTelemetry docs
(<https://langfuse.com/integrations/native/opentelemetry>), not guessed.
Spans are shipped by a `BatchSpanProcessor`, which **swallows export
failures** — an unreachable or misconfigured Langfuse drops spans, it
never fails a run (ADR-10: Langfuse is not load-bearing).

## The no-op guarantee

With `RELAY_OTEL_EXPORT=none`, `build_instrumentation()` returns a
literal no-op: **no** `TracerProvider`, **no** `OTLPSpanExporter`, no
global OTel state, no network. The OTel SDK import is paid only on the
langfuse path. This is asserted in CI (the test monkeypatches the
exporter to raise if constructed). The langfuse path builds its **own**
`TracerProvider` (never the process-global one) so embedding relay
never commandeers a host app's OTel setup and the test suite stays
isolated.

## Self-hosting Langfuse

A minimal compose snippet is at `docs/langfuse-compose.example.yml`.
Bring it up, create a project, copy its public/secret keys into the
`RELAY_LANGFUSE_*` vars, set `RELAY_OTEL_EXPORT=langfuse`, restart
`relay serve`.

## Verification

**Automated (CI, offline).** `tests/observability/test_otel_export.py`
drives the real `RelayCore` + `run_loop` against a scripted harness
with an `InMemorySpanExporter` and asserts the span tree, parent/child
links, `relay.iter_seq` correlation, the GenAI/usage attributes
(including the absent-→-omitted case), and the no-op guarantees. No
network.

**Manual acceptance (journal-attested, like the pi e2e checks — ADR-29,
cf. ADR-28 §3).** The "real run → trace tree in the Langfuse UI" check
needs real pi + a live Langfuse and is qualitative:

1. Start Langfuse (`docs/langfuse-compose.example.yml`), create a
   project, note its keys.
2. Export `RELAY_OTEL_EXPORT=langfuse`, `RELAY_LANGFUSE_HOST`,
   `RELAY_LANGFUSE_PUBLIC_KEY`, `RELAY_LANGFUSE_SECRET_KEY`; set
   `PI_AGENT_SDK=1`.
3. `relay serve`, register a project, start a small multi-iter run.
4. In the Langfuse UI confirm: one trace per run; `relay.iter` children
   nested under `relay.run`; `relay.tool_call` nested under the right
   iter; iter `seq` numbers match the dashboard timeline; token/cost
   attributes present on iters where pi surfaced usage.

Record the result (date, pi version, Langfuse version, screenshot or
trace ID) in a journal entry — this is the honest verification; CI does
not assert "nests correctly in the UI".
