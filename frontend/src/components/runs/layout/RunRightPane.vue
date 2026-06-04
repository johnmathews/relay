<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RunHealthBadge from '@/components/runs/RunHealthBadge.vue'
import ParentRunChip from '@/components/shared/ParentRunChip.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import PauseBanner from './PauseBanner.vue'
import OverviewPanel from './OverviewPanel.vue'
import IterTimelinePanel from './IterTimelinePanel.vue'
import ArtifactPanel from './ArtifactPanel.vue'
import ToolCallDetailDrawer, {
  type ToolCallDrawerPayload,
} from '@/components/runs/ToolCallDetailDrawer.vue'
import type { RunView } from '@/lib/runView'
import type { HeartbeatSnapshot, StreamEvent, PendingTurn } from '@/stores/events'
import type { Iter } from '@/lib/queries'
import { useReopenRunMutation } from '@/lib/queries'

interface RunDetail {
  id: string
  status: string
  started_at: string | null
  ended_at: string | null  // reserved — surface in the meta row in a later phase
  max_iters: number
  prompt_id: number | null
  prompt_body: string
  parent_run_id: string | null
  iters: ReadonlyArray<Iter>
}

const props = withDefaults(
  defineProps<{
    detail: RunDetail
    selection: RunView
    events: ReadonlyArray<StreamEvent>
    pendingTurns: ReadonlyArray<PendingTurn>
    lastHeartbeat: HeartbeatSnapshot | null
    childCount: number
    cancelLabel: string
    cancelling: boolean
    pauseQuestion: string
    pauseReviewPaths: ReadonlyArray<string>
    /**
     * Follow-live pin state. When true and the run is live, the parent
     * auto-promotes the rail selection to the latest iter as new iters
     * arrive. The button visually reflects this state and emits
     * `toggle-follow-live` on click.
     */
    followLive?: boolean
    /** Whether to render the pin button at all (hidden on terminal). */
    followLiveVisible?: boolean
  }>(),
  {
    followLive: false,
    followLiveVisible: false,
  },
)

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'resumed'): void
  (e: 'toggle-follow-live'): void
}>()

const iterCount = computed(() => props.detail.iters.length)
const currentPhase = computed(() => {
  const last = props.detail.iters[props.detail.iters.length - 1]
  return last?.phase ?? '—'
})

const isCancellable = computed(
  () =>
    props.detail.status === 'running' ||
    props.detail.status === 'awaiting_children',
)

const isPaused = computed(() => props.detail.status === 'paused')

function formatStarted(iso: string | null): string {
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

const FAILURE_STATUSES = new Set(['failed', 'cancelled'])
const failureInfo = computed<{
  reason: string
  marker_error: string | null
  hint: string | null
} | null>(() => {
  if (!FAILURE_STATUSES.has(props.detail.status)) return null
  const last = props.detail.iters[props.detail.iters.length - 1] ?? null
  const reason = last?.exit_reason ?? props.detail.status
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

function onCancel(): void {
  emit('cancel')
}

function onResumed(): void {
  emit('resumed')
}

function onToggleFollowLive(): void {
  emit('toggle-follow-live')
}

// Per-run dismissal of the red failure banner. Persisted in
// localStorage keyed by run id so a reload after dismissing keeps it
// hidden; visiting a different failed run shows that run's banner
// again. We reset the in-component flag whenever the run id changes
// (the view is re-keyed on /runs/:id navigation anyway, but a future
// change might not remount).
const DISMISS_KEY_PREFIX = 'relay.failureBanner.dismissed:'

function readDismissed(runId: string): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY_PREFIX + runId) === '1'
  } catch {
    return false
  }
}

const failureDismissed = ref<boolean>(readDismissed(props.detail.id))

watch(
  () => props.detail.id,
  (id) => {
    failureDismissed.value = readDismissed(id)
  },
)

function dismissFailure(): void {
  failureDismissed.value = true
  try {
    localStorage.setItem(DISMISS_KEY_PREFIX + props.detail.id, '1')
  } catch {
    // Storage unavailable — session-only dismissal still applies.
  }
}

const showFailure = computed(
  () => failureInfo.value != null && !failureDismissed.value,
)

// Reopen-as-paused affordance (WU5 — ADR-53). Visible only for failed runs
// whose last iter exited without a terminal sentinel, so the operator can
// resume with guidance rather than starting from scratch.
const reopenMutation = useReopenRunMutation()

const canReopen = computed<boolean>(() => {
  if (props.detail.status !== 'failed') return false
  const last = props.detail.iters[props.detail.iters.length - 1] ?? null
  const reason = last?.exit_reason
  return (
    reason === 'agent_end_no_signal' ||
    reason === 'agent_end_no_signal_autopause'
  )
})

async function onReopen(): Promise<void> {
  await reopenMutation.mutateAsync(props.detail.id)
  emit('resumed')
}

// Phase 5 — tool-call detail drawer. State is local + transient (per
// the proposal's URL contract: not reflected in the URL). A
// provide/inject pair exposes `openToolDetail` to any descendant
// `ToolCallCard` without prop-drilling through OverviewPanel /
// IterTimelinePanel / TimelinePane.
const drawerOpen = ref(false)
const drawerPayload = ref<ToolCallDrawerPayload | null>(null)

function openToolDetail(payload: ToolCallDrawerPayload): void {
  drawerPayload.value = payload
  drawerOpen.value = true
}

function closeDrawer(): void {
  drawerOpen.value = false
}

// Reset payload when the run changes — a stale tool from the previous
// run would be confusing if the operator re-opens the drawer.
watch(
  () => props.detail.id,
  () => {
    drawerOpen.value = false
    drawerPayload.value = null
  },
)

provide('openToolDetail', openToolDetail)
</script>

<template>
  <section
    class="right-pane"
    data-testid="run-right-pane"
  >
    <header class="right-pane__header">
      <div class="right-pane__title-row">
        <h1 class="right-pane__title">
          Run {{ detail.id }}
        </h1>
        <StatusBadge :status="detail.status" />
        <RunHealthBadge
          :status="detail.status"
          :last-heartbeat="lastHeartbeat"
        />
        <ParentRunChip :parent-run-id="detail.parent_run_id" />
      </div>

      <dl class="right-pane__meta">
        <div>
          <dt>Prompt</dt>
          <dd>{{ detail.prompt_id != null ? `#${detail.prompt_id}` : 'inline' }}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd
            :title="detail.started_at ?? ''"
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
      </dl>

      <div
        v-if="isCancellable || followLiveVisible"
        class="right-pane__actions"
      >
        <ActionButton
          v-if="isCancellable"
          :loading="cancelling"
          data-testid="cancel-run"
          @click="onCancel"
        >
          {{ cancelLabel }}
        </ActionButton>
        <button
          v-if="followLiveVisible"
          type="button"
          class="right-pane__follow-live"
          :class="{
            'right-pane__follow-live--on': followLive,
          }"
          :aria-pressed="followLive"
          :title="
            followLive
              ? 'Following latest iter — click to unpin'
              : 'Click to follow the latest iter'
          "
          data-testid="follow-live-pin"
          @click="onToggleFollowLive"
        >
          <span
            aria-hidden="true"
            class="right-pane__follow-live-glyph"
          >⏵</span>
          <span class="right-pane__follow-live-label">
            {{ followLive ? 'Following live' : 'Follow live' }}
          </span>
        </button>
      </div>

      <aside
        v-if="showFailure && failureInfo"
        class="right-pane__failure"
        data-testid="run-failure-banner"
        :data-reason="failureInfo.reason"
      >
        <button
          type="button"
          class="right-pane__failure-close"
          data-testid="dismiss-failure-banner"
          aria-label="Dismiss failure banner"
          title="Dismiss"
          @click="dismissFailure"
        >
          ×
        </button>
        <strong class="right-pane__failure-title">
          Run {{ detail.status }} — {{ failureInfo.reason }}
        </strong>
        <p
          v-if="failureInfo.marker_error"
          class="right-pane__failure-marker"
        >
          <span class="right-pane__failure-label">Marker error:</span>
          <code>{{ failureInfo.marker_error }}</code>
        </p>
        <p
          v-if="failureInfo.hint"
          class="right-pane__failure-hint"
        >
          {{ failureInfo.hint }}
        </p>
        <button
          v-if="canReopen"
          type="button"
          class="right-pane__failure-reopen"
          data-testid="reopen-run"
          :disabled="reopenMutation.isLoading.value"
          @click="onReopen"
        >
          {{ reopenMutation.isLoading.value ? 'Reopening...' : 'Reopen as paused' }}
        </button>
      </aside>
    </header>

    <PauseBanner
      v-if="isPaused"
      :run-id="detail.id"
      :question="pauseQuestion"
      :review-paths="pauseReviewPaths"
      @resumed="onResumed"
    />

    <div class="right-pane__body">
      <OverviewPanel
        v-if="selection.kind === 'overview'"
        :run-id="detail.id"
        :prompt-body="detail.prompt_body"
        :events="events"
        :pending-turns="pendingTurns"
      />
      <IterTimelinePanel
        v-else-if="selection.kind === 'iter'"
        :run-id="detail.id"
        :iter-seq="selection.seq"
        :iters="detail.iters"
        :events="events"
        :pending-turns="pendingTurns"
      />
      <ArtifactPanel
        v-else-if="selection.kind === 'artifact'"
        :run-id="detail.id"
        :path="selection.path"
      />
    </div>

    <ToolCallDetailDrawer
      :open="drawerOpen"
      :payload="drawerPayload"
      @close="closeDrawer"
    />
  </section>
</template>

<style scoped>
.right-pane {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem 1.25rem;
  min-width: 0;
}

.right-pane__header {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.75rem;
}

.right-pane__title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.right-pane__title {
  margin: 0;
  font-size: 1.3rem;
  font-family: var(--font-mono);
}

.right-pane__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}

.right-pane__meta dt {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.right-pane__meta dd {
  margin: 0.15rem 0 0;
  font-weight: 600;
}

.right-pane__actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.right-pane__follow-live {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.3rem 0.65rem;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-dim);
  font: inherit;
  font-size: 0.85em;
  cursor: pointer;
  transition:
    color 80ms ease-out,
    background 80ms ease-out,
    border-color 80ms ease-out;
}

.right-pane__follow-live:hover,
.right-pane__follow-live:focus-visible {
  color: var(--color-text);
  border-color: var(--color-accent);
}

.right-pane__follow-live--on {
  color: var(--color-accent);
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.right-pane__follow-live-glyph {
  font-size: 0.9em;
  line-height: 1;
}

.right-pane__failure {
  position: relative;
  border: 1px solid var(--color-danger-border);
  border-left: 4px solid var(--color-danger-border);
  background: var(--color-danger-bg);
  border-radius: 6px;
  padding: 0.75rem 2.4rem 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.right-pane__failure-close {
  position: absolute;
  top: 0.35rem;
  right: 0.45rem;
  width: 1.8rem;
  height: 1.8rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  color: var(--color-danger-strong);
  font: inherit;
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
}

.right-pane__failure-close:hover,
.right-pane__failure-close:focus-visible {
  border-color: var(--color-danger-border);
  background: var(--color-surface-hover);
}

.right-pane__failure-title {
  font-family: var(--font-mono);
  color: var(--color-danger-strong);
}

.right-pane__failure-marker,
.right-pane__failure-hint {
  margin: 0;
  font-size: 0.9em;
  line-height: 1.4;
}

.right-pane__failure-label {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
  margin-right: 0.4rem;
}

.right-pane__failure-reopen {
  align-self: flex-start;
  margin-top: 0.25rem;
  padding: 0.3rem 0.75rem;
  background: transparent;
  border: 1px solid var(--color-danger-border);
  border-radius: 4px;
  color: var(--color-danger-strong);
  font: inherit;
  font-size: 0.85em;
  cursor: pointer;
  transition:
    background 80ms ease-out,
    border-color 80ms ease-out;
}

.right-pane__failure-reopen:hover:not(:disabled),
.right-pane__failure-reopen:focus-visible:not(:disabled) {
  background: var(--color-surface-hover);
  border-color: var(--color-danger-strong);
}

.right-pane__failure-reopen:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.right-pane__body {
  flex: 1;
  min-height: 0;
}
</style>
