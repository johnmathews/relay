<script setup lang="ts">
// Right-pane body when selection.kind === 'overview'. Renders the
// run's prompt + the cross-iter live timeline (TimelinePane with
// selectedIterSeq = null = no scope filter).

import TimelinePane from '@/components/runs/TimelinePane.vue'
import type { StreamEvent, PendingTurn } from '@/stores/events'

defineProps<{
  runId: string
  promptBody: string
  events: ReadonlyArray<StreamEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
}>()
</script>

<template>
  <div
    class="overview-panel"
    data-testid="overview-panel"
  >
    <section class="overview-panel__prompt">
      <h2 class="overview-panel__heading">
        Prompt
      </h2>
      <pre class="overview-panel__prompt-body">{{ promptBody }}</pre>
    </section>

    <section class="overview-panel__timeline">
      <h2 class="overview-panel__heading">
        Timeline
      </h2>
      <TimelinePane
        :events="(events as StreamEvent[])"
        :selected-iter-seq="null"
        :pending-turns="(pendingTurns as PendingTurn[])"
        :run-id="runId"
      />
    </section>
  </div>
</template>

<style scoped>
.overview-panel {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.overview-panel__heading {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
}

.overview-panel__prompt-body {
  margin: 0;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  font-family: var(--font-mono);
  font-size: 0.85em;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 30vh;
  overflow-y: auto;
}
</style>
