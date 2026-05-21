// The open run's event stream — the load-bearing correctness of the
// Phase-4 vertical slice (W4).
//
// Spec §9.2/§9.3 + ADR-23: the dashboard treats LIVE and HISTORICAL the
// same — one ordered event list, rendered identically; there is no
// "replay toggle". HOW the list is populated depends on the run's
// status at open time:
//
//   • status ∈ {done, failed, cancelled}  → REPLAY. The run is finished
//     and immutable. We fetch the events over the paginated REST
//     endpoint and render statically. We do NOT open SSE. (Native
//     EventSource cannot distinguish a clean finished-run EOF/204 from a
//     transient transport error, so opening SSE on a finished run risks
//     a reconnect-storm via the W1 wrapper's error→reconnect. Avoid the
//     storm by never opening it for a terminal run.)
//
//   • status ∈ {running, paused}          → LIVE. We open the W1
//     `RunEventStream`. The server itself does subscribe→replay→cutover
//     (ADR-23), so the client just consumes. On the terminal
//     `run_ended` event the wrapper auto-stops; we then refetch the run
//     detail (status is now terminal) and stop. A genuine transient
//     transport error is fine — the wrapper reconnects with backoff
//     from `currentLastEventId` (gap-free, survives a tab sleep). But
//     if a status refetch ever shows the run is now terminal we
//     `close()` the wrapper so a finished-run EOF storm is impossible.
//
// `paused` is NOT terminal (ADR-23) — a paused run can resume, so it
// stays on the LIVE path with the stream open.
//
// SSE → cache invalidation: relevant events (iter/status/run-ended)
// invalidate the broadest affected Colada keys (`keys.runDetail(runId)`
// so the header/iters refetch, `keys.runs()` so the hub/project run
// lists refresh). A fast pi run can emit hundreds of events/sec, so
// invalidations are COALESCED: each relevant event arms a single
// trailing microtask (queueMicrotask) that fires the two invalidations
// at most once per microtask turn, no matter how many events landed in
// it. This bounds invalidations to O(turns) not O(events).
//
// This is ephemeral push-stream state, intentionally a Pinia store and
// NOT in Colada: SSE is not a cacheable REST resource (spec §9.2). Run
// *detail* stays in Colada via `useRunDetailQuery`.

import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { api } from '@/api/client'
import {
  RunEventStream,
  type RelaySseEvent,
  type RunEventStreamOptions,
} from '@/api/sse'
import type { EventRow } from '@/lib/queries'

/**
 * Run statuses that mean the run is finished and immutable.
 *
 * `paused` and `awaiting_children` are deliberately NOT terminal —
 * both can transition back to `running` (pause/resume; fanout/join
 * respectively). Adding either here would force the live SSE path to
 * close (replay mode), so a parent waiting for its children would
 * stop receiving the eventual `child_runs_resolved` + synthesizer-iter
 * events. See ADR-34 / `docs/spec.md` §3.1.
 */
const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled'])

/**
 * Relay event kinds (spec §3.2) whose arrival should refresh the
 * Colada-cached run detail / run lists. Pure within-iter chatter
 * (`assistant_text`, `tool_use_*`) does NOT change the run/iter rows, so
 * it is intentionally excluded — only lifecycle transitions invalidate.
 */
const INVALIDATING_KINDS = new Set([
  'run_started',
  'iter_started',
  'iter_ended',
  'signal_emit',
  'pause_requested',
  'pause_resolved',
  'run_ended',
])

/** A normalized event in the unified (live ⨮ replayed) list. */
export interface StreamEvent {
  /** Monotonic per-run sequence — the dedupe/order key. */
  seq: number
  /** Relay event kind (spec §3.2), e.g. `tool_use_start`. */
  kind: string
  /** Parsed JSON payload (best-effort; `{}` if unparseable). */
  payload: Record<string, unknown>
}

/** How the current list was populated (purely informational for UI). */
export type StreamMode = 'idle' | 'replay' | 'live'

function parsePayload(raw: string): Record<string, unknown> {
  if (raw === '') return {}
  try {
    const v: unknown = JSON.parse(raw)
    return v != null && typeof v === 'object'
      ? (v as Record<string, unknown>)
      : {}
  } catch {
    // A malformed payload must not break the timeline; surface the raw
    // text so the row is still inspectable.
    return { _raw: raw }
  }
}

/**
 * Options passed to `open()` — only used by tests to inject a fake
 * EventSource into the underlying W1 wrapper.
 */
export interface OpenOptions {
  /** Forwarded to `RunEventStream` (test EventSource injection). */
  streamOptions?: RunEventStreamOptions
  /**
   * Invalidation sink. The view passes `useInvalidate()` here (it must
   * be resolved in a component setup scope). Called, coalesced, with
   * each broad key prefix to refetch.
   */
  invalidate?: (key: readonly (string | number | object)[]) => unknown
  /**
   * Called (coalesced, alongside invalidations) when a lifecycle event
   * suggests the run's status may have changed; the view uses it to
   * refetch run detail and react to terminal/paused transitions.
   */
  onLifecycle?: () => void
}

export const useEventsStore = defineStore('run-events', () => {
  // shallowRef: the array identity changes on every append (we replace
  // it) so reactivity fires, but we don't deep-track thousands of rows.
  const events = shallowRef<StreamEvent[]>([])
  const mode = ref<StreamMode>('idle')
  /** Highest seq seen so far (drives the live-tail parity check). */
  const lastSeq = ref<number>(0)
  /** True while a replay REST fetch is in flight. */
  const loading = ref(false)
  /** Set if the replay fetch failed. */
  const error = ref<unknown>(null)

  let stream: RunEventStream | null = null
  let openRunId: string | null = null
  const seenSeqs = new Set<number>()
  let invalidateFn:
    | ((key: readonly (string | number | object)[]) => unknown)
    | null = null
  let onLifecycleFn: (() => void) | null = null
  let invalidationArmed = false

  /** Count of rows actually in the list — the parity-check observable. */
  const renderedCount = computed(() => events.value.length)

  /** The current last-seen seq as a string (SSE reconnect cursor). */
  const currentLastEventId = computed(() =>
    lastSeq.value > 0 ? String(lastSeq.value) : null,
  )

  function reset(): void {
    if (stream) {
      stream.close()
      stream = null
    }
    events.value = []
    seenSeqs.clear()
    mode.value = 'idle'
    lastSeq.value = 0
    loading.value = false
    error.value = null
    openRunId = null
    invalidateFn = null
    onLifecycleFn = null
    invalidationArmed = false
  }

  /**
   * Insert events keeping the list ordered by seq ascending and deduped
   * (the server replays at/after the cursor, so a reconnect legitimately
   * re-delivers the boundary event — dedupe by seq makes that safe).
   */
  function ingest(rows: StreamEvent[]): void {
    let changed = false
    const next = events.value.slice()
    for (const r of rows) {
      if (seenSeqs.has(r.seq)) continue
      seenSeqs.add(r.seq)
      next.push(r)
      if (r.seq > lastSeq.value) lastSeq.value = r.seq
      changed = true
    }
    if (!changed) return
    // Oldest → newest. Events nearly always arrive in order, so this
    // sort is O(n) in practice; correctness over a micro-optimisation.
    next.sort((a, b) => a.seq - b.seq)
    events.value = next
  }

  /**
   * Coalesced invalidation: many events in one microtask turn collapse
   * to a single pair of invalidate calls + one lifecycle ping. This is
   * the reconnect/fast-pi-run storm guard for the cache layer.
   */
  function armInvalidation(): void {
    if (invalidationArmed) return
    invalidationArmed = true
    queueMicrotask(() => {
      invalidationArmed = false
      if (openRunId == null) return
      if (invalidateFn) {
        invalidateFn(['runs', 'detail', openRunId])
        invalidateFn(['runs'])
      }
      if (onLifecycleFn) onLifecycleFn()
    })
  }

  function onSseEvent(ev: RelaySseEvent): void {
    const seqNum = ev.lastEventId != null ? Number(ev.lastEventId) : NaN
    if (!Number.isFinite(seqNum)) return
    ingest([
      { seq: seqNum, kind: ev.type, payload: parsePayload(ev.data) },
    ])
    if (INVALIDATING_KINDS.has(ev.type)) armInvalidation()
  }

  /**
   * REPLAY path: page through the persisted events over REST and render
   * statically. No SSE is opened. Used for a terminal run.
   */
  async function loadReplay(runId: string): Promise<void> {
    mode.value = 'replay'
    loading.value = true
    error.value = null
    try {
      const limit = 500
      let offset = 0
      // Page until a short page: the endpoint is offset-paginated.
      for (;;) {
        const res = await api.GET('/api/runs/{run_id}/events', {
          params: {
            path: { run_id: runId },
            query: { after_seq: 0, limit, offset },
          },
        })
        if (res.error !== undefined) throw res.error
        const rows: EventRow[] = res.data?.events ?? []
        ingest(
          rows.map((r) => ({
            seq: r.seq,
            kind: r.kind,
            payload: r.payload as Record<string, unknown>,
          })),
        )
        if (rows.length < limit) break
        offset += limit
      }
    } catch (e) {
      error.value = e
    } finally {
      loading.value = false
    }
  }

  /**
   * LIVE path: open the W1 wrapper. On `run_ended` the wrapper
   * auto-stops; we ping the lifecycle hook so the view refetches the
   * (now terminal) run detail. `markTerminal()` lets the view defuse
   * the wrapper if a status refetch shows the run finished while the
   * stream was mid-flight (storm guard).
   */
  function openLive(runId: string, options: OpenOptions): void {
    mode.value = 'live'
    stream = new RunEventStream(
      runId,
      options.streamOptions ?? {},
      currentLastEventId.value,
    )
    stream.onEvent(onSseEvent)
    stream.onEnd(() => {
      // Terminal `run_ended` seen — refresh detail so the view sees the
      // final status and tears down the live UI.
      if (onLifecycleFn) onLifecycleFn()
    })
    stream.start()
  }

  /**
   * Orchestrate REPLAY-vs-LIVE for a run given its current status.
   * Caller (the view) fetches run detail FIRST (Colada) and passes the
   * status here. Returns once the initial population strategy is chosen
   * (replay awaits the REST load; live returns immediately and streams).
   */
  async function open(
    runId: string,
    status: string,
    options: OpenOptions = {},
  ): Promise<void> {
    if (openRunId != null && openRunId !== runId) reset()
    openRunId = runId
    invalidateFn = options.invalidate ?? null
    onLifecycleFn = options.onLifecycle ?? null
    // Same-run re-open (pause→resume): a prior live stream may still be
    // open — `paused` is not terminal, so `markTerminal()` was never
    // called on it. Close it before (re)choosing a strategy, otherwise
    // `openLive` overwrites `stream` and the old EventSource is orphaned
    // (unreachable by `close()`/`markTerminal()`) → the reconnect-storm
    // failure mode ADR-23 guards against. `reset()` above already nulls
    // `stream` on a run change, so this only bites the same-run path.
    if (stream) {
      stream.close()
      stream = null
    }
    if (TERMINAL_STATUSES.has(status)) {
      await loadReplay(runId)
      return
    }
    openLive(runId, options)
  }

  /**
   * Storm guard: the view calls this after a run-detail refetch shows a
   * terminal status. If a stream is still open it is force-closed so a
   * finished-run EOF cannot trigger the wrapper's error→reconnect loop.
   */
  function markTerminal(): void {
    if (stream) {
      stream.close()
      stream = null
    }
    mode.value = 'replay'
  }

  return {
    events,
    mode,
    lastSeq,
    loading,
    error,
    renderedCount,
    currentLastEventId,
    open,
    markTerminal,
    reset,
    // Exposed for tests/debug only.
    _ingest: ingest,
  }
})
