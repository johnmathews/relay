<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RunHealthBadge from '@/components/runs/RunHealthBadge.vue'
import ParentRunChip from '@/components/shared/ParentRunChip.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import PauseAnswerForm from '@/components/runs/PauseAnswerForm.vue'
import OverviewPanel from './OverviewPanel.vue'
import IterTimelinePanel from './IterTimelinePanel.vue'
import ArtifactPanel from './ArtifactPanel.vue'
import type { RunView } from '@/lib/runView'
import type { HeartbeatSnapshot } from '@/stores/events'
import type { Iter } from '@/lib/queries'

interface RunDetail {
  id: string
  status: string
  started_at: string | null
  ended_at: string | null
  max_iters: number
  prompt_id: number | null
  prompt_body: string
  parent_run_id: string | null
  iters: ReadonlyArray<Iter>
}

const props = defineProps<{
  detail: RunDetail
  selection: RunView
  events: ReadonlyArray<unknown>
  pendingTurns: ReadonlyArray<unknown>
  lastHeartbeat: HeartbeatSnapshot | null
  childCount: number
  cancelLabel: string
  cancelling: boolean
  pauseQuestion: string
  pauseReviewPaths: ReadonlyArray<string>
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'resumed'): void
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
  const reason = (last as { exit_reason?: string | null } | null)?.exit_reason ?? props.detail.status
  const args = (last as { signal_args?: Record<string, unknown> | null } | null)?.signal_args ?? null
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
        v-if="isCancellable"
        class="right-pane__actions"
      >
        <ActionButton
          :loading="cancelling"
          data-testid="cancel-run"
          @click="onCancel"
        >
          {{ cancelLabel }}
        </ActionButton>
      </div>

      <aside
        v-if="failureInfo"
        class="right-pane__failure"
        data-testid="run-failure-banner"
        :data-reason="failureInfo.reason"
      >
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
      </aside>
    </header>

    <PauseAnswerForm
      v-if="isPaused"
      :run-id="detail.id"
      :question="pauseQuestion"
      :review-paths="(pauseReviewPaths as string[])"
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
  gap: 0.5rem;
}

.right-pane__failure {
  border: 1px solid #d04a4a;
  border-left: 4px solid #d04a4a;
  background: rgba(208, 74, 74, 0.08);
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.right-pane__failure-title {
  font-family: var(--font-mono);
  color: #b03a3a;
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

.right-pane__body {
  flex: 1;
  min-height: 0;
}
</style>
