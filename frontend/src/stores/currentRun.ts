// Ephemeral UI state for the open run-detail view (W4).
//
// Spec §9.2: plain Pinia holds ONLY ephemeral UI state; server data is
// in Colada (`useRunDetailQuery`) and the push stream is in
// `stores/events.ts`. This store is deliberately tiny — selected-iter
// filter (consumed later by W5's Iters pane) and the timeline
// collapse-all preference. It duplicates nothing from Colada.

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useCurrentRunStore = defineStore('current-run', () => {
  /**
   * Selected-iter filter for the timeline / iters pane. `null` = show
   * all iters. The value is the iter SEQ (`IterOut.seq`, a 1-based
   * per-run counter), NOT the iter row id: persisted event rows carry
   * no iter foreign key, so the only way to associate an event with an
   * iter is the `iter_started`/`iter_ended` boundary events whose
   * payloads carry `seq` (see ItersPane.vue's FILTER CONTRACT and
   * TimelinePane.vue). W5's ItersPane writes this; TimelinePane reads
   * it to scope the feed to one iter.
   */
  const selectedIterId = ref<number | null>(null)

  /**
   * When true the timeline renders every row collapsed by default
   * (per-row expand still works). A view-level reading preference.
   */
  const collapseAll = ref(false)

  function selectIter(iterId: number | null): void {
    selectedIterId.value = iterId
  }

  function setCollapseAll(value: boolean): void {
    collapseAll.value = value
  }

  function reset(): void {
    selectedIterId.value = null
    collapseAll.value = false
  }

  return {
    selectedIterId,
    collapseAll,
    selectIter,
    setCollapseAll,
    reset,
  }
})
