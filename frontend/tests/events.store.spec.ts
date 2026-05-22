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
})
