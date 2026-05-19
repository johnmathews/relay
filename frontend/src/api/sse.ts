// Framework-agnostic SSE wrapper for the relay run event stream.
//
// Backend contract (docs/api.md, docs/spec.md §9.2/§9.3, ADR-23):
//   - Endpoint: GET /api/events/{run_id}, content-type text/event-stream.
//   - SSE `id:` field == event `seq`. The browser exposes it as
//     `MessageEvent.lastEventId`.
//   - Reconnect resumes from the last seen seq. Native EventSource sends
//     the `Last-Event-ID` *header* automatically, but JS cannot set that
//     header and (more importantly) we manage the lifecycle ourselves,
//     so we pass the cursor as the `?last_event_id=` query param — an
//     explicitly supported fallback (api.md / ADR-23).
//   - A `run_ended` event closes the stream; for a finished run the
//     server streams history then EOFs (clean close). In BOTH cases we
//     must STOP — do not auto-reconnect (ADR-23 / spec §9.3: the
//     dashboard treats live and historical the same; a finished/closed
//     stream is terminal).
//
// Native EventSource auto-reconnects on transport error. We do NOT rely
// on that alone: we own the EventSource instance, and on every (re)open
// we rebuild the URL with the current cursor. A transport `error` while
// the stream is still considered live triggers our own reconnect with a
// small backoff; a terminal condition tears everything down.

/** A parsed relay SSE message. `data` is the raw event payload string. */
export interface RelaySseEvent {
  /** The SSE event name (relay event type), e.g. "iter_start". */
  type: string
  /** The SSE `id:` value == event `seq`, or null if absent. */
  lastEventId: string | null
  /** Raw `data:` payload (JSON string per the backend). */
  data: string
}

/** Minimal structural type for the parts of EventSource we use. */
export interface EventSourceLike {
  addEventListener(
    type: string,
    listener: (ev: MessageEvent) => void,
  ): void
  close(): void
}

/** Factory so tests can inject a fake EventSource. */
export type EventSourceFactory = (url: string) => EventSourceLike

export interface RunEventStreamOptions {
  /** The relay event type that terminates the stream. */
  terminalEventType?: string
  /** Reconnect backoff in ms after a transport error. */
  reconnectDelayMs?: number
  /** Injectable EventSource constructor (defaults to global). */
  eventSourceFactory?: EventSourceFactory
}

const DEFAULT_TERMINAL_EVENT = 'run_ended'
const DEFAULT_RECONNECT_DELAY_MS = 1000

// Relay event types are delivered as named SSE events. We listen on the
// generic 'message' plus the known named events. Listening to a fixed
// allowlist is robust and dependency-free.
//
// These are the EXACT relay `events.kind` discriminator values the
// backend emits (spec §3.2 event taxonomy; verified against
// `src/relay_v2/events.py`, `core.py`, `orchestrator/loop.py`). W4
// corrected this list from W1's placeholder names (`iter_start`,
// `assistant_message`, `run_status`) which did not match the backend
// and would have silently dropped every iter/text/signal event.
const KNOWN_EVENT_TYPES = [
  'message',
  'run_started',
  'run_ended',
  'iter_started',
  'iter_ended',
  'assistant_text',
  'tool_use_start',
  'tool_use_end',
  'signal_emit',
  'subagent_dispatch',
  'subagent_return',
  'pause_requested',
  'pause_resolved',
  'error',
] as const

/**
 * Opens (and transparently re-opens) an SSE connection to a run's event
 * stream, tracking the last seen seq for gap-free reconnect, and stops
 * permanently on the terminal event or a clean stream end.
 */
export class RunEventStream {
  private readonly runId: string
  private readonly terminalEventType: string
  private readonly reconnectDelayMs: number
  private readonly makeEventSource: EventSourceFactory

  private es: EventSourceLike | null = null
  private lastEventId: string | null
  private closed = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  private readonly eventCbs: Array<(ev: RelaySseEvent) => void> = []
  private readonly errorCbs: Array<(err: unknown) => void> = []
  private readonly endCbs: Array<() => void> = []

  constructor(
    runId: string,
    options: RunEventStreamOptions = {},
    initialLastEventId: string | null = null,
  ) {
    this.runId = runId
    this.terminalEventType =
      options.terminalEventType ?? DEFAULT_TERMINAL_EVENT
    this.reconnectDelayMs =
      options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS
    this.lastEventId = initialLastEventId
    const factory =
      options.eventSourceFactory ??
      ((url: string): EventSourceLike =>
        new EventSource(url) as unknown as EventSourceLike)
    this.makeEventSource = factory
  }

  /** Register an event callback. */
  onEvent(cb: (ev: RelaySseEvent) => void): void {
    this.eventCbs.push(cb)
  }

  /** Register a transport-error callback (informational; we recover). */
  onError(cb: (err: unknown) => void): void {
    this.errorCbs.push(cb)
  }

  /** Register a callback fired once when the stream ends terminally. */
  onEnd(cb: () => void): void {
    this.endCbs.push(cb)
  }

  /** The last seq seen so far (drives reconnect). */
  get currentLastEventId(): string | null {
    return this.lastEventId
  }

  /** Open the stream. Safe to call once; reconnects are automatic. */
  start(): void {
    if (this.closed) return
    this.open()
  }

  private buildUrl(): string {
    const base = `/api/events/${encodeURIComponent(this.runId)}`
    if (this.lastEventId != null && this.lastEventId !== '') {
      return `${base}?last_event_id=${encodeURIComponent(this.lastEventId)}`
    }
    return base
  }

  private open(): void {
    if (this.closed) return
    const es = this.makeEventSource(this.buildUrl())
    this.es = es

    const onMessage = (ev: MessageEvent): void => {
      if (this.closed) return
      if (ev.lastEventId != null && ev.lastEventId !== '') {
        this.lastEventId = ev.lastEventId
      }
      const type = ev.type === 'message' ? 'message' : ev.type
      const relayEvent: RelaySseEvent = {
        type,
        lastEventId: ev.lastEventId ?? null,
        data: typeof ev.data === 'string' ? ev.data : String(ev.data ?? ''),
      }
      for (const cb of this.eventCbs) cb(relayEvent)

      if (type === this.terminalEventType) {
        // Terminal relay event — stop permanently (ADR-23).
        this.finish()
      }
    }

    for (const t of KNOWN_EVENT_TYPES) {
      es.addEventListener(t, onMessage as (e: MessageEvent) => void)
    }

    es.addEventListener('error', ((ev: MessageEvent): void => {
      if (this.closed) return
      for (const cb of this.errorCbs) cb(ev)
      // A native EventSource fires 'error' both on a transient transport
      // hiccup AND on a clean server EOF (finished-run replay close). We
      // cannot distinguish them structurally here, so we attempt a
      // bounded reconnect; if the run is finished the server returns
      // 204 / immediate EOF and the stream naturally stops once the
      // terminal event has been seen (handled above) or remains idle.
      this.scheduleReconnect()
    }) as (e: MessageEvent) => void)
  }

  private scheduleReconnect(): void {
    if (this.closed) return
    // Tear down the current source before reopening with the updated
    // cursor so we never run two EventSources concurrently.
    this.teardownEs()
    if (this.reconnectTimer != null) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (this.closed) return
      this.open()
    }, this.reconnectDelayMs)
  }

  private teardownEs(): void {
    if (this.es) {
      this.es.close()
      this.es = null
    }
  }

  /** Internal terminal stop (terminal event or explicit close). */
  private finish(): void {
    if (this.closed) return
    this.closed = true
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.teardownEs()
    for (const cb of this.endCbs) cb()
  }

  /** Caller-initiated teardown; idempotent. Does not fire onEnd twice. */
  close(): void {
    if (this.closed) {
      this.teardownEs()
      return
    }
    this.closed = true
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.teardownEs()
  }
}

/** Convenience factory. */
export function openRunEventStream(
  runId: string,
  options?: RunEventStreamOptions,
  initialLastEventId?: string | null,
): RunEventStream {
  const s = new RunEventStream(runId, options, initialLastEventId ?? null)
  s.start()
  return s
}
