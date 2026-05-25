<script setup lang="ts">
// Run detail view (`/runs/:id`) — spec §9.1. Closes the Phase-4
// vertical slice: Hub → wizard → THIS view, demoable end-to-end.
//
// Header: status badge, prompt name/version (best-effort: id + body
// preview — the full prompt-name join is W5/later), started_at, iter
// count, current phase, action buttons.
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
//   5. Paused → PauseAnswerForm; on resume → refetch detail + reopen
//      live stream from the current cursor.
//
// W5 (Iters pane) is now wired below — clicking an iter scopes the
// timeline to that iter (filter by iter seq). W7 (Artifacts/Worktree
// panes) is still OUT OF SCOPE here — clearly-marked placeholder
// sections are left for it.

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RunHealthBadge from '@/components/runs/RunHealthBadge.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import TimelinePane from '@/components/runs/TimelinePane.vue'
import ItersPane from '@/components/runs/ItersPane.vue'
import PauseAnswerForm from '@/components/runs/PauseAnswerForm.vue'
import ArtifactsPane from '@/components/runs/ArtifactsPane.vue'
import WorktreePane from '@/components/runs/WorktreePane.vue'
import ChildrenPane from '@/components/runs/ChildrenPane.vue'
import ParentRunChip from '@/components/shared/ParentRunChip.vue'
import {
  useRunDetailQuery,
  useCancelRunMutation,
  useInvalidate,
  useRunChildrenQuery,
  asAsyncState,
  type RunDetail,
} from '@/lib/queries'
import { useEventsStore } from '@/stores/events'
import { useCurrentRunStore } from '@/stores/currentRun'

const props = defineProps<{ id: string }>()

/**
 * Render the run's `started_at` (a UTC-tagged ISO string from the API
 * since commit 8b00e61) in the viewer's local timezone. Falls back to
 * the raw value if parsing fails so a future format change can't blank
 * out the header.
 */
function formatStarted(iso: string | null | undefined): string {
  if (iso == null || iso === '') return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  return new Date(t).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

const detailQuery = useRunDetailQuery(() => props.id)
const detail = computed<RunDetail | null>(
  () => detailQuery.data.value ?? null,
)
const { isLoading, error } = asAsyncState(detailQuery)

const eventsStore = useEventsStore()
const currentRun = useCurrentRunStore()
const invalidate = useInvalidate()
const cancelRun = useCancelRunMutation()

// Local view-scoped terminal check that governs whether the live SSE
// stream is force-closed after a status refetch (see onLifecycle / onCancel
// below). MUST mirror `stores/events.ts::TERMINAL_STATUSES` — `paused` and
// `awaiting_children` are NOT terminal because they can transition back to
// `running`. Adding either here would `markTerminal()` an in-flight stream
// and stop live updates while the parent waits for child completion. See
// ADR-34 / `docs/spec.md` §3.1.
const TERMINAL = new Set(['done', 'failed', 'cancelled'])

const status = computed(() => detail.value?.status ?? '')
const isPaused = computed(() => status.value === 'paused')

const iters = computed(() => detail.value?.iters ?? [])

// 9e — Children query for cascade-aware cancel label.
const childrenQuery = useRunChildrenQuery(() => props.id)
const children = computed(() => childrenQuery.data.value ?? [])
const childCount = computed(() => children.value.length)

/** True when the run can still be cancelled (running OR awaiting children). */
const isCancellable = computed(
  () => status.value === 'running' || status.value === 'awaiting_children',
)

/** Cancel button label — cascade-aware when parent has live children. */
const cancelLabel = computed(() => {
  if (childCount.value === 0) return 'Cancel run'
  const n = childCount.value
  return `Cancel run and ${n} child${n === 1 ? '' : 'ren'}`
})
const iterCount = computed(() => iters.value.length)
const currentPhase = computed(() => {
  const last = iters.value[iters.value.length - 1]
  return last?.phase ?? '—'
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
 * The user-facing failure summary for a terminal failed/cancelled run.
 * Pulled from the last iter (the one that actually closed the run):
 *
 *   - `exit_reason`              — the orchestrator's reason code
 *     (`agent_end_no_signal`, `timeout`, `cancelled`, `crash`,
 *     `max_iters` — spec §3.1).
 *   - `signal_args.marker_error` — the marker-headline string set
 *     when the agent emitted a sentinel that violated the prompt
 *     marker contract (loop.py records it here).
 *
 * Returns `null` for terminal-but-not-failed states (`done`, paused
 * statuses are not terminal) so the banner stays hidden on success.
 *
 * The "agent_end_no_signal" hint is the load-bearing UX fix from
 * the field report: a fresh "Hello, this is a test" prompt against
 * a project without the engineering-team skill ALWAYS lands in this
 * exit reason — pi can't know about relay's sentinel grammar from
 * a bare prompt. The hint points the user at the skill install
 * (CLAUDE.md "Toolchain"; ADR-28).
 */
const FAILURE_STATUSES = new Set(['failed', 'cancelled'])
const failureInfo = computed<{
  reason: string
  marker_error: string | null
  hint: string | null
} | null>(() => {
  if (!FAILURE_STATUSES.has(status.value)) return null
  const last = iters.value[iters.value.length - 1] ?? null
  const reason = last?.exit_reason ?? status.value
  const args = last?.signal_args ?? null
  const markerError =
    args != null && typeof args.marker_error === 'string'
      ? args.marker_error
      : null
  let hint: string | null = null
  if (reason === 'agent_end_no_signal' && markerError == null) {
    hint =
      'The agent finished its turn without emitting a closing sentinel ' +
      '([[engteam:done]], [[engteam:handoff]], or [[engteam:pause-for-input]]). ' +
      'Relay bundles the engineering-team skill and injects it into every ' +
      'pi spawn automatically (no per-project install needed). Start the ' +
      'prompt with `/engineering-team …` to trigger it; if the skill is ' +
      'already loaded but you got this error, the agent may have aborted ' +
      'early (token budget, transient API failure) — check the timeline ' +
      'for the last tool result.'
  } else if (reason === 'timeout') {
    hint =
      'The iter exceeded its wall-clock budget (iter_timeout). The next ' +
      'iter would have started fresh; raise iter_timeout if the work ' +
      'legitimately needs longer.'
  } else if (reason === 'max_iters') {
    hint =
      'The run hit its max_iters cap before emitting a `done` sentinel. ' +
      'Raise max_iters or break the work into smaller handoffs.'
  } else if (reason === 'internal_error') {
    hint =
      'The orchestrator caught an unexpected exception while driving the ' +
      'loop. Check the server log for the stack trace.'
  }
  return { reason, marker_error: markerError, hint }
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
const renderedCount = computed(() => eventsStore.renderedCount)
// ADR-45 Plan A — feeds the run-health badge in the header so a quiet
// live run reads as "still alive" rather than "frozen".
const lastHeartbeat = computed(() => eventsStore.lastHeartbeat)
// ADR-46 Plan B — in-progress assistant turns from the ephemeral
// SSE delta channel; rendered as pseudo-rows below the canonical
// timeline so tokens appear live while pi streams.
const pendingTurns = computed(() => eventsStore.pendingTurns)

const showPrompt = ref(false)

/**
 * The most recent non-empty `assistant_text` event payload, surfaced as
 * a "what's the agent doing right now" peek above the timeline. Returns
 * `null` when no assistant text has streamed yet — the section then
 * stays hidden so the layout doesn't reserve dead space.
 */
const latestActivity = computed<string | null>(() => {
  const evs = eventsStore.events
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i]!
    if (ev.kind === 'assistant_text') {
      const t = ev.payload.text
      if (typeof t === 'string' && t.trim() !== '') return t
    }
  }
  return null
})

onBeforeUnmount(() => {
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
        <header class="run-detail__header">
          <div class="run-detail__title-row">
            <h1 class="run-detail__title">
              Run {{ detail.id }}
            </h1>
            <StatusBadge :status="detail.status" />
            <RunHealthBadge
              :status="detail.status"
              :last-heartbeat="lastHeartbeat"
            />
            <ParentRunChip :parent-run-id="detail.parent_run_id" />
          </div>
          <dl class="run-detail__meta">
            <div>
              <dt>Prompt</dt>
              <dd>
                {{
                  detail.prompt_id != null
                    ? `#${detail.prompt_id}`
                    : 'inline'
                }}
              </dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd
                :title="detail.started_at"
                data-testid="run-started-at"
              >
                {{ formatStarted(detail.started_at) }}
              </dd>
            </div>
            <div>
              <dt>Iters</dt>
              <dd>{{ iterCount }} / {{ detail.max_iters }}</dd>
            </div>
            <div>
              <dt>Phase</dt>
              <dd>{{ currentPhase }}</dd>
            </div>
            <div>
              <dt>Events</dt>
              <dd data-testid="rendered-event-count">
                {{ renderedCount }}
              </dd>
            </div>
          </dl>

          <div class="run-detail__actions">
            <ActionButton
              v-if="isCancellable"
              :loading="cancelling"
              data-testid="cancel-run"
              @click="onCancel"
            >
              {{ cancelLabel }}
            </ActionButton>
          </div>
        </header>

        <aside
          v-if="failureInfo"
          data-testid="run-failure-banner"
          class="run-detail__failure"
          :data-reason="failureInfo.reason"
        >
          <strong class="run-detail__failure-title">
            Run {{ status }} — {{ failureInfo.reason }}
          </strong>
          <p
            v-if="failureInfo.marker_error"
            class="run-detail__failure-marker"
          >
            <span class="run-detail__failure-label">Marker error:</span>
            <code>{{ failureInfo.marker_error }}</code>
          </p>
          <p
            v-if="failureInfo.hint"
            class="run-detail__failure-hint"
          >
            {{ failureInfo.hint }}
          </p>
        </aside>

        <details
          class="run-detail__prompt"
          :open="showPrompt"
          @toggle="showPrompt = ($event.target as HTMLDetailsElement).open"
        >
          <summary class="run-detail__prompt-summary">
            Prompt
          </summary>
          <pre class="run-detail__prompt-body">{{ detail.prompt_body }}</pre>
        </details>

        <div
          v-if="latestActivity != null"
          class="run-detail__activity"
          data-testid="latest-activity"
          aria-label="Latest agent output"
        >
          <span class="run-detail__activity-label">agent</span>
          <p class="run-detail__activity-text">
            {{ latestActivity }}
          </p>
        </div>

        <PauseAnswerForm
          v-if="isPaused"
          :run-id="detail.id"
          :question="pauseQuestion"
          :review-paths="pauseReviewPaths"
          @resumed="onResumed"
        />

        <h2 class="run-detail__section-title">
          Timeline
        </h2>
        <TimelinePane
          :events="eventList"
          :selected-iter-seq="currentRun.selectedIterId"
          :pending-turns="pendingTurns"
          :run-id="detail.id"
        />

        <!-- W5 — Iters pane. Clicking an iter filters the timeline
             above (by iter seq — see ItersPane FILTER CONTRACT). -->
        <div data-testid="iters-pane-slot">
          <ItersPane :iters="iters" />
        </div>

        <!-- 9e — Children pane: direct children dispatched via fanout
             (spec.md §9.1, 9e). Conditional — renders nothing on a run
             that never fanned out. -->
        <ChildrenPane :run-id="detail.id" />

        <!-- W7 — Artifacts pane: the shared FileTree+FileViewer wired
             to the run-artifacts source (ADR-25). -->
        <div data-testid="artifacts-pane-slot">
          <ArtifactsPane :run-id="detail.id" />
        </div>

        <!-- W7 — Worktree pane: DEGRADED for MVP (scope decision G2) —
             read-only path+branch from run detail; live git status /
             per-file diff is post-MVP. -->
        <div data-testid="worktree-pane-slot">
          <WorktreePane
            :worktree-path="detail.worktree_path"
            :branch="detail.branch"
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
  gap: 1rem;
  max-width: 1100px;
}

.run-detail__title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.run-detail__title {
  margin: 0;
  font-size: 1.3rem;
  font-family: var(--font-mono);
}

.run-detail__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0.75rem 0 0;
}

.run-detail__meta dt {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.run-detail__meta dd {
  margin: 0.15rem 0 0;
  font-weight: 600;
}

.run-detail__actions {
  margin-top: 0.9rem;
}

.run-detail__section-title {
  margin: 0.5rem 0 0;
  font-size: 1.05rem;
}

.run-detail__placeholder {
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  padding: 1rem;
  color: var(--color-text-dim);
}

.run-detail__placeholder p {
  margin: 0.4rem 0 0;
}

.run-detail__failure {
  border: 1px solid #d04a4a;
  border-left: 4px solid #d04a4a;
  background: rgba(208, 74, 74, 0.08);
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.run-detail__failure-title {
  font-family: var(--font-mono);
  color: #b03a3a;
}

.run-detail__failure-marker,
.run-detail__failure-hint {
  margin: 0;
  font-size: 0.9em;
  line-height: 1.4;
}

.run-detail__failure-label {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
  margin-right: 0.4rem;
}

.run-detail__failure-marker code {
  font-family: var(--font-mono);
  font-size: 0.85em;
}

.run-detail__prompt {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
}

.run-detail__prompt-summary {
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
  user-select: none;
}

.run-detail__prompt-summary:hover {
  color: var(--color-text);
}

.run-detail__prompt-body {
  margin: 0;
  padding: 0.6rem 0.75rem 0.75rem;
  border-top: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text);
  max-height: 40vh;
  overflow-y: auto;
}

.run-detail__activity {
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-accent, #4a90d9);
  border-radius: 6px;
  padding: 0.55rem 0.75rem;
  background: var(--color-surface);
}

.run-detail__activity-label {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.run-detail__activity-text {
  margin: 0.2rem 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.9em;
  max-height: 120px;
  overflow-y: auto;
}
</style>
