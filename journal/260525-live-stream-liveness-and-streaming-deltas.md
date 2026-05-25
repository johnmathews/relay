# 260525 — Live-stream liveness, streaming deltas, and an envelope-shape regression

Three pieces, all landed in one session:

1. A pre-existing live-stream payload-shape bug — surfaced as "tool
   cards are empty until you refresh."
2. **ADR-45 Plan A** — a named `heartbeat` SSE frame plus a
   `RunHealthBadge` so a quiet "pi is thinking" phase reads as
   healthy instead of frozen.
3. **ADR-46 Plan B** — streaming `text_delta` / `thinking_delta`
   chunks as ephemeral `assistant_delta` SSE frames so the dashboard
   paints tokens as pi produces them.

## What I came in to look at

User screenshotted a `/engineering-team` run that had been "stuck"
for ~5 minutes on the dashboard: timeline at two events
(`run_started`, `iter_started`), no further activity rendered. They
wanted to know whether it was actually healthy.

Process inspection found everything fine: `pi` PID alive, the
underlying `claude-agent-sdk` subprocess burning 3.5% CPU
continuously, started ~3 minutes earlier — well inside the 30-minute
iter timeout. The harness mapper (`pi.py:_PiEventMapper`) only
yields `AssistantText` at `turn_end` and `ToolUseStart` immediately
on a tool call, so a pi run that's doing nothing but `--thinking
adaptive` produces literally zero events for the orchestrator to
persist. The dashboard was faithful — it just had nothing to show.

That's a real UX gap, not a bug. User asked for plans for both a
heartbeat option (cheap "still alive" pulse) and a streaming-deltas
option (actual visible token output). Sketching the plans
incidentally surfaced a separate real bug.

## The envelope-unwrap bug (load-bearing for both plans)

While reading `frontend/src/stores/events.ts:onSseEvent` to write
the heartbeat plan I noticed:

```ts
ingest([{ seq: seqNum, kind: ev.type, payload: parsePayload(ev.data) }])
```

`parsePayload(ev.data)` parses the SSE frame's `data:` body. But
the backend (`api/events.py:_event_payload`) publishes the **full
envelope** `{seq, kind, payload, ts, run_id, iter_id}` — and the
REST replay path correctly unwraps `r.payload` before ingesting.
Live SSE was storing the whole envelope as `payload`, so every
renderer reading `event.payload.<field>` saw `undefined` (e.g.
`ToolCallCard.name` and `.args`) and the "generic" renderer dumped
the entire envelope as JSON. After a manual refresh the REST path
ran and the tool cards painted normally.

User had also seen this on their stuck run: live-updated tool cards
were empty; refresh rendered them in full. Now there's a name for
it.

Fix is one line — pluck `envelope.payload` (with a defensive
non-object → `{}` fallback). Regression test in
`events.store.spec.ts` synthesises the real wire shape and asserts
`row.payload.name`/`args`/`tool_id` are present (and that envelope
keys haven't leaked into the inner payload). Existing tests in that
file only checked `seq`/`kind`/invalidations, never payload
contents — which is why this had slipped through.

## Plan A — heartbeat

Replaced the pre-existing 15s `: keepalive` SSE comment with a
named `heartbeat` frame at a 5s cadence:

```
event: heartbeat
data: {"run_id":"…","server_ts":"…","last_event_ts":"…"}
```

Crucially, **no `id:` line**. Per the WHATWG SSE spec, a message
with no id leaves the browser's `Last-Event-ID` buffer unchanged
— which is what we want, because heartbeats are not persisted.
Bumping the cursor would point at a phantom DB row on reconnect
and break ADR-23 replay.

`last_event_ts` is tracked through the generator: replay loop
captures each `data["ts"]`, drain loop captures each `item.get("ts")`,
TimeoutError branch sends the latest. A brand-new connection that
reconnected past the tail sees `null` (legitimate, no event seen
on THIS connection yet).

Two new tests in `tests/api/test_sse.py`:
- `test_live_idle_emits_heartbeat_frame` — assert the shape, assert
  no `id:`.
- `test_live_drained_event_updates_last_event_ts` — assert a
  forwarded event mutates `last_event_ts` reported by the next
  heartbeat.

Tests monkeypatch `_KEEPALIVE_S` to 0.05s so they don't sleep.

Frontend side: a `'heartbeat'` listener added to `KNOWN_EVENT_TYPES`
in `sse.ts` (the load-bearing dual-list contract: browser
`EventSource` only fires listeners for explicitly registered named
events). Store has a new `lastHeartbeat: HeartbeatSnapshot | null`
shallowRef updated by `onSseEvent` — special-cased BEFORE the seq
check because heartbeats have no `id:` and the prior event's
`lastEventId` would otherwise re-ingest a duplicate seq. Heartbeats
do NOT enter the events list, do NOT bump `lastSeq`, do NOT
invalidate Colada caches (intentionally absent from
`INVALIDATING_KINDS`). Store regression test asserts all three
non-effects.

New `RunHealthBadge.vue` mounted next to `StatusBadge` in the
run-detail header. Renders nothing for terminal status (replay mode
has no live stream; stale clock would mislead). Ticking 1s interval
in `onMounted`/`onBeforeUnmount` drives the live age display.
Thresholds: ≤15s = `live` (green, pulsing); 15–60s = `slow`
(amber); >60s = `stalled` (red). 6 unit tests over a fake-timer
clock.

## Plan B — streaming deltas

New harness event `AssistantTextDelta(text, kind, turn_seq,
delta_seq)`. The mapper yields one inline for every pi `text_delta`
/ `thinking_delta` chunk — **additively**, the existing
`AssistantText` flush at `turn_end` is unchanged so ADR-18's
concatenation invariant (joining deltas of a (turn_seq, kind) =
canonical text) is preserved. `delta_seq` is monotonic within a
turn and resets on `turn_start`.

The Option-D lookahead (`PiSession.events`) handles deltas
correctly without modification: the existing else-branch flushes
any pending `AssistantText` first, then yields the delta — ordering
preserved. The one assertion update was
`test_fully_consumed_external_order_is_unchanged`, which now expects
`[SessionStarted, AssistantTextDelta, AssistantText, SessionEnded]`
where it used to expect three events. That assertion change IS the
ADR-46 contract change in test form.

Broadcaster gained a `publish_ephemeral(run_id, kind, data)`
method. It enqueues `{_ephemeral: True, _kind: kind, data: …}`. The
SSE drain loop discriminates on the `_ephemeral` marker BEFORE
reading `seq` from a normal envelope, then renders the frame
without an `id:` line (same shape as heartbeat). Slow-consumer
policy is identical to `publish` — a dropped delta is recoverable
because the canonical `AssistantText` is persisted and replay
backfills it.

`EventStore.store_harness_event` routes `AssistantTextDelta` to
`broadcaster.publish_ephemeral(...)` and returns without appending
— sharing the silent-drop dispatch path with `ToolUseUpdate` and
`SessionStarted`. Test in `tests/orchestrator/test_events.py`
asserts the events table sees nothing AND the broadcaster sees the
expected ephemeral shape including `iter_id`.

Frontend: `'assistant_delta'` added to `KNOWN_EVENT_TYPES`. Store
gains a `pendingMap` shallowRef keyed by `${iterId}:${turnSeq}:${kind}`
and exposed as a flat `pendingTurns` computed. Deltas accumulate
text; the matching entry is pruned when the canonical
`assistant_text` event arrives (matched on `envelope.iter_id` +
`payload.turn_seq` + `payload.kind`), or when `iter_ended` fires
(prefix-match by `${iid}:`). Two store tests cover both prune
paths.

`TimelinePane.vue` gained an optional `pendingTurns` prop. Renders
a pseudo-row per entry (`data-testid="pending-turn"`,
`data-pending-kind`) BELOW the canonical timeline. Hidden under an
active iter filter — deltas only flow for the live iter, so showing
them in a historical iter view would mislead. Two new TimelinePane
tests.

## Dual-list contract — easy to miss

CLAUDE.md already had a cross-cutting trap about `KNOWN_EVENT_TYPES`
(in `sse.ts`) vs `INVALIDATING_KINDS` (in `stores/events.ts`).
ADR-45 and ADR-46 both add events that should be in the former (so
the browser listener fires) and explicitly NOT in the latter (no
cache effect). The store regression tests for both pin that
non-effect down by asserting `invalidate.mock.calls.length === 0`
after the relevant frame.

Both ADRs also share the same id-less ephemeral-frame shape —
heartbeat (Plan A) and assistant_delta (Plan B) both deliberately
omit the `id:` line so the browser's Last-Event-ID cursor is
preserved across them. That's the same wire-level mechanism that
keeps reconnect resume working — a heartbeat or delta arriving
between persisted events doesn't pollute the cursor.

## What didn't change

- `RelayCore` API surface — no new methods.
- Event store schema and `events.kind` taxonomy — no new persisted
  kinds. Deltas and heartbeats live entirely outside the events
  table.
- Replay path — a reconnecting client sees no pending pseudo-rows
  but the canonical `AssistantText` is in the replay. Identical
  final state.
- OTel mirror — deltas deliberately not mirrored (would inflate
  trace size by 1000×; token totals are already on the canonical
  iter span's usage attributes per ADR-29).
- Signal detection — still happens only on the turn-complete
  `AssistantText`, never on a delta. Verified by reading
  `_drive_iter`'s signal-detection branch (loop.py:158).

## Final numbers

- Backend: 353 passed, 3 skipped, 94% coverage. Net +9 tests this
  session.
- Frontend: 214 passed across 30 files. Net +9 tests this session.
- ruff / mypy --strict / eslint / vue-tsc all clean.
- Two ADRs added (ADR-45, ADR-46). Three doc files updated
  (`docs/api.md`, `docs/dashboard.md`, `docs/harness.md`) plus
  `CLAUDE.md` to record the new event kinds and the cross-cutting
  trap.

## Operational note for the next live observation

When I look at a "stuck" run again, the new badge should answer the
"is this thing alive?" question at a glance. If the user clicks
into a paused run during pi's silent thinking phase, the pending
pseudo-row should also start filling in as soon as pi produces the
first delta — typically faster than the canonical AssistantText
flush by however long the turn takes. The two together replace the
"is this hung or thinking?" guesswork that prompted this whole
session.
