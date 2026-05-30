<script setup lang="ts">
// Right-pane body when selection.kind === 'overview'. Renders the
// run's prompt + the cross-iter live timeline (TimelinePane with
// selectedIterSeq = null = no scope filter) + the EventKindFilter
// chip row, which controls per-category visibility on the timeline.

import { computed } from 'vue'
import EventKindFilter from '@/components/runs/EventKindFilter.vue'
import TimelinePane from '@/components/runs/TimelinePane.vue'
import type { StreamEvent, PendingTurn } from '@/stores/events'
import {
  classifyEvent,
  KIND_CATEGORIES,
  type KindCategory,
} from '@/lib/eventKinds'

const props = defineProps<{
  runId: string
  promptBody: string
  events: ReadonlyArray<StreamEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
}>()

/**
 * Per-category counts over the cross-iter event list — the chip row
 * shows how many rows EACH category contributes to the current scope.
 */
const counts = computed<Record<KindCategory, number>>(() => {
  const acc = Object.fromEntries(
    KIND_CATEGORIES.map((c) => [c, 0]),
  ) as Record<KindCategory, number>
  for (const ev of props.events) acc[classifyEvent(ev)] += 1
  // tool_use_start + tool_use_end fold into one card in the timeline.
  // Halve the tool count rounded up so the chip number matches the
  // number of CARDS the operator sees (an in-flight tool has only the
  // start event yet — round up keeps it visible).
  acc.tool = Math.ceil(acc.tool / 2)
  return acc
})
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
      <EventKindFilter :counts="counts" />
      <TimelinePane
        :events="events"
        :selected-iter-seq="null"
        :pending-turns="pendingTurns"
        :run-id="runId"
        empty-message="Run hasn't emitted any events yet."
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

.overview-panel__timeline {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
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
