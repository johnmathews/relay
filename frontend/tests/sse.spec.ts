import { describe, it, expect, vi } from 'vitest'
import {
  RunEventStream,
  type EventSourceLike,
} from '../src/api/sse'

/** A controllable fake EventSource for tests. */
class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
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
  }

  /** Test helper: emit a named SSE event. */
  emit(type: string, data: string, lastEventId: string): void {
    const ev = { type, data, lastEventId } as unknown as MessageEvent
    for (const cb of this.listeners.get(type) ?? []) cb(ev)
  }

  /** Test helper: emit a transport error. */
  emitError(): void {
    const ev = { type: 'error' } as unknown as MessageEvent
    for (const cb of this.listeners.get('error') ?? []) cb(ev)
  }
}

function freshFactory(): (url: string) => EventSourceLike {
  FakeEventSource.instances = []
  return (url: string) => new FakeEventSource(url)
}

describe('RunEventStream', () => {
  it('surfaces events via onEvent and tracks lastEventId', () => {
    const stream = new RunEventStream('run-1', {
      eventSourceFactory: freshFactory(),
    })
    const seen: string[] = []
    stream.onEvent((e) => seen.push(`${e.type}:${e.lastEventId}`))
    stream.start()

    const es = FakeEventSource.instances[0]!
    es.emit('iter_started', '{"a":1}', '5')
    es.emit('tool_use_start', '{"b":2}', '6')

    expect(seen).toEqual(['iter_started:5', 'tool_use_start:6'])
    expect(stream.currentLastEventId).toBe('6')
  })

  it('reconnects with ?last_event_id=<lastId> after a transport error', () => {
    vi.useFakeTimers()
    const stream = new RunEventStream('run-2', {
      eventSourceFactory: freshFactory(),
      reconnectDelayMs: 10,
    })
    stream.start()

    const es0 = FakeEventSource.instances[0]!
    expect(es0.url).toBe('/api/events/run-2')
    es0.emit('iter_started', '{}', '42')
    es0.emitError()

    vi.advanceTimersByTime(10)

    expect(FakeEventSource.instances.length).toBe(2)
    const es1 = FakeEventSource.instances[1]!
    expect(es1.url).toBe('/api/events/run-2?last_event_id=42')
    expect(es0.closed).toBe(true)
    vi.useRealTimers()
  })

  it('stops reconnecting after the terminal run_ended event', () => {
    vi.useFakeTimers()
    const stream = new RunEventStream('run-3', {
      eventSourceFactory: freshFactory(),
      reconnectDelayMs: 10,
    })
    let ended = false
    stream.onEnd(() => {
      ended = true
    })
    stream.start()

    const es0 = FakeEventSource.instances[0]!
    es0.emit('run_ended', '{"status":"done"}', '99')

    expect(ended).toBe(true)
    expect(es0.closed).toBe(true)

    // A subsequent transport error must NOT spawn a reconnect.
    es0.emitError()
    vi.advanceTimersByTime(1000)
    expect(FakeEventSource.instances.length).toBe(1)
    vi.useRealTimers()
  })

  it('close() tears down the EventSource and prevents reconnects', () => {
    vi.useFakeTimers()
    const stream = new RunEventStream('run-4', {
      eventSourceFactory: freshFactory(),
      reconnectDelayMs: 10,
    })
    stream.start()
    const es0 = FakeEventSource.instances[0]!

    stream.close()
    expect(es0.closed).toBe(true)

    es0.emitError()
    vi.advanceTimersByTime(1000)
    expect(FakeEventSource.instances.length).toBe(1)
    vi.useRealTimers()
  })

  it('resumes from an initial lastEventId on first open', () => {
    const stream = new RunEventStream(
      'run-5',
      { eventSourceFactory: freshFactory() },
      '17',
    )
    stream.start()
    expect(FakeEventSource.instances[0]!.url).toBe(
      '/api/events/run-5?last_event_id=17',
    )
  })
})
