# 2026-05-19 — Phase 1: harness layer

Built the Phase 1 harness layer per `docs/plan.md`: the
`Harness`/`HarnessSession`/`HarnessEvent` protocol (spec §4.1), the
`PiHarness`/`PiSession` implementation + pi→event mapping (spec §4.2),
the `text_sentinels` signaling strategy (ported from v1), and the
`mcp_tools` stub. All work on `main` (clean), single commit.

## Open questions resolved (from fixtures, not assumptions)

**OQ-1 — `agent_end.messages` shape.** `agent_end` is `{type, messages}`.
`messages` is the full compiled conversation: a flat list of message
dicts (roles `user`/`assistant`/`toolResult`). Assistant messages carry
`content` blocks (`thinking`/`toolCall`/`text`) plus `usage`
(`input`/`output`/`cacheRead`/`cacheWrite`/`totalTokens`/`cost`),
`stopReason`, `model`, `responseId`. Sufficient for the final per-run
summary. **Decision:** the harness passes `messages` through verbatim in
`SessionEnded.messages` and never interprets it. spec §13 OQ-1 marked
resolved.

**OQ-2 — delta accumulation vs `message_end`.** Streamed text arrives as
`message_update.assistantMessageEvent` with per-content-block framing:
`{kind}_start` → `{kind}_delta`* → `{kind}_end` for kinds `text`,
`thinking`, `toolcall`. Concatenated `text_delta`s exactly equal the
block's `text_end.content` in every captured stream — deltas are
authoritative. **Decision:** accumulate `text_delta` per turn, flush one
`AssistantText` at `turn_end`; run sentinel detection at that turn
boundary (no need to wait for `message_end`). spec §13 OQ-2 resolved.

**OQ-3 — partially answered.** pi *does* surface token + cost: every
assistant message's `usage` includes a `cost` sub-object (`cost.total`
in USD). Recorded for Phase 7 (OTel/Langfuse); not consumed in Phase 1.

## New decision: ADR-18

The fixtures showed pi emits `thinking_*` and `toolcall_*` sub-types not
in spec §4.2's original table. A naïve "pass everything through as
`AssistantText`" (plan.md's stated mitigation) would feed
chain-of-thought into the sentinel parser and fire false signals.
ADR-18 (appended; decisions.md is append-only) decides:

- `AssistantText` gains `kind: Literal["text", "thinking"]` (default
  `"text"`, keeps spec §4.1's 2-arg constructor working).
- `text_delta` → `kind="text"`; `thinking_delta` → `kind="thinking"`;
  `toolcall_*`, all `*_start`/`*_end` framing, and any unrecognised
  sub-type/event are consumed internally (forward-compat with pi's
  weekly releases).
- `text_sentinels` inspects **only** `kind == "text"` — the v2 form of
  v1's anti-mention discipline (v1 stripped tool inputs via `jq`; v2
  additionally never parses `thinking` text).

spec §4.1/§4.2/§5.1 updated to match; §13 OQ-1/OQ-2/OQ-3 annotated.

## Signaling port

`signaling/sentinels.py` is a faithful line-based port of v1's
awk/jq parser (`relay-v1/bin/relay`, mirrored by
`relay-v1/tests/test-parsing.sh`): marker-pair extraction, the exact
decision order, and the verbatim error/repair strings (including the
"pre-2026-05-17 convention" / "takes no prompt body" recipes).
`MarkerError` carries headline + recipe so the ported substring
assertions match. All 30 v1 synthetic fixtures (c1–c13, marker
positive/negative, repair-recipe, phase-start groups) are ported to
`tests/harness/test_signaling_sentinels.py` and pass. Anti-mention is
now enforced upstream in the harness (only `AssistantText.kind=="text"`
reaches the parser) rather than by a `jq` pre-filter.

## Issue discovered: venv pinned to the old project path

The project moved `~/projects/relay-v2` → `~/projects/relay/relay-v2`.
The committed-state `.venv` had script shebangs and an editable `.pth`
pointing at the old absolute path, so `uv run` silently fell back to a
system pyenv Python with **no project deps** — `pytest` failed to import
`relay_v2` *and* `sqlalchemy`. `uv sync` "audited" without fixing
shebangs. Fix: rebuild the venv at the new path (`mv .venv aside &&
uv sync`). No code change; `.venv` is gitignored. Future env breakage
after a move is the same fix.

## Verification

- `uv run pytest` → **60 passed, 1 skipped**. The skip is
  `test_pi_integration.py` (gated behind `PI_INTEGRATION=1`; pi is never
  spawned by the default suite).
- Event-mapping unit tests pass against the captured `scratch/*.jsonl`
  fixtures (offline). Ported sentinel-parser tests pass.
- `uv run ruff check .` clean; `uv run mypy` (strict) clean — 15 source
  files.
- Real-pi `PI_INTEGRATION=1` path implemented (spawn → events →
  accumulate turn text → `text_sentinels` → `SignalEmitted` handoff →
  clean `SessionEnded`); not exercised in this session (gated).

## Follow-ups (out of Phase 1 scope)

- `duration_ms` on `ToolUseEnd` is timed by relay between start/end
  events (pi doesn't report it) — fine for now.
- Tool-result payload truncation is deferred to the EventStore write
  layer (Phase 2), per plan.md's stated mitigation — the harness does
  not truncate.
- Run the `PI_INTEGRATION=1` suite against live pi v0.74.0 before
  Phase 2 wires the orchestrator.

Added `docs/harness.md` (operational reference) per the global docs
policy.
