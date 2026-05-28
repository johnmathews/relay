<script setup lang="ts">
// Right-pane body when selection.kind === 'overview'. Renders the
// run's prompt + the cross-iter live timeline (TimelinePane with
// selectedIterSeq = null = no scope filter) + the Phase-2
// EventKindFilter chip row tied to the URL `&kinds=` param.

import { computed } from 'vue'
import EventKindFilter from '@/components/runs/EventKindFilter.vue'
import TimelinePane from '@/components/runs/TimelinePane.vue'
import type { StreamEvent, PendingTurn } from '@/stores/events'
import { classifyEvent, type KindCategory } from '@/lib/eventKinds'

const props = defineProps<{
  runId: string
  promptBody: string
  events: ReadonlyArray<StreamEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
  kindsFilter: ReadonlySet<KindCategory> | null
}>()

const emit = defineEmits<{
  (e: 'update:kindsFilter', value: ReadonlySet<KindCategory> | null): void
}>()

/**
 * Per-category counts over the cross-iter event list — the chip row
 * always shows the operator how many rows EACH category contributes
 * to the current scope, regardless of which chips are currently on.
 */
const counts = computed<Record<KindCategory, number>>(() => {
  const acc: Record<KindCategory, number> = {
    assistant: 0,
    thinking: 0,
    tool: 0,
    signal: 0,
    other: 0,
  }
  for (const ev of props.events) acc[classifyEvent(ev)] += 1
  // tool_use_start + tool_use_end fold into one card in the timeline.
  // Halve the tool count rounded up so the chip number matches the
  // number of CARDS the operator sees (an in-flight tool has only the
  // start event yet — round up keeps it visible).
  acc.tool = Math.ceil(acc.tool / 2)
  return acc
})

function onUpdate(value: ReadonlySet<KindCategory> | null): void {
  emit('update:kindsFilter', value)
}

function onClear(): void {
  emit('update:kindsFilter', null)
}
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
      <EventKindFilter
        :model-value="kindsFilter"
        :counts="counts"
        @update:model-value="onUpdate"
      />
      <TimelinePane
        :events="events"
        :selected-iter-seq="null"
        :pending-turns="pendingTurns"
        :run-id="runId"
        :kinds-filter="kindsFilter"
        @clear-kinds-filter="onClear"
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
