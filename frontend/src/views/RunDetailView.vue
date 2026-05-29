<script setup lang="ts">
// Run detail view (`/runs/:id`) — spec §9.1. Closes the Phase-4
// vertical slice: Hub → wizard → THIS view, demoable end-to-end.
//
// Orchestration (the load-bearing correctness — see stores/events.ts):
//   1. Fetch run detail FIRST via Colada (`useRunDetailQuery`).
//   2. Once detail lands, hand its status to the events store's
//      `open()`. Terminal status ⇒ REST replay (NO SSE). Running/paused
//      ⇒ live SSE via the W1 wrapper.
//   3. The store pings `onLifecycle` (coalesced) on lifecycle events;
//      we refetch detail there. If the refetched status is terminal we
//      call `store.markTerminal()` so a finished-run EOF cannot trigger
//      a reconnect-storm.
//   4. Cancel (running only) → cancel mutation → refetch detail.
//   5. Paused → PauseAnswerForm (in RunRightPane); on resume → refetch
//      detail + reopen live stream from the current cursor.
//
// Layout: two-column grid — RunSidebar (left rail) + RunRightPane
// (right body). View selection is URL-reflected via ?view=.

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import RunSidebar from '@/components/runs/layout/RunSidebar.vue'
import RunRightPane from '@/components/runs/layout/RunRightPane.vue'
import {
  useRunDetailQuery,
  useCancelRunMutation,
  useInvalidate,
  useRunChildrenQuery,
  useProjectQuery,
  asAsyncState,
  type RunDetail,
} from '@/lib/queries'
import { useEventsStore } from '@/stores/events'
import { useCurrentRunStore } from '@/stores/currentRun'
import {
  parseView,
  serializeView,
  smartDefault,
  type RunView,
} from '@/lib/runView'

const props = defineProps<{ id: string }>()

const detailQuery = useRunDetailQuery(() => props.id)
const detail = computed<RunDetail | null>(
  () => detailQuery.data.value ?? null,
)
const { isLoading, error } = asAsyncState(detailQuery)

const eventsStore = useEventsStore()
const currentRun = useCurrentRunStore()
const invalidate = useInvalidate()
const cancelRun = useCancelRunMutation()

const route = useRoute()
const router = useRouter()

/**
 * URL-derived view selection. Returns null while the URL has no
 * ?view=; falls back to {@link smartDefault} below once detail lands.
 *
 * Source of truth = URL. We never store selection in component state;
 * mutations always push through the router.
 */
const urlView = computed<RunView | null>(() => parseView(route.query))

const iters = computed(() => detail.value?.iters ?? [])

/**
 * The reviewable artifact paths declared on the paused iter (14c +
 * 14f — ADR-40/ADR-41). Empty array for any paused iter that didn't
 * carry the attribute (every pre-14b run, and any 14b skill that
 * omitted it); PauseAnswerForm treats an empty array as "render the
 * existing minimal form". Walks iters newest-first like `pauseQuestion`
 * so a resumed-then-paused-again run picks the latest pause.
 *
 * Migration fallback: a paused iter under 14a–14d carries only the
 * scalar `signal_args.review_path` key (no plural key). We read it as
 * a one-element list so an iter that survives a process restart into
 * the 14f code keeps working. New 14f emits land with the plural key
 * only (14f's sentinel parser stopped writing the scalar key).
 *
 * Declared up here (above `currentView` / the bootstrap watcher) so the
 * Phase 3 `smartDefault({ reviewPaths })` paused→artifact branch can
 * read it without a TDZ hazard under a hydrated Colada cache.
 */
const pauseReviewPaths = computed<string[]>(() => {
  for (let i = iters.value.length - 1; i >= 0; i--) {
    const it = iters.value[i]!
    if (it.signal_kind === 'pause' && it.signal_args != null) {
      const rps = (it.signal_args as Record<string, unknown>).review_paths
      if (Array.isArray(rps)) {
        return rps.filter(
          (v): v is string => typeof v === 'string' && v !== '',
        )
      }
      const legacy = (it.signal_args as Record<string, unknown>).review_path
      if (typeof legacy === 'string' && legacy !== '') return [legacy]
      // Found the latest pause iter; if it has no review_path(s), stop —
      // we don't fall back to an older pause's value.
      return []
    }
  }
  return []
})

/**
 * The effective view threaded into the layout components. Resolves
 * the smart-default in one place so RunSidebar / RunRightPane don't
 * need detail to choose a default.
 */
const currentView = computed<RunView>(() => {
  if (urlView.value != null) return urlView.value
  const d = detail.value
  if (d == null) return { kind: 'overview' }
  return smartDefault({
    status: d.status,
    iters: d.iters ?? [],
    reviewPaths: pauseReviewPaths.value,
  })
})

/**
 * One-shot bootstrap: when detail first lands and the URL has no
 * view=, hydrate the URL with the smart-default. This makes the
 * default shareable / refreshable. Subsequent navigation uses the
 * push from {@link onSelectView}.
 */
let viewBootstrapped = false
watch(
  detail,
  (d) => {
    if (d == null || viewBootstrapped) return
    if (urlView.value != null) {
      viewBootstrapped = true
      return
    }
    viewBootstrapped = true
    const v = smartDefault({
      status: d.status,
      iters: d.iters ?? [],
      reviewPaths: pauseReviewPaths.value,
    })
    void router.replace({
      query: { ...route.query, view: serializeView(v) },
    })
  },
  { immediate: true },
)

function onSelectView(view: RunView): void {
  // User-initiated navigation always un-pins follow-live — the click is
  // the signal of intent ("lock onto this") regardless of what was
  // clicked. The pin button re-engages tailing.
  if (followLive.value) followLive.value = false
  void router.push({
    query: { ...route.query, view: serializeView(view) },
  })
}

/**
 * Follow-live pin (proposal §"Follow-live behaviour"). When on and the
 * run is live (`running` / `awaiting_children`), the right pane auto-
 * promotes its selection to the latest iter as new iters arrive.
 *
 * Auto-engages on first detail load when:
 *   - status is live, AND
 *   - the URL had no `?view=` (smart-default fired — user did not pick
 *     a specific view).
 *
 * Auto-promote uses `router.replace` so navigating away with the back
 * button doesn't have to traverse every auto-promoted iter.
 */
const followLive = ref(false)
const isLive = computed(
  () =>
    detail.value?.status === 'running' ||
    detail.value?.status === 'awaiting_children',
)

let followLiveBootstrapped = false
watch(
  detail,
  (d) => {
    if (d == null || followLiveBootstrapped) return
    followLiveBootstrapped = true
    const liveStatus =
      d.status === 'running' || d.status === 'awaiting_children'
    if (liveStatus && urlView.value == null) followLive.value = true
  },
  { immediate: true },
)

watch(
  () => iters.value[iters.value.length - 1]?.seq ?? null,
  (latest) => {
    if (latest == null) return
    if (!followLive.value) return
    if (!isLive.value) return
    if (
      currentView.value.kind === 'iter' &&
      currentView.value.seq === latest
    )
      return
    void router.replace({
      query: {
        ...route.query,
        view: serializeView({ kind: 'iter', seq: latest }),
      },
    })
  },
)

function toggleFollowLive(): void {
  const next = !followLive.value
  followLive.value = next
  if (!next) return
  // Re-engaging: jump to the latest iter immediately.
  const latest = iters.value[iters.value.length - 1]?.seq
  if (latest == null) return
  void router.replace({
    query: {
      ...route.query,
      view: serializeView({ kind: 'iter', seq: latest }),
    },
  })
}

/**
 * Keyboard navigation (proposal §"Keyboard navigation"). Shipped subset:
 *   - `j` / `↓`   → next rail row (Overview → iter:1 → … → iter:N)
 *   - `k` / `↑`   → previous rail row
 *   - `g o`       → jump to Overview
 *   - `g i`       → jump to first iter
 *   - `f`         → toggle Follow-live pin (no-op on terminal)
 *   - `Esc`       → blur active element
 *   - `c`         → focus the Cancel button (does NOT trigger)
 *
 * Deferred (artifact-tree walking + chip-row focus are extra surface):
 *   `g a` (jump to first artifact), `h` / `l` (rail/pane focus),
 *   `/` (focus chip row).
 *
 * All shortcuts no-op when focus is inside a text input / textarea /
 * select / contenteditable, or when a modifier key is held. We write
 * the listener directly instead of pulling in @vueuse/core — the chord
 * state machine + focus guard would still be ours either way.
 */
const selectableViews = computed<RunView[]>(() => {
  const xs: RunView[] = [{ kind: 'overview' }]
  for (const it of iters.value) xs.push({ kind: 'iter', seq: it.seq })
  return xs
})

function viewsEq(a: RunView, b: RunView): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'iter' && b.kind === 'iter') return a.seq === b.seq
  if (a.kind === 'artifact' && b.kind === 'artifact') return a.path === b.path
  return a.kind === 'overview'
}

function moveSelection(delta: number): void {
  const xs = selectableViews.value
  if (xs.length === 0) return
  const cur = currentView.value
  let idx = xs.findIndex((v) => viewsEq(v, cur))
  // Current view isn't in the rail's selectable list (e.g. an artifact
  // path) — entering from "outside" lands on the first / last row.
  if (idx < 0) idx = delta > 0 ? -1 : xs.length
  const next = Math.min(xs.length - 1, Math.max(0, idx + delta))
  onSelectView(xs[next]!)
}

let chordPending = false
let chordTimer: ReturnType<typeof setTimeout> | null = null
const CHORD_TIMEOUT_MS = 800

function clearChord(): void {
  chordPending = false
  if (chordTimer != null) clearTimeout(chordTimer)
  chordTimer = null
}

function armChord(): void {
  chordPending = true
  if (chordTimer != null) clearTimeout(chordTimer)
  chordTimer = setTimeout(clearChord, CHORD_TIMEOUT_MS)
}

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  if (t.isContentEditable) return true
  const tag = t.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.defaultPrevented) return
  if (e.metaKey || e.ctrlKey || e.altKey) return
  if (isEditableTarget(e.target)) return

  if (chordPending) {
    if (e.key === 'o') {
      onSelectView({ kind: 'overview' })
      clearChord()
      e.preventDefault()
      return
    }
    if (e.key === 'i') {
      const first = iters.value[0]
      if (first != null) onSelectView({ kind: 'iter', seq: first.seq })
      clearChord()
      e.preventDefault()
      return
    }
    clearChord()
    // Fall through — the keypress that didn't complete the chord is
    // still eligible as a single-key shortcut.
  }

  switch (e.key) {
    case 'j':
    case 'ArrowDown':
      moveSelection(1)
      e.preventDefault()
      return
    case 'k':
    case 'ArrowUp':
      moveSelection(-1)
      e.preventDefault()
      return
    case 'g':
      armChord()
      e.preventDefault()
      return
    case 'f':
      if (isLive.value) {
        toggleFollowLive()
        e.preventDefault()
      }
      return
    case 'Escape':
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur()
      }
      return
    case 'c': {
      const btn = document.querySelector<HTMLElement>(
        '[data-testid="cancel-run"]',
      )
      if (btn != null) {
        btn.focus()
        e.preventDefault()
      }
      return
    }
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
})

// Local view-scoped terminal check that governs whether the live SSE
// stream is force-closed after a status refetch (see onLifecycle / onCancel
// below). MUST mirror `stores/events.ts::TERMINAL_STATUSES` — `paused` and
// `awaiting_children` are NOT terminal because they can transition back to
// `running`. Adding either here would `markTerminal()` an in-flight stream
// and stop live updates while the parent waits for child completion. See
// ADR-34 / `docs/spec.md` §3.1.
const TERMINAL = new Set(['done', 'failed', 'cancelled'])

// 9e — Children query for cascade-aware cancel label.
const childrenQuery = useRunChildrenQuery(() => props.id)
const children = computed(() => childrenQuery.data.value ?? [])
const childCount = computed(() => children.value.length)

// Project lookup so the sidebar can show "which project am I in" as a
// title at the top. Reactive on detail.project_id; an inert sentinel
// (0) before detail lands ensures the query never fires with NaN/null.
const projectQuery = useProjectQuery(() => detail.value?.project_id ?? 0)
const project = computed(() => {
  if (detail.value == null) return null
  const p = projectQuery.data.value
  if (p == null) return null
  return { id: p.id, name: p.name }
})

/** Cancel button label — cascade-aware when parent has live children. */
const cancelLabel = computed(() => {
  if (childCount.value === 0) return 'Cancel run'
  const n = childCount.value
  return `Cancel run and ${n} child${n === 1 ? '' : 'ren'}`
})

/** The pause question from the paused iter's signal_args (spec §3.2). */
const pauseQuestion = computed(() => {
  for (let i = iters.value.length - 1; i >= 0; i--) {
    const it = iters.value[i]!
    if (it.signal_kind === 'pause' && it.signal_args != null) {
      const q = it.signal_args.question
      if (typeof q === 'string') return q
    }
  }
  return ''
})

let opened = false

/**
 * Coalesced lifecycle handler the events store calls when a stream
 * lifecycle event lands. Refetch detail; if it is now terminal, defuse
 * the live stream so it can never reconnect-storm on the finished-run
 * EOF.
 */
async function onLifecycle(): Promise<void> {
  await detailQuery.refetch()
  if (TERMINAL.has(detail.value?.status ?? '')) {
    eventsStore.markTerminal()
  }
}

/** Open the right strategy once, when detail first lands. */
watch(
  detail,
  (d) => {
    if (d == null || opened) return
    opened = true
    void eventsStore.open(d.id, d.status, {
      invalidate,
      onLifecycle: () => void onLifecycle(),
    })
  },
  { immediate: true },
)

const cancelling = computed(() => cancelRun.isLoading.value)

async function onCancel(): Promise<void> {
  try {
    await cancelRun.mutateAsync(props.id)
  } catch {
    // Surfaced via the run status / next refetch; nothing inline here.
  }
  await detailQuery.refetch()
  if (TERMINAL.has(detail.value?.status ?? '')) {
    eventsStore.markTerminal()
  }
}

async function onResumed(): Promise<void> {
  // The run is running again — refetch detail and reopen the live
  // stream from the current cursor (gap-free continuation).
  await detailQuery.refetch()
  const d = detail.value
  if (d == null) return
  void eventsStore.open(d.id, d.status, {
    invalidate,
    onLifecycle: () => void onLifecycle(),
  })
}

const eventList = computed(() => eventsStore.events)
// ADR-45 Plan A — feeds the run-health badge in the header so a quiet
// live run reads as "still alive" rather than "frozen".
const lastHeartbeat = computed(() => eventsStore.lastHeartbeat)
// ADR-46 Plan B — in-progress assistant turns from the ephemeral
// SSE delta channel; rendered as pseudo-rows below the canonical
// timeline so tokens appear live while pi streams.
const pendingTurns = computed(() => eventsStore.pendingTurns)

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  clearChord()
  eventsStore.reset()
  currentRun.reset()
})
</script>

<template>
  <section class="run-detail">
    <AsyncBoundary
      :loading="isLoading"
      :error="error"
    >
      <template v-if="detail">
        <div class="run-detail__layout">
          <RunSidebar
            :run-id="detail.id"
            :project="project"
            :selection="currentView"
            :status="detail.status"
            :iters="detail.iters ?? []"
            :children="children"
            @update:view="onSelectView"
          />
          <RunRightPane
            :detail="{ ...detail, iters: detail.iters ?? [] }"
            :selection="currentView"
            :events="eventList"
            :pending-turns="pendingTurns"
            :last-heartbeat="lastHeartbeat"
            :child-count="childCount"
            :cancel-label="cancelLabel"
            :cancelling="cancelling"
            :pause-question="pauseQuestion"
            :pause-review-paths="pauseReviewPaths"
            :follow-live="followLive"
            :follow-live-visible="isLive"
            @cancel="onCancel"
            @resumed="onResumed"
            @toggle-follow-live="toggleFollowLive"
          />
        </div>
      </template>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.run-detail {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.run-detail__layout {
  display: grid;
  grid-template-columns: minmax(220px, 280px) 1fr;
  min-height: 100%;
  align-items: stretch;
}

@media (max-width: 899px) {
  /* Stacking under 900px is Phase 6 work — for Phase 1, simply allow
     the rail to fall below the right pane in narrow viewports. The
     visual result is acceptable for the localhost dev use case. */
  .run-detail__layout {
    grid-template-columns: 1fr;
  }
}
</style>
