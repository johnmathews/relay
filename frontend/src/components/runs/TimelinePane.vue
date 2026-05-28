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

import { computed, nextTick, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import ToolCallCard from './ToolCallCard.vue'
import SignalCard from './SignalCard.vue'
import UsageRow from './UsageRow.vue'
import type { PendingTurn, StreamEvent } from '@/stores/events'
import { useBrowserUiStore } from '@/stores/files'
import {
  useTimelinePrefsStore,
  type TimelineRowType,
} from '@/stores/timelinePrefs'
import { serializeView } from '@/lib/runView'
import {
  classifyEvent,
  classifyPending,
  KIND_LABEL,
  type KindCategory,
} from '@/lib/eventKinds'

/**
 * Row types that participate in the collapse / expand workflow.
 * Boundary / pause / usage / artifact_edited rows are intrinsically
 * one-liners; collapsing them gains nothing and just adds chrome.
 */
const COLLAPSIBLE_TYPES = new Set([
  'tool',
  'signal',
  'assistant',
  'thinking',
  'generic',
])

const props = defineProps<{
  /** The ordered, deduped event list from the events store. */
  events: ReadonlyArray<StreamEvent>
  /**
   * Optional iter-SEQ filter (W5 Iters pane). When set, only events
   * belonging to that iter are shown. Events carry no iter foreign key,
   * so membership is derived from the `iter_started`/`iter_ended`
   * boundary events whose payloads carry `seq` (see ItersPane.vue's
   * FILTER CONTRACT). `null`/`undefined` ⇒ show all iters (default).
   */
  selectedIterSeq?: number | null
  /**
   * Phase 2 of the run-detail layout proposal — the chip-row
   * visibility filter. `null`/absent means "show all categories" (the
   * default), a `Set<KindCategory>` is the proper subset that stays
   * visible. Applied AFTER `selectedIterSeq` so the iter-scope filter
   * can still anchor on `iter_started`/`iter_ended` boundaries even
   * when the user has hidden the `signal` chip.
   */
  kindsFilter?: ReadonlySet<KindCategory> | null
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
  pendingTurns?: ReadonlyArray<PendingTurn>
}>()

const emit = defineEmits<{
  (e: 'clearKindsFilter'): void
}>()

const router = useRouter()
const route = useRoute()

/**
 * Apply the W5 iter filter. We walk the (already seq-ordered) event
 * list tracking the "current iter seq" via `iter_started` (enter) /
 * `iter_ended` (exit) boundaries; an event belongs to the iter that is
 * open when it arrives. The selected iter's own boundary events are
 * kept so the filtered view still shows its start/end. Unknown future
 * kinds are unaffected. With no filter set this is the identity.
 */
const iterScopedEvents = computed<StreamEvent[]>(() => {
  const sel = props.selectedIterSeq
  if (sel == null) return [...props.events]
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

/**
 * Phase 2 — chip-row visibility filter applied AFTER the iter scope.
 * Order is load-bearing: the iter-scope walk relies on `iter_started`
 * / `iter_ended` boundaries, which would themselves be filtered out
 * if we applied kinds first whenever the user has hidden the Signal
 * chip.
 */
const filteredEvents = computed<StreamEvent[]>(() => {
  const kinds = props.kindsFilter ?? null
  if (kinds == null) return iterScopedEvents.value
  return iterScopedEvents.value.filter((ev) => kinds.has(classifyEvent(ev)))
})

/** ADR-46 Plan B: pending pseudo-rows only render in the "all iters"
 * view. Streaming deltas come from the live iter; in a historical
 * iter filter they would be misleading (and the iterId-to-payload-seq
 * mapping is one-way derivable, not two-way). Also respects the
 * Phase-2 kinds filter — a hidden `assistant` chip should hide its
 * in-flight stream too. */
const visiblePending = computed<PendingTurn[]>(() => {
  if (props.selectedIterSeq != null) return []
  const all = props.pendingTurns ?? []
  const kinds = props.kindsFilter ?? null
  if (kinds == null) return [...all]
  return all.filter((pt) => kinds.has(classifyPending(pt)))
})

function onClearKindsFilter(): void {
  emit('clearKindsFilter')
}

/** Per-row chip-category. Reused by the header strip and the inline
 *  layout so the kind-colour palette is named in both. */
function categoryFor(row: Row): KindCategory {
  return classifyEvent(row.event)
}

/** Above this many events the list is windowed. */
const VIRTUAL_THRESHOLD = 1000
/** Fixed estimated row height (px) for windowing math. */
const ROW_HEIGHT = 88
/** Extra rows rendered above/below the viewport to avoid blank flashes. */
const OVERSCAN = 8

interface Row {
  /** Stable key. */
  key: string
  /** 'tool' | 'signal' | 'assistant' | 'thinking' | 'boundary' | 'pause' | 'usage' | 'artifact_edited' | 'generic'. */
  kind: Row['type'] extends never ? never : string
  /**
   * Display row category. The `assistant_text` event kind is split
   * into TWO row types — `assistant` (the `text` kind, the agent's
   * reply, expanded by default and visually highlighted) vs
   * `thinking` (the `text === 'thinking'` reasoning stream, a
   * scannable header by default per ADR-18 — never the carrier of
   * user-facing output). The split is the load-bearing
   * differentiation for the type-default expand prefs.
   */
  type:
    | 'tool'
    | 'signal'
    | 'assistant'
    | 'thinking'
    | 'boundary'
    | 'pause'
    | 'usage'
    | 'artifact_edited'
    | 'generic'
  /** The originating event (newest of a merged pair for tools). */
  event: StreamEvent
  /** Paired tool_use_end payload, when this is a tool row. */
  toolEnd?: Record<string, unknown>
  /** Pre-computed smart preview for the card header. Populated in
   *  the `rows` computed so the template doesn't re-run the
   *  per-tool string-matching logic 3× per row per render
   *  (`v-if`, `:title`, text node). Empty string for boundary /
   *  pause / usage / artifact_edited (they render their own
   *  body). */
  preview: string
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
        preview: '',
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
          preview: '',
        })
      }
    } else if (ev.kind === 'signal_emit') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'signal',
        event: ev,
        preview: '',
      })
    } else if (ev.kind === 'assistant_text') {
      // Split assistant_text by payload.kind so the type-default
      // expand prefs (and the ASSISTANT highlight) can target the
      // user-facing reply (`text`) separately from the model
      // reasoning (`thinking`). ADR-18 keeps these distinct at the
      // protocol level; the dashboard now keeps them distinct
      // visually too.
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: p.kind === 'thinking' ? 'thinking' : 'assistant',
        event: ev,
        preview: '',
      })
    } else if (ev.kind === 'pause_requested' || ev.kind === 'pause_resolved') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'pause',
        event: ev,
        preview: '',
      })
    } else if (ev.kind === 'harness_session_ended') {
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'usage',
        event: ev,
        preview: '',
      })
    } else if (ev.kind === 'artifact_edited') {
      // 14c — ADR-40. One-line row: path · sha-before → sha-after ·
      // editor. No "view diff" link in v1 (proposal §OQ-6 → 14e).
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'artifact_edited',
        event: ev,
        preview: '',
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
        preview: '',
      })
    } else {
      // Unknown / future kind — render generically, never throw.
      out.push({
        key: `e${ev.seq}`,
        kind: ev.kind,
        type: 'generic',
        event: ev,
        preview: '',
      })
    }
  }
  // Fill smart previews in a single post-pass. We do this AFTER the
  // loop so tool rows have their paired `toolEnd` attached first —
  // previewFor only reads `event.payload` (not `toolEnd`) today, but
  // the post-pass is the right place to grow if a future preview
  // wants timing/result data too. Computing once here means the
  // template uses `row.preview` instead of calling `previewFor(row)`
  // three times (`v-if`, `:title`, text) per row per render.
  for (const r of out) {
    r.preview = previewFor(r)
  }
  return out
})

const virtualized = computed(() => rows.value.length > VIRTUAL_THRESHOLD)

const scrollEl = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportH = ref(600)

/**
 * Pinned-to-bottom auto-scroll (2026-05-25 live-stream UX): a live
 * run can append events while the user is also reading history. If
 * they're at the bottom we follow the tail; if they've scrolled up
 * we leave them where they are and surface a "Jump to latest"
 * button. Tolerance of 50px accommodates sub-pixel rounding +
 * keyboard scroll increments — anything closer to the bottom than
 * that counts as "still pinned".
 */
const PIN_TOLERANCE_PX = 50
const isPinned = ref(true)

function distanceFromBottom(el: HTMLElement): number {
  return el.scrollHeight - (el.scrollTop + el.clientHeight)
}

function onScroll(): void {
  const el = scrollEl.value
  if (el == null) return
  scrollTop.value = el.scrollTop
  viewportH.value = el.clientHeight
  // A user-driven scroll updates the pin state. A programmatic
  // scrollTop assignment (our own auto-scroll) also fires this
  // handler, but since we always assign to the bottom in that case
  // distanceFromBottom ≤ 0 and the pin stays true.
  isPinned.value = distanceFromBottom(el) <= PIN_TOLERANCE_PX
}

function scrollToBottom(): void {
  const el = scrollEl.value
  if (el == null) return
  el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
}

function jumpToLatest(): void {
  isPinned.value = true
  scrollToBottom()
}

/**
 * Per-row expand override (keyed by `row.key`, which is unique per
 * event seq). Wins over the type default when set; a `null` slot
 * means "follow the type default". Lives in component state and
 * resets on remount — overrides shouldn't outlive the tab the way
 * type defaults do.
 */
const rowOverrides = ref<Record<string, boolean>>({})
const prefs = useTimelinePrefsStore()

function isCollapsible(t: Row['type']): boolean {
  return COLLAPSIBLE_TYPES.has(t)
}

function isRowExpanded(row: Row): boolean {
  if (!isCollapsible(row.type)) return true
  const ov = rowOverrides.value[row.key]
  if (typeof ov === 'boolean') return ov
  return prefs.isExpandedByDefault(row.type as TimelineRowType)
}

function toggleRow(row: Row): void {
  rowOverrides.value = {
    ...rowOverrides.value,
    [row.key]: !isRowExpanded(row),
  }
}

/**
 * The text the "copy" button puts on the clipboard. Matches what is
 * visible on the row when expanded: assistant/thinking → the text
 * body; tool → `args: …\nresult: …` JSON; signal/generic/etc. →
 * pretty-printed payload JSON. Boundaries / pause / usage rows
 * still get a copy button since the payload is sometimes useful
 * (a stop_reason or pause question is worth quoting).
 */
function getCopyText(row: Row): string {
  const ev = row.event
  const p = ev.payload
  if (row.type === 'assistant' || row.type === 'thinking') {
    return typeof p.text === 'string' ? p.text : ''
  }
  if (row.type === 'tool') {
    const args = JSON.stringify(p.args, null, 2)
    const end = row.toolEnd
    if (end == null) return `args:\n${args}`
    const result = JSON.stringify(end.result, null, 2)
    return `args:\n${args}\n\nresult:\n${result}`
  }
  if (row.type === 'signal') {
    return JSON.stringify({ kind: p.kind, args: p.args }, null, 2)
  }
  try {
    return JSON.stringify(p, null, 2)
  } catch {
    return ''
  }
}

async function copyRow(row: Row): Promise<void> {
  const text = getCopyText(row)
  if (text === '') return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // Clipboard permissions denied / unavailable (Safari private
    // mode etc.). Soft-fail — the user can fall back to manual
    // selection.
  }
}

/**
 * When new rows arrive AND the user is pinned, advance the scroll
 * position to the new tail. The scroll position is set after the
 * next tick so the DOM has reflowed and `scrollHeight` reflects the
 * appended rows (jsdom tests plant the geometry directly, so
 * nextTick is enough; real browsers do the same one-frame later).
 * Watching `rows.length` is correct even with virtualization —
 * `rows` is the FULL folded list and gates the scroll-height
 * spacers.
 */
watch(
  () => rows.value.length,
  async () => {
    if (!isPinned.value) return
    await nextTick()
    scrollToBottom()
  },
)

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

/* Card-header helpers — these drive the per-row header strip
   (#seq · glyph · name · status · duration · smart-preview) that the
   user always sees, collapsed or expanded. The preview is a per-tool
   summary so a 50-row development iter is scannable without expanding
   anything. */

const PREVIEW_MAX = 140

function truncatePreview(s: string): string {
  if (s.length <= PREVIEW_MAX) return s
  return `${s.slice(0, PREVIEW_MAX - 1)}…`
}

function glyphFor(row: Row): string {
  switch (row.type) {
    case 'tool':
      return '⚒'
    case 'signal':
      return '⚑'
    case 'assistant':
      return '▣'
    case 'thinking':
      return '◌'
    case 'generic':
      return '◇'
    default:
      return '·'
  }
}

function headerName(row: Row): string {
  const p = row.event.payload
  switch (row.type) {
    case 'tool':
      return asStr(p.name, 'tool')
    case 'signal':
      return asStr(p.kind, 'signal')
    case 'assistant':
      return 'assistant'
    case 'thinking':
      return 'thinking'
    case 'generic':
      return row.kind
    default:
      return row.kind
  }
}

type RowStatus = 'ok' | 'err' | 'pending'

function statusFor(row: Row): RowStatus | null {
  if (row.type !== 'tool') return null
  if (row.toolEnd == null) return 'pending'
  return row.toolEnd.is_error === true ? 'err' : 'ok'
}

function statusGlyphFor(row: Row): string {
  const s = statusFor(row)
  if (s === 'ok') return '✓'
  if (s === 'err') return '✗'
  if (s === 'pending') return '…'
  return ''
}

function statusTitleFor(row: Row): string {
  const s = statusFor(row)
  if (s === 'ok') return 'Completed'
  if (s === 'err') return 'Errored'
  if (s === 'pending') return 'In flight'
  return ''
}

function durationFor(row: Row): string {
  if (row.type !== 'tool') return ''
  const ms = asNum(row.toolEnd?.duration_ms)
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const sec = Math.round(ms / 1000)
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function firstLine(s: string): string {
  for (const line of s.split('\n')) {
    if (line.trim() !== '') return line.trim()
  }
  return ''
}

/** A one-line summary shown in the row header that stays visible even
 *  when the body is collapsed. Per-tool-aware: bash → `$ <command>`,
 *  read → `← <path>`, write/edit → `→ <path>`, grep → `? <pattern>`,
 *  glob → `* <pattern>`, task → description; assistant/thinking → the
 *  first non-empty line; generic → stringified payload. Returns empty
 *  string when there is nothing to summarise. */
function previewFor(row: Row): string {
  const p = row.event.payload
  if (row.type === 'tool') {
    // Pi emits tool names in either casing (`Bash` / `bash`) depending
    // on the underlying provider. Normalise to lowercase for matching;
    // the header still renders the original-cased name from the
    // payload via headerName(). Args keys vary too — bash uses
    // `command`, read/write/edit use `file_path` or `path`.
    const name = asStr(p.name, '').toLowerCase()
    const args =
      typeof p.args === 'object' && p.args !== null
        ? (p.args as Record<string, unknown>)
        : null
    if (args == null) return ''
    const argStr = (k: string): string =>
      typeof args[k] === 'string' ? (args[k] as string) : ''
    const filePath =
      argStr('file_path') || argStr('path') || argStr('filename')
    const pattern = argStr('pattern')
    if (name === 'bash') {
      const cmd = argStr('command')
      if (cmd === '') return ''
      return truncatePreview(`$ ${cmd.replace(/\s+/g, ' ').trim()}`)
    }
    if (name === 'write' || name === 'edit') {
      return filePath === '' ? '' : truncatePreview(`→ ${filePath}`)
    }
    if (name === 'read') {
      return filePath === '' ? '' : truncatePreview(`← ${filePath}`)
    }
    if (name === 'grep') {
      return pattern === '' ? '' : truncatePreview(`? ${pattern}`)
    }
    if (name === 'glob') {
      return pattern === '' ? '' : truncatePreview(`* ${pattern}`)
    }
    if (name === 'task' || name === 'agent') {
      const d = argStr('description') || argStr('prompt')
      return d === '' ? '' : truncatePreview(d)
    }
    const keys = Object.keys(args)
    if (keys.length === 0) return ''
    const k = keys[0]!
    const v = args[k]
    const vs = typeof v === 'string' ? v : JSON.stringify(v)
    return truncatePreview(`${k}: ${vs}`)
  }
  if (row.type === 'assistant' || row.type === 'thinking') {
    const t = typeof p.text === 'string' ? p.text : ''
    return truncatePreview(firstLine(t))
  }
  if (row.type === 'generic') {
    return truncatePreview(generic(row.event))
  }
  return ''
}

function onHeaderClick(row: Row): void {
  if (!isCollapsible(row.type)) return
  toggleRow(row)
}

/**
 * 14e: clicking an `artifact_edited` row opens the artifacts panel at
 * the file's CURRENT on-disk state (deliberately not a historical diff
 * — ADR-40 §B1 does not preserve before-content; the row reads the
 * artifact as it exists right now). We:
 *   1. Mutate the shared file-browser Pinia store (`run:<runId>`) so
 *      the sidebar's Artifacts section highlights the file.
 *   2. Push `?view=artifact:<path>` so the right pane opens the file
 *      viewer (Phase 1 layout — ArtifactsPane is gone, the right pane
 *      is the artifact viewer now).
 *   3. Scroll the sidebar's Artifacts section into view.
 * No-op if the `runId` prop isn't provided (older call-sites / unit tests).
 */
function onArtifactEditedClick(path: string): void {
  if (props.runId == null || path === '') return
  useBrowserUiStore(`run:${props.runId}`).selectFile(path)
  void router.push({
    query: { ...route.query, view: serializeView({ kind: 'artifact', path }) },
  })
  if (typeof document !== 'undefined') {
    const el = document.querySelector('[data-testid="sidebar-artifacts-section"]')
    if (el != null && 'scrollIntoView' in el) {
      (el as HTMLElement).scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
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
      v-if="rows.length === 0 && iterScopedEvents.length === 0"
      class="timeline__empty"
    >
      No events yet.
    </p>

    <!-- Phase 2 — distinguish "this scope is empty" (above) from
         "the kinds filter is hiding every event the scope has".
         The latter is recoverable; the parent owns the chip-row
         state so the Clear button just emits up and waits. -->
    <div
      v-else-if="rows.length === 0"
      class="timeline__empty timeline__empty--filtered"
      data-testid="timeline-all-hidden"
    >
      <p>All events hidden by filter.</p>
      <button
        type="button"
        class="timeline__empty-clear"
        data-testid="timeline-clear-kinds"
        @click="onClearKindsFilter"
      >
        Clear filter
      </button>
    </div>

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
        :class="{
          'timeline__row--card': isCollapsible(row.type),
          'timeline__row--inline': !isCollapsible(row.type),
          'timeline__row--collapsible': isCollapsible(row.type),
          'timeline__row--collapsed':
            isCollapsible(row.type) && !isRowExpanded(row),
          'timeline__row--assistant': row.type === 'assistant',
          'timeline__row--error': statusFor(row) === 'err',
        }"
        :data-kind="row.kind"
        :data-row-type="row.type"
      >
        <!-- Collapsible rows render as a bordered card with a clickable
             header strip (seq + glyph + name + status + duration +
             smart preview) and a body that hides when collapsed. -->
        <template v-if="isCollapsible(row.type)">
          <header
            class="timeline__card-header"
            :data-testid="`row-header-${row.event.seq}`"
            :aria-expanded="isRowExpanded(row)"
            role="button"
            tabindex="0"
            @click="onHeaderClick(row)"
            @keydown.enter.prevent="onHeaderClick(row)"
            @keydown.space.prevent="onHeaderClick(row)"
          >
            <span class="timeline__card-seq">#{{ row.event.seq }}</span>
            <span
              class="timeline__kind-label"
              :data-kind="categoryFor(row)"
              :data-testid="`row-kind-${row.event.seq}`"
              :title="KIND_LABEL[categoryFor(row)]"
            >{{ KIND_LABEL[categoryFor(row)] }}</span>
            <span
              class="timeline__card-glyph"
              aria-hidden="true"
            >{{ glyphFor(row) }}</span>
            <span class="timeline__card-name">{{ headerName(row) }}</span>
            <span
              v-if="statusFor(row)"
              class="timeline__card-status"
              :data-status="statusFor(row)"
              :title="statusTitleFor(row)"
            >{{ statusGlyphFor(row) }}</span>
            <span
              v-if="durationFor(row)"
              class="timeline__card-duration"
            >{{ durationFor(row) }}</span>
            <span
              v-if="row.preview"
              class="timeline__card-preview"
              :title="row.preview"
            >{{ row.preview }}</span>
            <span class="timeline__card-spacer" />
            <div
              class="timeline__card-controls"
              @click.stop
            >
              <button
                type="button"
                class="timeline__row-btn"
                data-testid="copy-step"
                title="Copy step content to clipboard"
                @click="copyRow(row)"
              >
                <span
                  class="timeline__row-btn-glyph"
                  aria-hidden="true"
                >⧉</span>
                <span class="timeline__row-btn-label">Copy</span>
              </button>
              <button
                type="button"
                class="timeline__row-btn"
                data-testid="toggle-step"
                :aria-expanded="isRowExpanded(row)"
                :title="isRowExpanded(row) ? 'Collapse this step' : 'Expand this step'"
                @click="toggleRow(row)"
              >
                <span
                  class="timeline__row-btn-glyph"
                  aria-hidden="true"
                >{{ isRowExpanded(row) ? '▾' : '▸' }}</span>
                <span class="timeline__row-btn-label">
                  {{ isRowExpanded(row) ? 'Collapse' : 'Expand' }}
                </span>
              </button>
            </div>
          </header>

          <div
            v-if="isRowExpanded(row)"
            class="timeline__card-body"
          >
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
            <p
              v-else-if="row.type === 'assistant' || row.type === 'thinking'"
              class="timeline__text"
            >
              {{ text(row.event) }}
            </p>
            <code
              v-else
              class="timeline__bmeta"
            >
              {{ generic(row.event) }}
            </code>
          </div>
        </template>

        <!-- One-liner rows (boundary / pause / usage / artifact_edited)
             keep their existing inline layout — they carry their own
             chrome and gain nothing from the card structure. -->
        <template v-else>
          <span class="timeline__seq-row">
            <span class="timeline__seq">#{{ row.event.seq }}</span>
            <span
              class="timeline__kind-label"
              :data-kind="categoryFor(row)"
              :data-testid="`row-kind-${row.event.seq}`"
              :title="KIND_LABEL[categoryFor(row)]"
            >{{ KIND_LABEL[categoryFor(row)] }}</span>
          </span>
          <div class="timeline__row-controls timeline__row-controls--inline">
            <button
              type="button"
              class="timeline__row-btn"
              data-testid="copy-step"
              title="Copy step content to clipboard"
              @click="copyRow(row)"
            >
              <span
                class="timeline__row-btn-glyph"
                aria-hidden="true"
              >⧉</span>
              <span class="timeline__row-btn-label">Copy</span>
            </button>
          </div>

          <div
            v-if="row.type === 'boundary'"
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
        </template>
      </li>
    </ol>

    <div
      v-if="virtualized"
      :style="{ height: `${window.padBottom}px` }"
      aria-hidden="true"
    />

    <button
      v-if="!isPinned"
      type="button"
      class="timeline__jump"
      data-testid="jump-to-latest"
      @click="jumpToLatest"
    >
      ↓ Jump to latest
    </button>

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
  position: relative;
  max-height: 70vh;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.75rem;
  background: var(--color-bg);
}

/* Shared row-button look — used both inside the card header and on
   one-liner inline rows. */
.timeline__row-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text);
  font: inherit;
  font-size: 0.78em;
  line-height: 1;
  padding: 0.35em 0.65em;
  min-height: 1.9rem;
  cursor: pointer;
}
.timeline__row-btn:hover,
.timeline__row-btn:focus-visible {
  color: var(--color-text);
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
}
.timeline__row-btn-glyph {
  font-size: 0.95em;
  line-height: 1;
}
.timeline__row-btn-label {
  font-weight: 500;
  letter-spacing: 0.02em;
}

/* ── Card-style rows (tool / signal / assistant / thinking / generic).
   Each step is a bordered card with a header strip that's clickable as
   a whole. The header is always visible (carries the smart preview);
   the body hides when collapsed. */

.timeline__row--card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  overflow: hidden;
}

/* Per-row-type palette — each step type gets its own pastel surface
   + saturated border so the timeline reads as a scannable colour
   index even when fully collapsed. Tokens live in styles/base.css
   with parallel light + dark values. */
.timeline__row--card[data-row-type='assistant'] {
  border-color: var(--color-row-assistant-border);
  background: var(--color-row-assistant-bg);
}
.timeline__row--card[data-row-type='thinking'] {
  border-color: var(--color-row-thinking-border);
  background: var(--color-row-thinking-bg);
}
.timeline__row--card[data-row-type='tool'] {
  border-color: var(--color-row-tool-border);
  background: var(--color-row-tool-bg);
}
.timeline__row--card[data-row-type='signal'] {
  border-color: var(--color-row-signal-border);
  background: var(--color-row-signal-bg);
}
.timeline__row--card[data-row-type='generic'] {
  border-color: var(--color-row-other-border);
  background: var(--color-row-other-bg);
}

/* The error state on a tool row wins over the per-type tint — a
   failed bash should still read as "this one is the problem" at a
   glance, even though the type colour is amber. */
.timeline__row--card.timeline__row--error {
  border-color: var(--color-danger);
}

.timeline__card-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.45rem 0.6rem;
  background: transparent;
  border-bottom: 1px solid transparent;
  cursor: pointer;
  user-select: none;
  min-height: 2.6rem;
}

.timeline__row--card:not(.timeline__row--collapsed) .timeline__card-header {
  border-bottom-color: var(--color-border);
  background: var(--color-surface-hover);
}

.timeline__card-header:hover,
.timeline__card-header:focus-visible {
  background: var(--color-surface-hover);
  outline: none;
}

.timeline__card-seq {
  font-family: var(--font-mono);
  font-size: 0.78em;
  color: var(--color-text-dim);
  min-width: 2.6rem;
}

.timeline__card-glyph {
  font-size: 0.95em;
  color: var(--color-text-dim);
  line-height: 1;
}

.timeline__card-name {
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--color-text);
}

.timeline__card-status {
  font-size: 0.95em;
  line-height: 1;
}
.timeline__card-status[data-status='ok'] {
  color: var(--color-success);
}
.timeline__card-status[data-status='err'] {
  color: var(--color-danger);
}
.timeline__card-status[data-status='pending'] {
  color: var(--color-warning);
}

.timeline__card-duration {
  font-size: 0.78em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.timeline__card-preview {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: 0.82em;
  color: var(--color-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline__card-spacer {
  flex: 1;
  min-width: 0.5rem;
}

.timeline__card-preview + .timeline__card-spacer {
  display: none;
}

.timeline__card-controls {
  display: flex;
  gap: 0.35rem;
  flex-shrink: 0;
}

.timeline__card-body {
  padding: 0.6rem 0.75rem;
  background: var(--color-surface);
}

/* Inside the body the existing per-type renderers carry their own
   borders / padding / backgrounds that were sized for the OLD
   uncontained row layout. Strip them so we don't get nested-card
   visual noise. The renderers' inner detail (tool args, signal anchor,
   etc.) is preserved. */
.timeline__card-body :deep(.tool-card) {
  border: none;
  padding: 0;
  background: transparent;
}
.timeline__card-body :deep(.tool-card__head) {
  display: none;
}
.timeline__card-body :deep(.signal-card) {
  border: none;
  border-left: 3px solid var(--color-warning);
  border-radius: 0;
  padding: 0.1rem 0 0.1rem 0.6rem;
  background: transparent;
}

/* ── Inline rows (boundary / pause / usage / artifact_edited).
   Kept on the legacy positioned-control layout because they're
   intrinsically one-liners with their own self-contained chrome. */

.timeline__row--inline {
  position: relative;
  padding-top: 2.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.timeline__row-controls--inline {
  position: absolute;
  top: 0.4rem;
  right: 0.5rem;
  display: flex;
  gap: 0.35rem;
  z-index: 1;
}

/* Jump-to-latest pill — only rendered while the user is scrolled up
   off the tail (pinned=false). Sits at the bottom of the timeline
   scroll container so the click target is near the natural reading
   position, not floating over the page. */
.timeline__jump {
  position: sticky;
  bottom: 0.5rem;
  display: block;
  margin: 0.5rem auto 0;
  padding: 0.3em 0.9em;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-size: 0.82em;
  cursor: pointer;
  box-shadow: 0 2px 8px var(--color-shadow);
}
.timeline__jump:hover {
  border-color: currentcolor;
}

.timeline__empty {
  color: var(--color-text-dim);
  margin: 0;
}

.timeline__empty--filtered {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0.75rem;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
}

.timeline__empty--filtered p {
  margin: 0;
  font-size: 0.9em;
}

.timeline__empty-clear {
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: 6px;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.82em;
  padding: 0.3em 0.7em;
}

.timeline__empty-clear:hover,
.timeline__empty-clear:focus-visible {
  background: var(--color-surface-hover);
  outline: none;
}

/* Phase 2 — small category label rendered next to #seq in both the
   card header and the inline-row layout. Uses the same kind-colour
   tokens as the card border + chip dot so the row, the chip, and the
   label are all visibly the same thing. Background is a soft tint so
   it reads as a label, not a button. */
.timeline__kind-label {
  display: inline-block;
  font-size: 0.65em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
  font-family: var(--font-mono);
  padding: 0.15em 0.45em;
  border-radius: 3px;
  border: 1px solid var(--color-row-other-border);
  background: var(--color-row-other-bg);
  color: var(--color-text);
  line-height: 1.4;
  white-space: nowrap;
}
.timeline__kind-label[data-kind='assistant'] {
  border-color: var(--color-row-assistant-border);
  background: var(--color-row-assistant-bg);
}
.timeline__kind-label[data-kind='thinking'] {
  border-color: var(--color-row-thinking-border);
  background: var(--color-row-thinking-bg);
}
.timeline__kind-label[data-kind='tool'] {
  border-color: var(--color-row-tool-border);
  background: var(--color-row-tool-bg);
}
.timeline__kind-label[data-kind='signal'] {
  border-color: var(--color-row-signal-border);
  background: var(--color-row-signal-bg);
}

.timeline__seq-row {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
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
  border-color: var(--color-warning);
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
  color: var(--color-text-dim);
  border: none;
  border-left: 2px solid var(--color-border);
  background: transparent;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  width: 100%;
}

.timeline__edit:hover {
  background: var(--color-warning-bg);
}

.timeline__edit:focus-visible {
  outline: 2px solid var(--color-warning);
  outline-offset: 1px;
}

.timeline__edit-glyph {
  font-size: 1em;
  color: var(--color-warning);
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
