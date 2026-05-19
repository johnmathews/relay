# Harness layer

> Phase 1 deliverable. Implementation reference for `src/relay_v2/harness/`.
> Canonical design is `spec.md` §4 (protocol/events) and §5 (signaling);
> ADR-04, ADR-05, ADR-16, ADR-18 carry the rationale. This doc is the
> operational "how it fits together" companion — when it disagrees with
> `spec.md`, `spec.md` wins.

## What it is

The harness layer is the **only** code that knows pi exists (ADR-04).
Everything above it consumes normalized events and two protocols. Swapping
pi for another harness later means writing one new module, not touching
the orchestrator.

```
src/relay_v2/harness/
├── protocol.py            Harness / HarnessSession protocols,
│                          HarnessEvent hierarchy, SignalConfig, SignalEmitted
├── pi.py                  PiHarness, PiSession, map_pi_events  (pi JSONL → events)
└── signaling/
    ├── config.py          SignalConfig re-export
    ├── sentinels.py       text_sentinels strategy (v1 parser port)
    └── mcp_tools.py       mcp_tools strategy — MVP stub (raises NotImplementedError)
```

## The contract (spec.md §4.1)

`Harness.spawn(prompt, cwd, env, signal_config, resume_from=None)
→ HarnessSession`. A `HarnessSession` exposes `session_id`, an async
`events()` iterator of `HarnessEvent`, `cancel()`, and `wait() →
SessionEnded`.

Normalized events: `SessionStarted`, `AssistantText` (with
`kind ∈ {"text", "thinking"}`), `ToolUseStart` / `ToolUseUpdate` /
`ToolUseEnd`, `SessionEnded` (`stop_reason ∈
{"clean", "crash", "timeout", "cancelled"}`). Every event carries a
monotonic `seq` and a `ts`.

## pi mapping (spec.md §4.2, ADR-18)

Grounded in the committed de-risking fixtures, not assumptions:

- `session` → `SessionStarted`.
- `message_update`/`text_delta` → accumulated per turn → one
  `AssistantText(kind="text")` flushed at `turn_end` (OQ-2).
- `message_update`/`thinking_delta` → accumulated → `AssistantText(kind="thinking")`.
- `tool_execution_start|update|end` → `ToolUseStart|Update|End`
  (`duration_ms` is timed by relay between the start/end events — pi does
  not report it).
- `agent_end` → `SessionEnded(messages, stop_reason="clean")`; `messages`
  is passed through verbatim and never interpreted (OQ-1).
- All other event/sub-types — `*_start`/`*_end` framing, `toolcall_*`,
  `agent_start`/`turn_start`/`message_*`/`turn_end`, and **any
  unrecognised type** — are consumed internally. Unknown-type tolerance
  is deliberate: pi releases weekly (pinned to v0.74.0).

No `agent_end` in the stream (crash / timeout / cancel) → `events()`
ends without a `SessionEnded`; `PiSession.wait()` synthesizes the
terminal event with the appropriate non-`clean` `stop_reason`.

### Invocation

`PI_AGENT_SDK=1 pi -p <prompt> --mode json --provider <p> --model <m>
[--session <id>]` (ADR-16; `--session` is crash-recovery only — relay's
value proposition is a *fresh* context per iter, so `resume_from` is
normally `None`). Spawned via `asyncio.create_subprocess_exec` (argv
list, no shell).

## Signaling (spec.md §5, ADR-05)

`SignalConfig.strategy` selects the detector; the orchestrator gets a
normalized `SignalEmitted(kind, args)` either way.

- **`text_sentinels`** (MVP) — `detect_in_text(text, config)` is run on
  each turn's accumulated `AssistantText` **where `kind == "text"`
  only** (ADR-18 anti-mention). It returns the terminal signal
  (`done` / `handoff` / `pause`) when present, else the first
  non-closing one (`phase_start` / `unit_*`). The prompt-marker
  extraction and the v1 grammar (including exact error/repair strings)
  are ported verbatim; `MarkerError` carries the headline + repair
  recipe. v1's 30 synthetic fixtures are ported to
  `tests/harness/test_signaling_sentinels.py`.
- **`mcp_tools`** — stub. Selecting it raises `NotImplementedError`
  (needs the `pi-mcp-adapter` extension; post-MVP).

## Testing

- Unit tests run **fully offline** against
  `scratch/pi_derisk_workdir/*.jsonl` (committed ground truth):
  `tests/harness/test_protocol.py`, `test_pi_event_mapping.py`,
  `test_signaling_sentinels.py`, `test_signaling_mcp_stub.py`.
- `tests/harness/test_pi_integration.py` invokes a real pi and is
  **skipped unless `PI_INTEGRATION=1`**. pi is never spawned by the
  default suite.

Run: `uv run pytest`; gates `uv run ruff check .` and `uv run mypy`
(strict) must stay clean.
