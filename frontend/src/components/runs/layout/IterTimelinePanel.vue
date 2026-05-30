<script setup lang="ts">
// Right-pane body when selection.kind === 'iter'. TimelinePane scoped
// to one iter via its existing selected-iter-seq prop, with the
// EventKindFilter chip row above it. The chip row controls per-category
// visibility on the timeline.

import { computed } from 'vue'
import EventKindFilter from '@/components/runs/EventKindFilter.vue'
import TimelinePane from '@/components/runs/TimelinePane.vue'
import type { StreamEvent, PendingTurn } from '@/stores/events'
import type { Iter } from '@/lib/queries'
import {
  classifyEvent,
  KIND_CATEGORIES,
  type KindCategory,
} from '@/lib/eventKinds'

const props = defineProps<{
  runId: string
  iterSeq: number
  iters: ReadonlyArray<Iter>
  events: ReadonlyArray<StreamEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
}>()

/**
 * Per-category counts scoped to THIS iter. Re-runs the same boundary
 * walk TimelinePane uses internally so the chip counts match the
 * visible row count exactly. Folding tool_use_* pairs into "one card
 * each" is the same heuristic as OverviewPanel.
 */
const counts = computed<Record<KindCategory, number>>(() => {
  const acc = Object.fromEntries(
    KIND_CATEGORIES.map((c) => [c, 0]),
  ) as Record<KindCategory, number>
  let openIter: number | null = null
  for (const ev of props.events) {
    const evSeq =
      typeof ev.payload.seq === 'number' ? ev.payload.seq : null
    let belongs: boolean
    if (ev.kind === 'iter_started') {
      belongs = evSeq === props.iterSeq
      openIter = evSeq
    } else if (ev.kind === 'iter_ended') {
      belongs = evSeq === props.iterSeq || openIter === props.iterSeq
      openIter = null
    } else {
      belongs = openIter === props.iterSeq
    }
    if (belongs) acc[classifyEvent(ev)] += 1
  }
  acc.tool = Math.ceil(acc.tool / 2)
  return acc
})
</script>

<template>
  <div
    class="iter-timeline-panel"
    data-testid="iter-timeline-panel"
  >
    <header class="iter-timeline-panel__header">
      <h2 class="iter-timeline-panel__heading">
        Iter #{{ iterSeq }}
      </h2>
    </header>

    <EventKindFilter :counts="counts" />
    <TimelinePane
      :events="events"
      :selected-iter-seq="iterSeq"
      :pending-turns="pendingTurns"
      :run-id="runId"
      empty-message="Iter started — no events yet."
    />
  </div>
</template>

<style scoped>
.iter-timeline-panel {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.iter-timeline-panel__heading {
  margin: 0;
  font-size: 1.05rem;
}
</style>
