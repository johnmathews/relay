<script setup lang="ts">
// The run timeline: a chronological (oldest → newest) feed of the
// unified event list (live ⨮ replayed are identical — spec §9.3). Each
// relay event kind (spec §3.2) renders readably:
//   • tool_use_start/tool_use_end → paired into a ToolCallCard
//   • signal_emit                 → SignalCard (distinctive + anchor)
//   • assistant_text              → message text
//   • iter_started / iter_ended   → iter boundary rows
//   • run_started / run_ended     → run boundary rows
//   • pause_requested/_resolved   → pause rows
//   • anything else               → a generic JSON row (never throws on
//                                    an unknown future kind)
//
// VIRTUALIZATION: for runs > VIRTUAL_THRESHOLD events we window the DOM
// — only rows intersecting the scroll viewport (+ overscan) are
// rendered, with top/bottom spacer divs preserving scroll height. No
// new dependency (hand-rolled, per the W4 constraint). Below the
// threshold every row renders normally. The rendered row count is
// exposed via `data-event-count` (TOTAL) and `data-rendered-rows`
// (windowed DOM count) for the plan.md live-tail parity check.

import { computed, ref } from 'vue'
import ToolCallCard from './ToolCallCard.vue'
import SignalCard from './SignalCard.vue'
import UsageRow from './UsageRow.vue'
import type { PendingTurn, StreamEvent } from '@/stores/events'
import { useBrowserUiStore } from '@/stores/files'

const props = defineProps<{
  /** The ordered, deduped event list from the events store. */
  events: StreamEvent[]
  /**
   * Optional iter-SEQ filter (W5 Iters pane). When set, only events
   * belonging to that iter are shown. Events carry no iter foreign key,
   * so membership is derived from the `iter_started`/`iter_ended`
   * boundary events whose payloads carry `seq` (see ItersPane.vue's
   * FILTER CONTRACT). `null`/`undefined` ⇒ show all iters (default).
   */
  selectedIterSeq?: number | null
  /**
   * The run this timeline belongs to. When provided (14e), an
   * `artifact_edited` row becomes a click-target that opens the
   * artifacts pane at the file's current on-disk content via the
   * shared file browser store keyed `run:<runId>`. Optional so older
   * call-sites (and tests) need not thread it through.
   */
  runId?: string
  /**
   * ADR-46 Plan B — in-progress assistant turns (text/thinking
   * deltas accumulating live). Rendered as ephemeral pseudo-rows
   * BELOW the canonical timeline; replaced by the canonical
   * `assistant_text` event when it lands. Hidden entirely when an
   * iter filter is active (deltas only flow for the running iter;
   * showing them in a historical iter view is misleading). Default
   * `[]` keeps older call-sites unchanged.
   */
  pendingTurns?: PendingTurn[]
}>()

/**
 * Apply the W5 iter filter. We walk the (already seq-ordered) event
 * list tracking the "current iter seq" via `iter_started` (enter) /
 * `iter_ended` (exit) boundaries; an event belongs to the iter that is
 * open when it arrives. The selected iter's own boundary events are
 * kept so the filtered view still shows its start/end. Unknown future
 * kinds are unaffected. With no filter set this is the identity.
 */
const filteredEvents = computed<StreamEvent[]>(() => {
  const sel = props.selectedIterSeq
  if (sel == null) return props.events
  const out: StreamEvent[] = []
  let openIter: number | null = null
  for (const ev of props.events) {
    const evSeq =
      typeof ev.payload.seq === 'number' ? ev.payload.seq : null
    if (ev.kind === 'iter_started') {
      openIter = evSeq
      if (evSeq === sel) out.push(ev)
      continue
    }
    if (ev.kind === 'iter_ended') {
      if (evSeq === sel || openIter === sel) out.push(ev)
      openIter = null
      continue
    }
    if (openIter === sel) out.push(ev)
  }
  return out
})

/** ADR-46 Plan B: pending pseudo-rows only render in the "all iters"
 * view. Streaming deltas come from the live iter; in a historical
 * iter filter they would be misleading (and the iterId-to-payload-seq
 * mapping is one-way derivable, not two-way). */
const visiblePending = computed<PendingTurn[]>(() => {
  if (props.selectedIterSeq != null) return []
  return props.pendingTurns ?? []
})

/** Above this many events the list is windowed. */
const VIRTUAL_THRESHOLD = 1000
/** Fixed estimated row height (px) for windowing math. */
const ROW_HEIGHT = 88
/** Extra rows rendered above/below the viewport to avoid blank flashes. */
const OVERSCAN = 8

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
    | 'artifact_edited'
    | 'generic'
  /** The originating event (newest of a merged pair for tools). */
  event: StreamEvent
  /** Paired tool_use_end payload, when this is a tool row. */
  toolEnd?: Record<string, unknown>
}

/**
 * Fold the raw event list into display rows. `tool_use_end` is merged
 * into its matching `tool_use_start` (by `tool_id`) so a call+result is
 * one card; an unmatched end (rare/ordering) still renders standalone.
 */
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const toolIndex = new Map<string, number>()
  for (const ev of filteredEvents.value) {
    const p = ev.payload
    if (ev.kind === 'tool_use_start') {
      const idx = out.length
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'tool',
        event: ev,
      })
      const tid = typeof p.tool_id === 'string' ? p.tool_id : null
      if (tid != null) toolIndex.set(tid, idx)
    } else if (ev.kind === 'tool_use_end') {
      const tid = typeof p.tool_id === 'string' ? p.tool_id : null
      const at = tid != null ? toolIndex.get(tid) : undefined
      if (at != null) {
        out[at]!.toolEnd = p
      } else {
        out.push({
          key: `e${ev.seq}`,
          kind: ev.kind,
          type: 'tool',
          event: ev,
          toolEnd: p,
        })
      }
    } else if (ev.kind === 'signal_emit') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'signal',
        event: ev,
      })
    } else if (ev.kind === 'assistant_text') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'message',
        event: ev,
      })
    } else if (ev.kind === 'pause_requested' || ev.kind === 'pause_resolved') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'pause',
        event: ev,
      })
    } else if (ev.kind === 'harness_session_ended') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'usage',
        event: ev,
      })
    } else if (ev.kind === 'artifact_edited') {
      // 14c — ADR-40. One-line row: path · sha-before → sha-after ·
      // editor. No "view diff" link in v1 (proposal §OQ-6 → 14e).
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'artifact_edited',
        event: ev,
      })
    } else if (
      ev.kind === 'iter_started' ||
      ev.kind === 'iter_ended' ||
      ev.kind === 'run_started' ||
      ev.kind === 'run_ended'
    ) {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'boundary',
        event: ev,
      })
    } else {
      // Unknown / future kind — render generically, never throw.
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'generic',
        event: ev,
      })
    }
  }
  return out
})

const virtualized = computed(() => rows.value.length > VIRTUAL_THRESHOLD)

const scrollEl = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportH = ref(600)

function onScroll(): void {
  const el = scrollEl.value
  if (el == null) return
  scrollTop.value = el.scrollTop
  viewportH.value = el.clientHeight
}

// Windowed slice. Below the threshold this returns the full list (the
// math degenerates to [0, len]).
const window = computed(() => {
  if (!virtualized.value) {
    return { start: 0, end: rows.value.length, padTop: 0, padBottom: 0 }
  }
  const total = rows.value.length
  const first = Math.max(
    0,
    Math.floor(scrollTop.value / ROW_HEIGHT) - OVERSCAN,
  )
  const visible = Math.ceil(viewportH.value / ROW_HEIGHT) + OVERSCAN * 2
  const end = Math.min(total, first + visible)
  return {
    start: first,
    end,
    padTop: first * ROW_HEIGHT,
    padBottom: (total - end) * ROW_HEIGHT,
  }
})

const visibleRows = computed(() =>
  rows.value.slice(window.value.start, window.value.end),
)

function text(ev: StreamEvent): string {
  const t = ev.payload.text
  return typeof t === 'string' ? t : ''
}

function generic(ev: StreamEvent): string {
  try {
    return JSON.stringify(ev.payload)
  } catch {
    return ''
  }
}

/** Narrow an unknown payload field to a number (or undefined). */
function asNum(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined
}

/** Narrow an unknown payload field to a string with a fallback. */
function asStr(v: unknown, fallback: string): string {
  return typeof v === 'string' ? v : fallback
}

/** Shorten a sha256 hex string to the first 4 chars + ellipsis for the
 *  inline `artifact_edited` row. `null` (pre-edit hash on a create) →
 *  the literal "∅" so the row reads `∅ → 9b1e…`. */
function shortSha(v: unknown): string {
  if (v == null) return '∅'
  if (typeof v !== 'string' || v === '') return '?'
  return `${v.slice(0, 4)}…`
}

/**
 * 14e: clicking an `artifact_edited` row opens the artifacts pane at
 * the file's CURRENT on-disk state (deliberately not a historical diff
 * — ADR-40 §B1 does not preserve before-content; the row reads the
 * artifact as it exists right now). We mutate the shared file-browser
 * Pinia store keyed `run:<runId>` so `ArtifactsPane`'s `FileViewer`
 * picks up the selection, then scroll the pane into view. No-op if the
 * `runId` prop isn't provided (older call-sites / unit tests).
 */
function onArtifactEditedClick(path: string): void {
  if (props.runId == null || path === '') return
  useBrowserUiStore(`run:${props.runId}`).selectFile(path)
  if (typeof document !== 'undefined') {
    const el = document.querySelector('[data-testid="artifacts-pane"]')
    if (el != null && 'scrollIntoView' in el) {
      (el as HTMLElement).scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    }
  }
}
</script>

<template>
  <div
    ref="scrollEl"
    class="timeline"
    :data-event-count="rows.length"
    :data-rendered-rows="visibleRows.length"
    :data-virtualized="virtualized"
    @scroll="onScroll"
  >
    <p
      v-if="rows.length === 0"
      class="timeline__empty"
    >
      No events yet.
    </p>

    <div
      v-if="virtualized"
      :style="{ height: `${window.padTop}px` }"
      aria-hidden="true"
    />

    <ol class="timeline__list">
      <li
        v-for="row in visibleRows"
        :key="row.key"
        class="timeline__row"
        :data-kind="row.kind"
      >
        <span class="timeline__seq">#{{ row.event.seq }}</span>

        <ToolCallCard
          v-if="row.type === 'tool'"
          :name="asStr(row.event.payload.name, 'tool')"
          :args="row.event.payload.args"
          :result="row.toolEnd?.result"
          :is-error="row.toolEnd?.is_error === true"
          :duration-ms="asNum(row.toolEnd?.duration_ms)"
        />

        <SignalCard
          v-else-if="row.type === 'signal'"
          :seq="row.event.seq"
          :signal-kind="asStr(row.event.payload.kind, 'signal')"
          :args="row.event.payload.args"
        />

        <div
          v-else-if="row.type === 'message'"
          class="timeline__message"
        >
          <span class="timeline__label">assistant</span>
          <p class="timeline__text">
            {{ text(row.event) }}
          </p>
        </div>

        <div
          v-else-if="row.type === 'boundary'"
          class="timeline__boundary"
        >
          <span class="timeline__btag">{{ row.kind }}</span>
          <code class="timeline__bmeta">{{ generic(row.event) }}</code>
        </div>

        <div
          v-else-if="row.type === 'pause'"
          class="timeline__pause"
        >
          <span class="timeline__btag">{{ row.kind }}</span>
          <code class="timeline__bmeta">{{ generic(row.event) }}</code>
        </div>

        <UsageRow
          v-else-if="row.type === 'usage'"
          :event="row.event"
        />

        <button
          v-else-if="row.type === 'artifact_edited'"
          type="button"
          class="timeline__edit"
          data-testid="artifact-edited-row"
          :title="runId ? 'Open this artifact' : ''"
          @click="onArtifactEditedClick(asStr(row.event.payload.path, ''))"
        >
          <span class="timeline__edit-glyph">✎</span>
          <code class="timeline__edit-path">{{ asStr(row.event.payload.path, '?') }}</code>
          <span class="timeline__edit-sha">
            {{ shortSha(row.event.payload.sha256_before) }}
            →
            {{ shortSha(row.event.payload.sha256_after) }}
          </span>
          <span class="timeline__edit-editor">·
            {{ asStr(row.event.payload.editor, 'dashboard') }}
          </span>
        </button>

        <div
          v-else
          class="timeline__generic"
        >
          <span class="timeline__label">{{ row.kind }}</span>
          <code class="timeline__bmeta">{{ generic(row.event) }}</code>
        </div>
      </li>
    </ol>

    <div
      v-if="virtualized"
      :style="{ height: `${window.padBottom}px` }"
      aria-hidden="true"
    />

    <!-- ADR-46 Plan B — in-progress assistant turns. Live deltas
         streamed via SSE; replaced by the canonical assistant_text
         when it lands. Hidden under an iter filter (deltas only
         flow for the running iter). -->
    <ol
      v-if="visiblePending.length > 0"
      class="timeline__pending-list"
      data-testid="pending-turns"
    >
      <li
        v-for="(pt, idx) in visiblePending"
        :key="`pending:${pt.iterId}:${pt.turnSeq}:${pt.kind}:${idx}`"
        class="timeline__row timeline__row--pending"
        :data-testid="'pending-turn'"
        :data-pending-kind="pt.kind"
      >
        <span class="timeline__seq">···</span>
        <div class="timeline__message timeline__message--pending">
          <span class="timeline__label">
            {{ pt.kind === 'thinking' ? 'thinking…' : 'assistant…' }}
          </span>
          <p class="timeline__text">
            {{ pt.text }}
          </p>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.timeline {
  max-height: 70vh;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.75rem;
  background: var(--color-bg);
}

.timeline__empty {
  color: var(--color-text-dim);
  margin: 0;
}

.timeline__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.timeline__row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.timeline__seq {
  font-size: 0.7em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.timeline__label,
.timeline__btag {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.timeline__message,
.timeline__boundary,
.timeline__pause,
.timeline__generic {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.55rem 0.7rem;
  background: var(--color-surface);
}

.timeline__pause {
  border-color: #e0b341;
}

.timeline__text {
  margin: 0.2rem 0 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.timeline__bmeta {
  display: block;
  margin-top: 0.2rem;
  font-size: 0.8em;
  color: var(--color-text-dim);
  word-break: break-all;
}

.timeline__edit {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  padding: 0.25rem 0.5rem;
  font-size: 0.85em;
  color: var(--color-text-muted, #888);
  border: none;
  border-left: 2px solid var(--color-border-subtle, #e0e0e0);
  background: transparent;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  width: 100%;
}

.timeline__edit:hover {
  background: rgba(224, 179, 65, 0.07);
}

.timeline__edit:focus-visible {
  outline: 2px solid #e0b341;
  outline-offset: 1px;
}

.timeline__edit-glyph {
  font-size: 1em;
  color: #e0b341;
}

.timeline__edit-path {
  font-family: var(--font-mono);
  color: var(--color-text);
}

.timeline__edit-sha {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-dim);
}

.timeline__edit-editor {
  font-size: 0.92em;
  color: var(--color-text-dim);
}
</style>
