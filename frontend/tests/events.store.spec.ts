import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import type { EventSourceLike } from '../src/api/sse'

// Mock the api client so the REST replay path needs no backend.
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: { GET: (...a: unknown[]) => GET(...a) },
}))

import { useEventsStore } from '../src/stores/events'

/** Controllable fake EventSource injected into the REAL W1 wrapper. */
class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  readyState: number = 1
  private listeners = new Map<string, Array<(ev: MessageEvent) => void>>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (ev: MessageEvent) => void): void {
    const arr = this.listeners.get(type) ?? []
    arr.push(cb)
    this.listeners.set(type, arr)
  }

  close(): void {
    this.closed = true
    this.readyState = 2
  }

  emit(type: string, data: string, lastEventId: string): void {
    const ev = { type, data, lastEventId } as unknown as MessageEvent
    for (const cb of this.listeners.get(type) ?? []) cb(ev)
  }

  emitError(): void {
    const ev = { type: 'error' } as unknown as MessageEvent
    for (const cb of this.listeners.get('error') ?? []) cb(ev)
  }
}

function freshFactory(): (url: string) => EventSourceLike {
  FakeEventSource.instances = []
  return (url: string) => new FakeEventSource(url)
}

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}

describe('events store — replay vs live orchestration', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    GET.mockReset()
  })

  it('terminal-status run ⇒ REST replay, SSE never opened', async () => {
    GET.mockResolvedValue(
      ok({
        events: [
          { seq: 1, kind: 'run_started', payload: { x: 1 } },
          { seq: 2, kind: 'iter_started', payload: {} },
        ],
        after_seq: 0,
        limit: 500,
        offset: 0,
      }),
    )
    const store = useEventsStore()
    const factory = freshFactory()
    await store.open('run-1', 'done', {
      streamOptions: { eventSourceFactory: factory },
    })

    expect(store.mode).toBe('replay')
    expect(FakeEventSource.instances.length).toBe(0)
    expect(store.events.map((e) => e.seq)).toEqual([1, 2])
    expect(store.renderedCount).toBe(2)
  })

  it('running run ⇒ SSE opened via the injected fake EventSource', async () => {
    const store = useEventsStore()
    await store.open('run-2', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    expect(store.mode).toBe('live')
    expect(FakeEventSource.instances.length).toBe(1)
    expect(GET).not.toHaveBeenCalled()
  })

  it('paused run stays LIVE (paused is not terminal)', async () => {
    const store = useEventsStore()
    await store.open('run-2b', 'paused', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    expect(store.mode).toBe('live')
    expect(FakeEventSource.instances.length).toBe(1)
  })

  it('same-run re-open (pause→resume) closes the prior stream, no orphan', async () => {
    // Regression: a paused run stays LIVE with an open stream. On resume
    // the view re-calls open() with the SAME runId. The prior stream
    // must be closed before the new one opens, or it is orphaned and
    // keeps reconnecting (ADR-23 storm). reset() does NOT fire here
    // because the runId is unchanged.
    const store = useEventsStore()
    const factory = freshFactory()
    await store.open('run-2c', 'paused', {
      streamOptions: { eventSourceFactory: factory },
    })
    const paused = FakeEventSource.instances[0]!
    expect(paused.closed).toBe(false)

    await store.open('run-2c', 'running', {
      streamOptions: { eventSourceFactory: factory },
    })
    // Prior (paused) stream closed; exactly one fresh stream open.
    expect(paused.closed).toBe(true)
    expect(FakeEventSource.instances.length).toBe(2)
    expect(FakeEventSource.instances[1]!.closed).toBe(false)
  })

  it('SSE events are deduped and ordered by seq; lastSeq tracked', async () => {
    const store = useEventsStore()
    await store.open('run-3', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!
    es.emit('iter_started', '{}', '5')
    es.emit('assistant_text', '{"text":"hi"}', '3')
    es.emit('iter_started', '{}', '5') // duplicate seq — ignored
    es.emit('tool_use_start', '{}', '8')

    expect(store.events.map((e) => e.seq)).toEqual([3, 5, 8])
    expect(store.lastSeq).toBe(8)
    expect(store.currentLastEventId).toBe('8')
  })

  it('on error for a now-terminal run the stream is closed (no storm)', async () => {
    vi.useFakeTimers()
    const store = useEventsStore()
    await store.open('run-4', 'running', {
      streamOptions: {
        eventSourceFactory: freshFactory(),
        reconnectDelayMs: 10,
      },
    })
    const es0 = FakeEventSource.instances[0]!

    // View observed the run went terminal → defuse the stream.
    store.markTerminal()
    expect(es0.closed).toBe(true)
    expect(store.mode).toBe('replay')

    // A subsequent transport error must NOT spawn a reconnect.
    es0.emitError()
    vi.advanceTimersByTime(1000)
    expect(FakeEventSource.instances.length).toBe(1)
    vi.useRealTimers()
  })

  it('invalidations are COALESCED across a burst of events', async () => {
    const invalidate = vi.fn()
    const onLifecycle = vi.fn()
    const store = useEventsStore()
    await store.open('run-5', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
      invalidate,
      onLifecycle,
    })
    const es = FakeEventSource.instances[0]!
    // 50 lifecycle-relevant events in one microtask turn.
    for (let i = 1; i <= 50; i++) {
      es.emit('iter_started', '{}', String(i))
    }
    expect(invalidate).not.toHaveBeenCalled() // still armed, not fired
    await Promise.resolve() // let the trailing microtask run

    // Exactly one coalesced flush: 4 invalidate keys + 1 lifecycle ping
    // (14c added the artifacts prefix so `artifact_edited` drops the
    // editor's loaded baseline; the broadened set fires for every
    // lifecycle event, not just artifact_edited).
    expect(invalidate).toHaveBeenCalledTimes(4)
    expect(invalidate).toHaveBeenCalledWith(['runs', 'detail', 'run-5'])
    expect(invalidate).toHaveBeenCalledWith(['runs'])
    expect(invalidate).toHaveBeenCalledWith(['runs', 'children', 'run-5'])
    expect(invalidate).toHaveBeenCalledWith(['artifacts', 'run-5'])
    expect(onLifecycle).toHaveBeenCalledTimes(1)
  })

  it('non-lifecycle chatter does NOT invalidate', async () => {
    const invalidate = vi.fn()
    const store = useEventsStore()
    await store.open('run-6', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
      invalidate,
    })
    const es = FakeEventSource.instances[0]!
    es.emit('assistant_text', '{"text":"x"}', '1')
    es.emit('tool_use_start', '{}', '2')
    es.emit('tool_use_end', '{}', '3')
    await Promise.resolve()
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('invalidates runChildren key on subagent_dispatch', async () => {
    const invalidate = vi.fn()
    const store = useEventsStore()
    await store.open('run-1', 'awaiting_children', {
      invalidate,
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!

    es.emit(
      'subagent_dispatch',
      JSON.stringify({ child_run_id: 'child-a', role: 'explorer', prompt: 'x' }),
      '1',
    )

    // Coalesced — flush the microtask queue.
    await Promise.resolve()
    await Promise.resolve()

    // The arming fires three keys: ['runs', 'detail', runId], ['runs'],
    // and (new in 9e) ['runs', 'children', runId].
    const calls = invalidate.mock.calls.map((c) => c[0])
    expect(calls).toContainEqual(['runs', 'children', 'run-1'])
  })

  it('also invalidates on subagent_return and child_runs_resolved', async () => {
    const invalidate = vi.fn()
    const store = useEventsStore()
    await store.open('run-1', 'awaiting_children', {
      invalidate,
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!

    es.emit(
      'subagent_return',
      JSON.stringify({ child_run_id: 'child-a', status: 'done', summary: 's' }),
      '1',
    )
    es.emit(
      'child_runs_resolved',
      JSON.stringify({ children_count: 1, terminal_statuses: { 'child-a': 'done' } }),
      '2',
    )

    await Promise.resolve()
    await Promise.resolve()

    const calls = invalidate.mock.calls.map((c) => c[0])
    const childrenInvalidations = calls.filter(
      (k) => Array.isArray(k) && k[0] === 'runs' && k[1] === 'children',
    )
    expect(childrenInvalidations.length).toBeGreaterThanOrEqual(1)
  })

  // Bug 2 regression: child_runs_resolved (9a) and harness_session_ended
  // (ADR-39) were added to INVALIDATING_KINDS but not to the SSE
  // wrapper's KNOWN_EVENT_TYPES — so the browser EventSource's named-
  // event listeners were never registered for them, and live events
  // arriving with `event: child_runs_resolved` / `event:
  // harness_session_ended` were silently dropped by the browser. The
  // backend taxonomy + the live timeline must agree: every kind that
  // the backend can emit must reach the store via the wrapper.
  it('delivers child_runs_resolved live (was silently dropped — Bug 2)', async () => {
    const store = useEventsStore()
    await store.open('run-cr', 'awaiting_children', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!
    es.emit(
      'child_runs_resolved',
      JSON.stringify({ children_count: 2, terminal_statuses: {} }),
      '7',
    )
    expect(store.events.map((e) => e.kind)).toContain('child_runs_resolved')
    expect(store.lastSeq).toBe(7)
  })

  // 14c — `artifact_edited` (ADR-40) MUST be wired BOTH in the SSE
  // wrapper's KNOWN_EVENT_TYPES (so the browser EventSource named-event
  // listener fires) AND in INVALIDATING_KINDS (so the artifacts cache
  // drops). This isolating case emits ONLY the kind under test so the
  // assertion target cannot be a sibling kind — same shape as the Bug-2
  // regression cases above.
  it('delivers artifact_edited live and invalidates the artifacts cache', async () => {
    const invalidate = vi.fn()
    const store = useEventsStore()
    await store.open('run-ae', 'paused', {
      streamOptions: { eventSourceFactory: freshFactory() },
      invalidate,
    })
    const es = FakeEventSource.instances[0]!
    es.emit(
      'artifact_edited',
      JSON.stringify({
        path: 'improvement-plan.md',
        size_before: 11,
        size_after: 14,
        sha256_before: 'a3f2',
        sha256_after: '9b1e',
        editor: 'dashboard',
      }),
      '3',
    )
    expect(store.events.map((e) => e.kind)).toContain('artifact_edited')
    expect(store.lastSeq).toBe(3)

    await Promise.resolve()
    const calls = invalidate.mock.calls.map((c) => c[0])
    expect(calls).toContainEqual(['artifacts', 'run-ae'])
  })

  it('delivers harness_session_ended live (was silently dropped — Bug 2)', async () => {
    const store = useEventsStore()
    await store.open('run-hse', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!
    es.emit(
      'harness_session_ended',
      JSON.stringify({ stop_reason: 'clean', messages: [], summary: null }),
      '4',
    )
    expect(store.events.map((e) => e.kind)).toContain('harness_session_ended')
    expect(store.lastSeq).toBe(4)
  })

  // ADR-45 Plan A: heartbeat is an ephemeral liveness ping. It MUST
  // be wired into KNOWN_EVENT_TYPES (so the browser EventSource fires
  // the listener) but it MUST NOT be ingested into the timeline event
  // list, MUST NOT update lastSeq (the SSE id: line is intentionally
  // omitted on the wire so the browser's Last-Event-ID cursor is
  // preserved across heartbeats), and MUST NOT trigger Colada
  // invalidations. It feeds a separate `lastHeartbeat` field used by
  // the run-detail liveness widget.
  it('routes heartbeat to lastHeartbeat, not events/lastSeq/invalidations', async () => {
    const invalidate = vi.fn()
    const onLifecycle = vi.fn()
    const store = useEventsStore()
    await store.open('run-hb', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
      invalidate,
      onLifecycle,
    })
    const es = FakeEventSource.instances[0]!

    // First a real persisted event so lastSeq starts at 5.
    es.emit(
      'iter_started',
      JSON.stringify({
        seq: 5,
        kind: 'iter_started',
        payload: { seq: 1 },
        ts: '2026-05-25T09:00:00+00:00',
        run_id: 'run-hb',
        iter_id: 5,
      }),
      '5',
    )
    await Promise.resolve()
    expect(store.lastSeq).toBe(5)
    expect(store.events.length).toBe(1)
    // The priming iter_started event legitimately invalidates — reset
    // the spies so the next assertion isolates the heartbeat's
    // (non-)behaviour.
    invalidate.mockClear()
    onLifecycle.mockClear()

    // Now a heartbeat. The real browser leaves MessageEvent.lastEventId
    // at the prior value ('5') when the frame had no id: line — the
    // FakeEventSource mirrors that here.
    es.emit(
      'heartbeat',
      JSON.stringify({
        run_id: 'run-hb',
        server_ts: '2026-05-25T09:00:07+00:00',
        last_event_ts: '2026-05-25T09:00:00+00:00',
      }),
      '5',
    )
    await Promise.resolve()

    // Timeline UNCHANGED.
    expect(store.events.length).toBe(1)
    expect(store.lastSeq).toBe(5)
    // Liveness clock updated.
    expect(store.lastHeartbeat).toBeTruthy()
    expect(store.lastHeartbeat!.serverTs).toBe('2026-05-25T09:00:07+00:00')
    expect(store.lastHeartbeat!.lastEventTs).toBe('2026-05-25T09:00:00+00:00')
    expect(typeof store.lastHeartbeat!.receivedAt).toBe('number')
    // No cache invalidations.
    expect(invalidate).not.toHaveBeenCalled()
    expect(onLifecycle).not.toHaveBeenCalled()
  })

  // ADR-46 Plan B: assistant text/thinking deltas are ephemeral —
  // they accumulate into a per-(iter, turn, kind) "pending" buffer
  // so TimelinePane can render an in-progress row, but they MUST NOT
  // enter the canonical events list (the persisted AssistantText at
  // turn end is the source of truth). When that canonical row
  // arrives, the matching pending entry is dropped (otherwise the
  // pending row and the real row would both render).
  it('accumulates assistant_delta into pendingTurns and drops on canonical text', async () => {
    const store = useEventsStore()
    await store.open('run-d', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!

    // Two text deltas for (iter=20, turn=1).
    es.emit(
      'assistant_delta',
      JSON.stringify({
        iter_id: 20,
        turn_seq: 1,
        delta_seq: 1,
        text: 'hel',
        kind: 'text',
      }),
      '5',
    )
    es.emit(
      'assistant_delta',
      JSON.stringify({
        iter_id: 20,
        turn_seq: 1,
        delta_seq: 2,
        text: 'lo',
        kind: 'text',
      }),
      '5',
    )

    // Timeline UNCHANGED.
    expect(store.events.length).toBe(0)
    expect(store.lastSeq).toBe(0)

    const pending = store.pendingTurns
    expect(pending.length).toBe(1)
    expect(pending[0]!.iterId).toBe(20)
    expect(pending[0]!.turnSeq).toBe(1)
    expect(pending[0]!.kind).toBe('text')
    expect(pending[0]!.text).toBe('hello')

    // A thinking delta for the SAME (iter, turn) opens a SECOND
    // pending entry (thinking and text are distinct flushes).
    es.emit(
      'assistant_delta',
      JSON.stringify({
        iter_id: 20,
        turn_seq: 1,
        delta_seq: 3,
        text: 'reasoning',
        kind: 'thinking',
      }),
      '5',
    )
    expect(store.pendingTurns.length).toBe(2)

    // Canonical assistant_text for (iter=20, turn=1, text) — drop
    // the text pending entry. The thinking pending entry survives
    // until its own canonical flush arrives.
    es.emit(
      'assistant_text',
      JSON.stringify({
        seq: 6,
        kind: 'assistant_text',
        payload: { text: 'hello', turn_seq: 1, kind: 'text' },
        ts: '2026-05-25T10:00:00+00:00',
        run_id: 'run-d',
        iter_id: 20,
      }),
      '6',
    )
    const remaining = store.pendingTurns
    expect(remaining.length).toBe(1)
    expect(remaining[0]!.kind).toBe('thinking')
  })

  it('iter_ended clears all pending turns for that iter', async () => {
    const store = useEventsStore()
    await store.open('run-d2', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!

    es.emit(
      'assistant_delta',
      JSON.stringify({
        iter_id: 5,
        turn_seq: 1,
        delta_seq: 1,
        text: 'stuck',
        kind: 'thinking',
      }),
      '0',
    )
    expect(store.pendingTurns.length).toBe(1)

    // Iter ended without a canonical thinking flush (interrupted
    // turn). Pending must be dropped so the new iter starts clean.
    es.emit(
      'iter_ended',
      JSON.stringify({
        seq: 9,
        kind: 'iter_ended',
        payload: { exit_reason: 'cancelled' },
        ts: '2026-05-25T10:01:00+00:00',
        run_id: 'run-d2',
        iter_id: 5,
      }),
      '9',
    )
    expect(store.pendingTurns.length).toBe(0)
  })

  // Regression for the live-vs-replay payload-shape divergence (the
  // "tool cards are empty until you refresh" bug from 2026-05-25).
  //
  // The SSE `data:` body is the FULL envelope the broadcaster publishes
  // — `{seq, kind, payload, ts, run_id, iter_id}` (api/events.py:_frame
  // + _event_payload). The REST replay path correctly unwraps `r.payload`
  // before ingesting. The live path here must do the same — otherwise
  // every renderer that reads `event.payload.<field>` sees `undefined`
  // (e.g. ToolCallCard's name/args), and the "generic" renderer dumps
  // the whole envelope as JSON. Replay rendered fine; live did not.
  it('unwraps the SSE envelope so live payload matches REST replay', async () => {
    const store = useEventsStore()
    await store.open('run-env', 'running', {
      streamOptions: { eventSourceFactory: freshFactory() },
    })
    const es = FakeEventSource.instances[0]!
    // What the server actually sends on the wire:
    es.emit(
      'tool_use_start',
      JSON.stringify({
        seq: 5,
        kind: 'tool_use_start',
        payload: { tool_id: 'tu-1', name: 'read', args: { path: '/x' } },
        ts: '2026-05-25T08:33:42',
        run_id: 'run-env',
        iter_id: 20,
      }),
      '5',
    )

    const row = store.events.find((e) => e.seq === 5)
    expect(row).toBeDefined()
    expect(row!.kind).toBe('tool_use_start')
    // INNER payload, not the envelope:
    expect(row!.payload.name).toBe('read')
    expect(row!.payload.tool_id).toBe('tu-1')
    expect(row!.payload.args).toEqual({ path: '/x' })
    // Envelope keys must NOT leak into the payload:
    expect(row!.payload.seq).toBeUndefined()
    expect(row!.payload.ts).toBeUndefined()
    expect(row!.payload.run_id).toBeUndefined()
  })
})
