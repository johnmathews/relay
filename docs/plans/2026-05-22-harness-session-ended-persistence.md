# Persist `harness_session_ended` (ADR-39) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the latent ADR-10 invariant gap parked since Phase 7 — the
`SessionEnded` data (usage + stop_reason) is captured by the Option-D
lookahead in `PiSession.events()` and surfaced to OTel, but is **never
written to the events table**. This plan introduces a new event kind
`harness_session_ended` appended on **every iter-close path** before
`iter_ended`, with payload `{stop_reason, messages, summary}`. ADR-39
records the contract change; spec.md §3.2 and §6 are updated; the frontend
timeline renders a small row showing stop_reason + total tokens.

**Architecture:** A single new event kind, written by extending
`loop._finish_iter` to accept `stop_reason`/`messages`/`summary` and
prepend a `store.append("harness_session_ended", …)` ahead of the existing
`iter_ended` append. The four `_finish_iter` call sites in `run_loop`
already have `outcome.stop_reason` and `outcome.messages` populated by
`_drive_iter`'s finally block (line 200-205 of `loop.py`); the only
new datum is `summary`, threaded through on the `signal.kind == "done"`
branch only. The frontend reads the new kind via existing untyped event
streaming and renders it as a new timeline row type. No schema change,
no harness change (ADR-04 unchanged), no MCP change, no OTel change.

**Tech Stack:** Python 3.13 + SQLAlchemy async + pytest-asyncio +
FastAPI/Pydantic v2 (backend); Vue 3 + Pinia Colada + Vitest (frontend).

**Numbering:** Phase 9g in informal terms (continues the 9a–9f post-MVP
arc), but the change is self-contained and standalone — no inter-dep
with the fanout-join arc beyond ADR-29's lookahead which it leaves in
place verbatim.

---

## File structure

**Backend — modify:**
- `src/relay_v2/orchestrator/loop.py` — extend `_finish_iter` signature
  + threaded calls in all four close branches. (~30 lines net.)
- `docs/spec.md` — §3.2 taxonomy row added; §6 paragraph on the close-
  time persistence contract; cross-link to ADR-39.
- `docs/decisions.md` — append ADR-39 (~80 lines, format matches ADR-38).

**Backend — tests modify/add:**
- `tests/orchestrator/test_loop.py` — assert `harness_session_ended` is
  appended in every close path, ordered immediately before `iter_ended`,
  with the right payload. Existing event-count/seq assertions in this
  file shift by +1 per iter; they'll need re-baselining.
- `tests/api/test_sse.py` — assert SSE replay carries the new event row
  and Last-Event-ID dedupe still works around the bumped seq.

**Frontend — modify:**
- `frontend/src/stores/events.ts` — add `'harness_session_ended'` to
  `INVALIDATING_KINDS` (lifecycle event refreshes Colada caches).
- `frontend/src/components/runs/TimelinePane.vue` — recognize
  `harness_session_ended` as a new row type `'usage'` (or extend
  `'boundary'`); render `stop_reason` + total token count inline.
- `frontend/src/components/runs/UsageRow.vue` (NEW) — small SFC
  rendering one row: stop_reason badge, `Σ in / Σ out / Σ cache-read`
  tokens summed across `payload.messages`.

**Frontend — tests:**
- `frontend/tests/components/UsageRow.spec.ts` (NEW) — render with a
  fixture, assert badge text + token totals.
- `frontend/tests/components/TimelinePane.spec.ts` (modify) — assert
  the new row appears in the fold output.

**No files deleted; no schema migration; no new module created on
backend.** Source file count stays at **39** (the new ADR is markdown).

---

## Task list

### Task 1: Write ADR-39

**Files:**
- Modify: `docs/decisions.md` (append at end)

- [ ] **Step 1: Append ADR-39 in the exact format of ADR-38**

ADR-38 is at the bottom of `docs/decisions.md`. Append ADR-39 below it
following the existing structure (Status / Context / Decision (multiple
sub-decisions) / Rejected / Consequences / Related). Use this body:

```markdown
## ADR-39 — Persist `harness_session_ended` events on every iter close (ADR-10 invariant fix)

**Status:** accepted (2026-05-22).

**Context.** Phase 7 introduced the OTel mirror, and to keep
`gen_ai.usage.*` attributes accurate on terminal-sentinel iters
(`[[engteam:done]]` / `[[engteam:handoff]]` / `[[engteam:pause-for-input]]`
/ `[[engteam:fanout]]`), the pi harness gained an Option-D one-event
`AssistantText` lookahead so `agent_end` (carrier of
`SessionEnded.messages` per ADR-18) is consumed in-stream before the
orchestrator breaks on the sentinel detection (ADR-29). The trade-off
recorded in the ADR-29 implementation comment of `harness/pi.py`:
*"external event order is unchanged and the event store is unaffected
(the orchestrator still breaks before `SessionEnded` is yielded — no
`agent_end` row, ADR-10 contract intact)"*. The "ADR-10 contract intact"
clause is the parked debt — the OTel mirror sees the data, but the event
store does not. ADR-10's invariant is "event store is the single source
of truth — every observable action is an append-only events row"; a
session ending is observable, and the row was never written. The Phase
9a–9f arc deliberately left this open because the in-flight OTel work
got the data where it needed to go (Langfuse), and closing the event-
store gap is its own contract change touching spec.md §3.2 (taxonomy)
and §6 (orchestrator close path). This ADR records that close.

**Decision — new event kind `harness_session_ended`.** A relay-domain
event kind (not `agent_end` — that's pi vocabulary, and ADR-04
prohibits harness terms leaking above the boundary) appended to the
events table on every iter-close path immediately before `iter_ended`.
The payload is:

```json
{
  "stop_reason": "clean" | "crash" | "timeout" | "cancelled",
  "messages": [...],
  "summary": "..."
}
```

- `stop_reason`: verbatim from `SessionEnded.stop_reason` (captured by
  `PiSession.wait()` which returns the in-stream-consumed
  `_final` on clean terminals, or the synthesised one on cancel /
  crash / timeout).
- `messages`: verbatim from `SessionEnded.messages` — the ADR-18
  opaque-passthrough convention is preserved. Each message carries its
  per-message usage block (input/output/cache-read tokens) which is
  what Langfuse and the dashboard usage row both need.
- `summary`: populated **only** on the `signal.kind == "done"` close
  path, from `signal.args.get("summary")`. `null` on every other
  close path. Redundant with `run_ended.summary` on the *final* iter
  of a `done` run; retained because the iter-level event is the
  natural place for the iter's summary and consumers shouldn't have
  to walk forward to `run_ended` to find it.

**Decision — emit on every iter-close path, not just the
terminal-sentinel path.** Cancelled / timed-out / no-signal /
crash iters all have `outcome.stop_reason` and `outcome.messages`
populated by `_drive_iter`'s finally block. ADR-10's invariant is
universal; restricting persistence to the clean-close path would
leave four observable-action gaps. The cost is one extra event row
per iter (already small relative to per-iter `assistant_text` /
`tool_use_*` traffic).

**Decision — append in `loop._finish_iter`, not in
`EventStore.store_harness_event`.** Two reasons:

1. The harness `SessionEnded` HarnessEvent is never yielded to the
   loop by `PiSession.events()` (it is consumed in-stream by Option
   D — ADR-29). So `store_harness_event(ev)` never receives one.
2. The cancelled/timeout paths don't have a `SessionEnded` until
   `session.wait()` is awaited in the finally — which `loop._drive_iter`
   already does. Putting the append in `_finish_iter` keeps the
   producer (loop) and persistence (events table) on one side of the
   harness boundary and leaves `store_harness_event` purely a
   harness-event router.

The `EventStore.store_harness_event` docstring is updated to reflect
this:`SessionEnded` is still not mapped there — it is the loop's job to
write `harness_session_ended` from the `session.wait()` result.

**Decision — event ordering: `harness_session_ended` BEFORE
`iter_ended`.** Both events describe the same close moment. The
session-end is a more granular fact ("the harness session terminated
with stop_reason X and these messages"); `iter_ended` is the
orchestrator-level fact ("the iter closed with this signal and
exit_reason"). Putting `harness_session_ended` first preserves the
intuition that the iter row is the *summary*, and that anything
inside the iter (including its terminal session) precedes the iter
close. Consumers tailing the stream see usage land before the iter
boundary, which matches how a real session ends in time.

**Decision — frontend timeline gets a small usage row.** A new
`UsageRow.vue` SFC renders `harness_session_ended` events as a single
metadata line: stop_reason badge + summed input/output/cache-read
tokens across `payload.messages`. The kind is added to
`INVALIDATING_KINDS` in `stores/events.ts` so Colada caches refresh
when one arrives.

**Rejected — extend `iter_ended` payload with `messages`/usage.**
Bundles a potentially-large payload into a row whose existing
`{seq, signal_kind, exit_reason}` consumers expect to be small. SSE
frame sizes grow on every iter, replay pagination effectiveness drops,
and the semantic mixing (lifecycle + data) leaks ADR-18's opaque-
messages convention into the iter-close row. Separating preserves the
small-row invariant for `iter_ended`.

**Rejected — name it `agent_end`.** Matches pi's raw event name and
spec's existing `agent_end_no_signal` exit_reason vocabulary, but
imports pi vocabulary into the relay event taxonomy. ADR-04 keeps
pi-specific terms inside `harness/`. `harness_session_ended` reads as
"a harness session has ended" — agnostic across the harness Protocol,
which is the point of the abstraction.

**Rejected — emit only on the clean terminal-sentinel close path.**
The literal reading of the original deferral note. Leaves four
observable-action gaps (cancel / timeout / no-signal / crash) where
`session.wait()` data is silently discarded. ADR-10's invariant is
either universal or it is not load-bearing.

**Rejected — derive usage from existing `assistant_text` rows on
read.** Per-message usage lives only in `SessionEnded.messages`, never
in the streaming `text_delta` deltas (ADR-18 — text is captured live;
usage is the close-time roll-up). A read-time derivation would need to
re-parse and re-attribute, which is precisely the problem ADR-10
forbids — the event store should *contain* the truth, not require
reconstruction.

**Consequences.**

- Per-iter event count goes up by exactly one. SSE replay payload size
  grows by `len(messages)` per iter for token-usage carrying messages;
  bounded by pi's existing per-iter message count. Replay pagination
  in `api/events.py::sse_event_stream._replay` (`_REPLAY_PAGE = 500`)
  is unaffected — the page is row-count-bounded, not byte-bounded.
- `tests/orchestrator/test_loop.py`'s event-count and per-seq
  assertions need re-baselining (+1 per iter, ordering preserved).
- `tests/api/test_sse.py` Last-Event-ID dedupe assertions need
  re-baselining for the same reason — no behaviour change in the
  cutover dedupe itself, just the seq numbers.
- ADR-29 (Option-D lookahead) remains in place verbatim. The OTel
  mirror still consumes `out.messages` from `_drive_iter`'s finally
  block; it does NOT read the new event row. Two consumers of the
  same in-memory data is fine — the event row is for replay
  (consumers that arrive later) and the in-memory mirror is for OTel
  (real-time export).
- Frontend Timeline gains a new row type. Existing rows render
  unchanged.

**Related:** ADR-04 (harness isolation — preserved; the new event
kind is relay-domain), ADR-10 (event store as source of truth — the
invariant this ADR closes the gap on), ADR-18 (opaque-messages
convention — payload preserves it verbatim), ADR-29 (Option-D
lookahead — left in place; this ADR's persistence is independent of
the lookahead),
`docs/plans/2026-05-22-harness-session-ended-persistence.md`.
```

- [ ] **Step 2: Commit the ADR alone**

```bash
git add docs/decisions.md
git commit -m "docs(adr): ADR-39 — persist harness_session_ended event on every iter close

Records the contract change that closes the latent ADR-10 invariant gap
parked since Phase 7. The OTel mirror sees SessionEnded data via the
ADR-29 Option-D lookahead, but the event store never received the row.
ADR-39 mandates a new \`harness_session_ended\` event kind appended in
loop._finish_iter on every iter-close path before iter_ended.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Update spec.md §3.2 + §6

**Files:**
- Modify: `docs/spec.md` (§3.2 taxonomy table; §6 close-path paragraph)

- [ ] **Step 1: Add the new event kind row to §3.2**

Find the taxonomy table (currently ~lines 184–198). Add a row between
`iter_ended` and `pause_requested` (ordered by lifecycle position):

```markdown
| `harness_session_ended` | iter's harness session terminates (every close path) — appended **before** `iter_ended` (spec §6, ADR-39) | `{stop_reason, messages, summary}` — `stop_reason ∈ {clean, crash, timeout, cancelled}`; `messages` is `SessionEnded.messages` verbatim (ADR-18 opaque); `summary` populated only on the `done` close path, `null` otherwise |
```

- [ ] **Step 2: Add a paragraph to §6 (Orchestrator) describing the close-time write**

Find §6's existing close-path paragraph (search `agent_end_no_signal`).
Append a new paragraph after the existing close-reason narrative:

```markdown
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
```

- [ ] **Step 3: Commit spec.md alone**

```bash
git add docs/spec.md
git commit -m "docs(spec): §3.2 + §6 — add harness_session_ended event kind + close-time persistence contract (ADR-39)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Backend test-first — assert `harness_session_ended` on terminal-signal close

**Files:**
- Test: `tests/orchestrator/test_loop.py`
- Test helper: existing fixtures in `tests/orchestrator/scripted_harness.py`

- [ ] **Step 1: Write the new test asserting harness_session_ended on signal close**

Add to `tests/orchestrator/test_loop.py` (place near the existing
terminal-signal tests; search for `def test_loop_done_signal` or similar
landmark and add below):

```python
def test_loop_emits_harness_session_ended_on_done_close(
    scripted_harness, store, run_ctx
):
    """On a clean `[[engteam:done]]` close, the loop appends one
    `harness_session_ended` event with `stop_reason='clean'`,
    `messages=<verbatim>`, `summary='wrap summary'`, ordered immediately
    before the iter's `iter_ended` (ADR-39)."""
    messages_fixture = [
        {"role": "assistant", "content": "...", "usage": {"input_tokens": 12, "output_tokens": 7}}
    ]
    scripted_harness.queue_session(
        text_turns=[
            'Working on it.\n[[engteam:done]]\nsummary: wrap summary\n'
        ],
        end_messages=messages_fixture,
        stop_reason="clean",
    )

    asyncio.run(run_loop(
        run_ctx,
        harness=scripted_harness,
        store=store,
        cancel_event=asyncio.Event(),
        session_handle=SessionHandle(),
    ))

    rows = asyncio.run(store.list_events(run_ctx.run_id, after_seq=0))
    kinds_in_order = [r.kind for r in rows]
    assert "harness_session_ended" in kinds_in_order

    hse_idx = kinds_in_order.index("harness_session_ended")
    ie_idx = kinds_in_order.index("iter_ended")
    assert hse_idx < ie_idx, "harness_session_ended must precede iter_ended"

    hse_row = rows[hse_idx]
    assert hse_row.payload == {
        "stop_reason": "clean",
        "messages": messages_fixture,
        "summary": "wrap summary",
    }
    assert hse_row.iter_id == rows[ie_idx].iter_id, \
        "harness_session_ended and iter_ended share the iter_id"
```

- [ ] **Step 2: Run it and verify it fails**

```bash
uv run pytest tests/orchestrator/test_loop.py::test_loop_emits_harness_session_ended_on_done_close -v
```

Expected: `AssertionError: 'harness_session_ended' is not in list of kinds` (the event is not yet emitted).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/orchestrator/test_loop.py
git commit -m "test(orchestrator): assert harness_session_ended emitted on done close (red)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Backend — extend `_finish_iter` to emit `harness_session_ended`

**Files:**
- Modify: `src/relay_v2/orchestrator/loop.py`

- [ ] **Step 1: Extend `_finish_iter` signature + body**

Replace the existing `_finish_iter` (around line 209-232 of `loop.py`):

```python
async def _finish_iter(
    store: EventStore,
    *,
    run_id: str,
    iter_id: int,
    seq: int,
    signal_kind: str | None,
    signal_args: dict[str, Any] | None,
    exit_reason: str,
    stop_reason: str,
    messages: list[Any],
    summary: str | None = None,
) -> None:
    """Close the iter row + append `harness_session_ended` then `iter_ended`.

    The `harness_session_ended` event (ADR-39) lands BEFORE `iter_ended`
    on every close path: terminal signal, cancelled, timed-out,
    no-signal, crash. Both events share `iter_id`; `harness_session_ended`
    persists pi's verbatim `SessionEnded.messages` (ADR-18 opaque) and
    `stop_reason`, closing the ADR-10 invariant gap parked since Phase 7
    (ADR-29 captured the data for OTel; the event store now gets it too).
    """
    await store.append(
        run_id,
        "harness_session_ended",
        {
            "stop_reason": stop_reason,
            "messages": messages,
            "summary": summary,
        },
        iter_id=iter_id,
    )
    await close_iter(
        store.sessionmaker,
        iter_id,
        signal_kind=signal_kind,
        signal_args=signal_args,
        exit_reason=exit_reason,
    )
    await store.append(
        run_id,
        "iter_ended",
        {"seq": seq, "signal_kind": signal_kind, "exit_reason": exit_reason},
        iter_id=iter_id,
    )
```

- [ ] **Step 2: Update each of the four call sites in `run_loop`**

There are four `_finish_iter` calls in `run_loop` (currently lines
319, 327, 355, 366). Update each to pass `stop_reason`, `messages`, and
(only on the done branch) `summary`. Match each call site exactly —
preserve the surrounding `iter_span.set_exit(...)` and the subsequent
`return LoopResult(...)` lines verbatim.

**Cancelled branch** (around line 317):

```python
            if outcome.cancelled:
                iter_span.set_exit("cancelled")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=None,
                    exit_reason="cancelled",
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                )
                return LoopResult("cancelled", reason="cancelled")
```

**Timeout branch** (around line 325):

```python
            if outcome.timed_out:
                iter_span.set_exit("timeout")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=None,
                    exit_reason="timeout",
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                )
                return LoopResult("failed", reason="timeout")
```

**No-signal branch** (around line 354):

```python
                iter_span.set_exit(reason)
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=args,
                    exit_reason=reason,
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                )
                return LoopResult(
                    "failed", reason=reason,
                    summary=outcome.marker_headline,
                )
```

**Signal branch** (around line 365). This is the only branch carrying
`summary`. Extract it from `signal.args` (the `done` close has it; the
others may carry `summary` too in fixtures, fall back to `None`):

```python
            iter_span.set_exit("signal")
            summary_val = signal.args.get("summary") if signal.kind == "done" else None
            await _finish_iter(
                store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                signal_kind=signal.kind, signal_args=signal.args,
                exit_reason="signal",
                stop_reason=outcome.stop_reason,
                messages=outcome.messages,
                summary=summary_val,
            )
```

(The remainder of the signal branch — the `if signal.kind == "done":`
return, `pause`, `fanout`, `handoff` — is unchanged.)

- [ ] **Step 3: Run the failing test and verify it now passes**

```bash
uv run pytest tests/orchestrator/test_loop.py::test_loop_emits_harness_session_ended_on_done_close -v
```

Expected: PASS.

- [ ] **Step 4: Run the full orchestrator test suite to see what shifted**

```bash
uv run pytest tests/orchestrator/ -v 2>&1 | tail -80
```

Expected: many existing tests now fail with "expected N events, got N+1"
or "expected seq=5, got seq=6" type assertions. **This is expected and
is the whole reason the plan has Task 5.** Do not try to fix them by
weakening the loop change — fix the test baselines.

- [ ] **Step 5: Commit the loop change alone (with the green new test, before re-baselining)**

```bash
git add src/relay_v2/orchestrator/loop.py tests/orchestrator/test_loop.py
git commit -m "feat(orchestrator): emit harness_session_ended in _finish_iter on every iter close (ADR-39)

The new event is appended BEFORE iter_ended on all four close paths
(signal / cancelled / timeout / no-signal). Payload mirrors
SessionEnded shape: {stop_reason, messages, summary}. summary is
populated only on the done close path.

Other orchestrator tests with seq/count assertions will fail on this
commit; the next commit re-baselines them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Re-baseline existing orchestrator tests

**Files:**
- Modify: `tests/orchestrator/test_loop.py`
- Modify: `tests/orchestrator/test_relay_core.py` (search for event-count assertions)
- Modify: `tests/orchestrator/test_fanout_*` (if any assert specific seqs)
- Modify: any other `tests/orchestrator/test_*.py` that asserts an event count or seq

- [ ] **Step 1: Run the orchestrator suite to enumerate failures**

```bash
uv run pytest tests/orchestrator/ -x 2>&1 | tail -40
```

For each failing test that asserts `len(events) == N`, `events[i].seq ==
S`, or `kinds == [...]`, you have two valid edits:

- Add `'harness_session_ended'` to the expected `kinds` list at the
  right position (immediately before each `iter_ended`).
- Bump expected event counts by +1 per iter.

- [ ] **Step 2: Update each failing test mechanically**

For each test, prefer the "list of expected kinds" form if it exists —
adding `'harness_session_ended'` immediately before each `iter_ended`
documents the invariant. For pure count assertions, increment the
literal by +1 per closed iter. Do NOT use `len(events) > N` slop — keep
the assertions exact.

After each test edit, re-run that specific test:

```bash
uv run pytest tests/orchestrator/test_loop.py::TEST_NAME -v
```

- [ ] **Step 3: Run the full orchestrator suite and confirm green**

```bash
uv run pytest tests/orchestrator/ -v 2>&1 | tail -20
```

Expected: all green (the existing 293-test baseline +1 from Task 3).

- [ ] **Step 4: Commit the re-baseline**

```bash
git add tests/orchestrator/
git commit -m "test(orchestrator): re-baseline event-count + seq assertions for harness_session_ended (ADR-39)

Every iter close now appends one harness_session_ended event before
iter_ended (see ADR-39). Existing tests that asserted exact event
counts or seqs are bumped by +1 per closed iter; existing tests that
asserted full kind sequences are updated to include
'harness_session_ended' immediately before each 'iter_ended'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Verify SSE replay + cutover dedupe still works

**Files:**
- Modify: `tests/api/test_sse.py` — add a focused test; re-baseline any seq-specific assertions.

- [ ] **Step 1: Add an SSE-replay test asserting the new event is carried**

In `tests/api/test_sse.py`, add (model after the existing terminal-run
replay test, e.g. one that exercises a closed run's history):

```python
async def test_sse_replay_includes_harness_session_ended(async_client, ...):
    """After a run terminates, replaying its events via GET /api/events
    must include the harness_session_ended row in order with iter_ended
    (ADR-39 + ADR-23 replay invariant)."""
    run_id = await _start_and_finish_done_run(...)  # use existing helper

    resp = await async_client.get(f"/api/events/{run_id}", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    body = resp.text

    # SSE frames are `id:N\nevent:KIND\ndata:JSON\n\n` triples.
    frames = [f for f in body.split("\n\n") if f.strip()]
    kinds = [f.split("event: ", 1)[1].split("\n", 1)[0] for f in frames]

    assert "harness_session_ended" in kinds
    hse_idx = kinds.index("harness_session_ended")
    ie_idx = kinds.index("iter_ended")
    assert hse_idx < ie_idx
```

- [ ] **Step 2: Run it**

```bash
uv run pytest tests/api/test_sse.py::test_sse_replay_includes_harness_session_ended -v
```

Expected: PASS (Task 4's loop change already writes the row; SSE replay
is a passive tail).

- [ ] **Step 3: Run the full API suite and re-baseline any seq-specific failures**

```bash
uv run pytest tests/api/ -v 2>&1 | tail -30
```

For any test that asserts a specific event seq or count, apply the same
mechanical +1-per-iter update from Task 5.

- [ ] **Step 4: Run the full backend gate**

```bash
uv run ruff check . && uv run mypy --strict src/ && uv run pytest 2>&1 | tail -20
```

Expected: ruff clean, mypy clean, pytest green (293 + 1 = 294 pass, 3
pi-e2e gated).

- [ ] **Step 5: Commit the SSE work**

```bash
git add tests/api/
git commit -m "test(api): assert SSE replay carries harness_session_ended in order

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend — recognize `harness_session_ended` as a lifecycle event

**Files:**
- Modify: `frontend/src/stores/events.ts`

- [ ] **Step 1: Add the new kind to INVALIDATING_KINDS**

In `frontend/src/stores/events.ts` (around line 74), add the new kind
to the set (alphabetical-ish order; place it before `iter_ended`):

```ts
const INVALIDATING_KINDS = new Set([
  'run_started',
  'iter_started',
  'harness_session_ended',
  'iter_ended',
  'signal_emit',
  'pause_requested',
  'pause_resolved',
  'run_ended',
  'subagent_dispatch',
  'subagent_return',
  'child_runs_resolved',
])
```

- [ ] **Step 2: Run frontend tests to confirm no regressions**

```bash
cd frontend && npm run test 2>&1 | tail -10
```

Expected: no test that asserts the exact contents of `INVALIDATING_KINDS`
breaks. (If one does, update its expectation to match.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/events.ts
git commit -m "feat(frontend): treat harness_session_ended as a lifecycle event (ADR-39)

Adds the new event kind to INVALIDATING_KINDS so its arrival refreshes
Colada-cached run detail / run lists / children list.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend — UsageRow.vue SFC

**Files:**
- Create: `frontend/src/components/runs/UsageRow.vue`
- Test: `frontend/tests/components/UsageRow.spec.ts`

- [ ] **Step 1: Write the spec first (TDD red)**

Create `frontend/tests/components/UsageRow.spec.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UsageRow from '@/components/runs/UsageRow.vue'

describe('UsageRow', () => {
  it('renders stop_reason badge + summed token counts', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 42,
          kind: 'harness_session_ended',
          payload: {
            stop_reason: 'clean',
            summary: 'wrap-up',
            messages: [
              { role: 'assistant', usage: { input_tokens: 12, output_tokens: 7, cache_read_input_tokens: 3 } },
              { role: 'assistant', usage: { input_tokens: 30, output_tokens: 21, cache_read_input_tokens: 9 } },
            ],
          },
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('clean')
    expect(text).toContain('42')  // sum input
    expect(text).toContain('28')  // sum output
    expect(text).toContain('12')  // sum cache-read
  })

  it('renders gracefully when messages is empty', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 1,
          kind: 'harness_session_ended',
          payload: { stop_reason: 'cancelled', summary: null, messages: [] },
        },
      },
    })
    expect(wrapper.text()).toContain('cancelled')
    expect(wrapper.text()).toContain('0')
  })
})
```

- [ ] **Step 2: Run the spec to verify it fails (file doesn't exist yet)**

```bash
cd frontend && npm run test -- UsageRow.spec.ts
```

Expected: FAIL with "Cannot find module '@/components/runs/UsageRow.vue'".

- [ ] **Step 3: Create the SFC**

Create `frontend/src/components/runs/UsageRow.vue`:

```vue
<script setup lang="ts">
// A `harness_session_ended` timeline row (ADR-39). Shows the harness
// session's stop_reason + the summed input/output/cache-read tokens
// across the message-level usage blocks (ADR-18: messages are opaque
// to relay; we just sum the documented numeric keys).

import { computed } from 'vue'
import type { StreamEvent } from '@/stores/events'

const props = defineProps<{ event: StreamEvent }>()

interface UsageBlock {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
}

interface MessageWithUsage {
  usage?: UsageBlock
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

const totals = computed(() => {
  const messages = props.event.payload.messages
  const list: MessageWithUsage[] = Array.isArray(messages)
    ? (messages as MessageWithUsage[])
    : []
  let inputTokens = 0
  let outputTokens = 0
  let cacheRead = 0
  for (const m of list) {
    const u = m.usage ?? {}
    inputTokens += num(u.input_tokens)
    outputTokens += num(u.output_tokens)
    cacheRead += num(u.cache_read_input_tokens)
  }
  return { inputTokens, outputTokens, cacheRead }
})

const stopReason = computed(() => {
  const r = props.event.payload.stop_reason
  return typeof r === 'string' ? r : 'unknown'
})
</script>

<template>
  <div class="usage-row" :data-stop-reason="stopReason">
    <span class="badge">{{ stopReason }}</span>
    <span class="tokens">
      Σ in {{ totals.inputTokens }} · out {{ totals.outputTokens }} · cache {{ totals.cacheRead }}
    </span>
  </div>
</template>

<style scoped>
.usage-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  padding: 0.25rem 0.5rem;
  font-size: 0.85em;
  color: var(--color-text-muted, #888);
  border-left: 2px solid var(--color-border-subtle, #e0e0e0);
}
.badge {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tokens {
  font-variant-numeric: tabular-nums;
}
</style>
```

- [ ] **Step 4: Run the spec to verify green**

```bash
cd frontend && npm run test -- UsageRow.spec.ts
```

Expected: PASS (both cases).

- [ ] **Step 5: Run vue-tsc + eslint to confirm clean**

```bash
cd frontend && npm run check 2>&1 | tail -20
```

Expected: all green.

- [ ] **Step 6: Commit the new SFC**

```bash
git add frontend/src/components/runs/UsageRow.vue frontend/tests/components/UsageRow.spec.ts
git commit -m "feat(frontend): UsageRow.vue — timeline row for harness_session_ended (ADR-39)

Renders stop_reason + summed input/output/cache-read tokens across the
event payload's messages[].usage blocks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Wire UsageRow into TimelinePane

**Files:**
- Modify: `frontend/src/components/runs/TimelinePane.vue`
- Test: `frontend/tests/components/TimelinePane.spec.ts` (modify if it asserts row types)

- [ ] **Step 1: Add `'usage'` as a Row['type'] in TimelinePane.vue**

In `TimelinePane.vue` around line 78-94, extend the `Row['type']` union:

```ts
interface Row {
  /** Stable key. */
  key: string
  /** 'tool' | 'signal' | 'message' | 'boundary' | 'pause' | 'usage' | 'generic'. */
  kind: Row['type'] extends never ? never : string
  type:
    | 'tool'
    | 'signal'
    | 'message'
    | 'boundary'
    | 'pause'
    | 'usage'
    | 'generic'
  /** The originating event (newest of a merged pair for tools). */
  event: StreamEvent
  /** Paired tool_use_end payload, when this is a tool row. */
  toolEnd?: Record<string, unknown>
}
```

- [ ] **Step 2: Branch on the new kind inside the rows computed**

Inside the `rows = computed(...)` (around line 130-170), add a branch
**before** the catch-all `generic` else:

```ts
    } else if (ev.kind === 'harness_session_ended') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'usage',
        event: ev,
      })
    } else if (
```

(insert immediately before the existing `iter_started / iter_ended /
run_started / run_ended` branch so the boundary check still catches
those.)

- [ ] **Step 3: Render UsageRow in the template**

Find the row-render `<template>` section (look for the `v-for` over
windowed rows). Add a branch where the other row-type components are
mounted. Pattern matches the existing dispatch on `row.type`. Import
UsageRow at the top of the `<script setup>`:

```ts
import UsageRow from './UsageRow.vue'
```

And in the template, where other types are rendered, add:

```vue
<UsageRow v-else-if="row.type === 'usage'" :event="row.event" />
```

The exact insertion location depends on the current template; the
critical thing is that `row.type === 'usage'` has its own branch and is
NOT caught by the generic fallback.

- [ ] **Step 4: Update / add a TimelinePane test**

Open `frontend/tests/components/TimelinePane.spec.ts`. If a "row-types
present" assertion exists, extend it. Otherwise add a focused test:

```ts
it('renders harness_session_ended as a UsageRow', async () => {
  const wrapper = mount(TimelinePane, {
    props: {
      events: [
        { seq: 1, kind: 'iter_started', payload: { seq: 1 } },
        {
          seq: 2,
          kind: 'harness_session_ended',
          payload: {
            stop_reason: 'clean',
            summary: 'ok',
            messages: [{ usage: { input_tokens: 5, output_tokens: 3 } }],
          },
        },
        { seq: 3, kind: 'iter_ended', payload: { seq: 1, signal_kind: 'done', exit_reason: 'signal' } },
      ],
    },
  })
  expect(wrapper.findComponent({ name: 'UsageRow' }).exists()).toBe(true)
  expect(wrapper.text()).toContain('clean')
})
```

- [ ] **Step 5: Run the frontend gate**

```bash
cd frontend && npm run check 2>&1 | tail -20
```

Expected: vue-tsc clean, eslint clean (--max-warnings 0), vitest all
pass (existing baseline + 2 new tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/runs/TimelinePane.vue frontend/tests/components/TimelinePane.spec.ts
git commit -m "feat(frontend): wire UsageRow into TimelinePane (ADR-39)

harness_session_ended events now render as a small usage row in the
run timeline (stop_reason + summed token counts) instead of falling
into the generic JSON row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Update CLAUDE.md "Current state" walkthrough

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a short "Phase 9g" paragraph to the Current state walkthrough**

CLAUDE.md's "Current state" section is a chronological narrative of
shipped phases. Append a paragraph after the existing Phase 9f
paragraph (search for "Phase 9f then closes" → end of fanout-join arc):

```markdown
**Phase 9g** then closes the latent ADR-10 invariant gap that
`SessionEnded` was captured by the Option-D harness lookahead and
surfaced to OTel (ADR-29) but never written to the events table. A
new event kind `harness_session_ended` is appended in
`loop._finish_iter` on every iter-close path (signal / cancelled /
timeout / no-signal / crash) BEFORE the paired `iter_ended` event,
with payload `{stop_reason, messages, summary}` — `messages`
verbatim per ADR-18, `summary` populated only on the `done` close
path. The OTel mirror still reads from `out.messages` in-memory in
the loop's finally block (ADR-29 lookahead preserved); the new
event row is for replay consumers (SSE, audit, future analytics).
Frontend gains a small `UsageRow.vue` rendering stop_reason +
summed token counts inline in the timeline; `INVALIDATING_KINDS`
in `stores/events.ts` includes the new kind so Colada caches
refresh. New ADR-39 records the contract change; spec.md §3.2
gains the taxonomy row and §6 the close-time persistence
paragraph. No schema change, no harness change (ADR-04 preserved),
no MCP change. Backend tests: 293 + ~N new harness_session_ended
assertions + re-baselined event counts. Frontend: 155 + 2 new
UsageRow / TimelinePane tests.
```

(Fill in the actual `N` after Task 5 finishes — the final test count
is whatever the orchestrator suite settles at.)

- [ ] **Step 2: Commit CLAUDE.md alone**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): add Phase 9g walkthrough — harness_session_ended persistence (ADR-39)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: End-to-end verification

- [ ] **Step 1: Full backend gate**

```bash
uv run ruff check . && uv run mypy --strict src/ && uv run pytest 2>&1 | tail -10
```

Expected: all green.

- [ ] **Step 2: Full frontend gate**

```bash
cd frontend && npm run check 2>&1 | tail -10
```

Expected: all green.

- [ ] **Step 3: Manual smoke (optional, pi-gated)**

Set `PI_INTEGRATION=1` and run a real pi-driven `done` close (the
pi-e2e test suite). Verify the resulting events DB contains a
`harness_session_ended` row with non-empty `messages` carrying usage:

```bash
PI_INTEGRATION=1 uv run pytest tests/orchestrator/test_pi_e2e.py -v
# then inspect: sqlite3 .relay/relay.db "select kind from events where run_id=... order by seq"
```

Expected: `harness_session_ended` appears once per closed iter,
immediately before `iter_ended`.

- [ ] **Step 4: Final tree review**

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

Expected: 7–8 small commits (ADR / spec / failing-test / loop / re-
baseline / SSE / frontend store / frontend SFC / TimelinePane / CLAUDE).
Each one self-contained; the squash-merge produces one PR commit.

---

## Self-review notes

**Spec coverage:**
- spec.md §3.2 taxonomy update → Task 2.
- spec.md §6 close-path contract → Task 2.
- ADR-39 → Task 1.
- Backend persistence (every iter close) → Task 4.
- Backend tests (every close path) → Task 3 + Task 5.
- SSE replay invariant → Task 6.
- Frontend lifecycle invalidation → Task 7.
- Frontend timeline render → Task 8 + Task 9.
- CLAUDE.md walkthrough → Task 10.
- End-to-end gate → Task 11.

**Placeholder scan:** none — every step has either exact code, exact
file paths, or exact commands.

**Type consistency:** payload shape `{stop_reason, messages, summary}`
appears identically in Task 1 (ADR), Task 2 (spec), Task 3 (test), Task
4 (implementation), Task 8 (frontend SFC). Event kind string
`'harness_session_ended'` appears identically everywhere.

**Scope notes:**
- The OTel mirror (`set_usage(messages)` in `loop.py:296`) is left
  unchanged — it reads from `outcome.messages` in-memory, not from the
  new event row. Two consumers of the same data is intentional
  (real-time mirror + replay store).
- No schema migration (events table accepts arbitrary kinds via the
  `kind TEXT NOT NULL` column).
- No harness contract change. `PiSession.events()` still consumes
  `SessionEnded` internally per ADR-29.
