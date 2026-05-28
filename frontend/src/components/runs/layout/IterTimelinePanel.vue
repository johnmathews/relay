<script setup lang="ts">
// Right-pane body when selection.kind === 'iter'. Thin wrapper around
// TimelinePane scoped to one iter seq via its existing
// selected-iter-seq prop (the same prop ItersPane drives today).

import TimelinePane from '@/components/runs/TimelinePane.vue'
import ItersPane from '@/components/runs/ItersPane.vue'
import type { StreamEvent, PendingTurn } from '@/stores/events'
import type { Iter } from '@/lib/queries'

defineProps<{
  runId: string
  iterSeq: number
  iters: ReadonlyArray<Iter>
  events: ReadonlyArray<StreamEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
}>()
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

    <!-- Casts: TimelinePane's props are declared as mutable arrays
         (events: StreamEvent[]) but the events store yields ReadonlyArray.
         The cast is template-only — we never mutate. Phase 2 will widen
         TimelinePane's prop types to ReadonlyArray and the cast will go. -->
    <TimelinePane
      :events="(events as StreamEvent[])"
      :selected-iter-seq="iterSeq"
      :pending-turns="(pendingTurns as PendingTurn[])"
      :run-id="runId"
    />

    <!-- The iter-row inspector (existing ItersPane) stays visible for
         status/timing detail of the selected iter. Phase 1 keeps it
         rendered intact below the timeline; later phases (5 — drawer)
         may move per-iter detail into a richer view. -->
    <ItersPane :iters="(iters as Iter[])" />
  </div>
</template>

<style scoped>
.iter-timeline-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.iter-timeline-panel__heading {
  margin: 0;
  font-size: 1.05rem;
}
</style>
