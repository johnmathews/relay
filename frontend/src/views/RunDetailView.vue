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

import { computed, onBeforeUnmount, watch } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import TimelinePane from '@/components/runs/TimelinePane.vue'
import ItersPane from '@/components/runs/ItersPane.vue'
import PauseAnswerForm from '@/components/runs/PauseAnswerForm.vue'
import ArtifactsPane from '@/components/runs/ArtifactsPane.vue'
import WorktreePane from '@/components/runs/WorktreePane.vue'
import {
  useRunDetailQuery,
  useCancelRunMutation,
  useInvalidate,
  asAsyncState,
  type RunDetail,
} from '@/lib/queries'
import { useEventsStore } from '@/stores/events'
import { useCurrentRunStore } from '@/stores/currentRun'

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

const TERMINAL = new Set(['done', 'failed', 'cancelled'])

const status = computed(() => detail.value?.status ?? '')
const isRunning = computed(() => status.value === 'running')
const isPaused = computed(() => status.value === 'paused')

const iters = computed(() => detail.value?.iters ?? [])
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
      'If you meant to drive a structured run, install the engineering-team ' +
      'skill into the target project (`relay install-skill --project ' +
      '<root>`) and start the prompt with `/engineering-team …`.'
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
              <dd>{{ detail.started_at }}</dd>
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
              v-if="isRunning"
              :loading="cancelling"
              data-testid="cancel-run"
              @click="onCancel"
            >
              Cancel run
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

        <PauseAnswerForm
          v-if="isPaused"
          :run-id="detail.id"
          :question="pauseQuestion"
          @resumed="onResumed"
        />

        <h2 class="run-detail__section-title">
          Timeline
        </h2>
        <TimelinePane
          :events="eventList"
          :selected-iter-seq="currentRun.selectedIterId"
        />

        <!-- W5 — Iters pane. Clicking an iter filters the timeline
             above (by iter seq — see ItersPane FILTER CONTRACT). -->
        <div data-testid="iters-pane-slot">
          <ItersPane :iters="iters" />
        </div>

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
</style>
