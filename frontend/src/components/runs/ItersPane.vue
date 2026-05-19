<script setup lang="ts">
// Iters pane for the run-detail view (spec §9.1 "Run detail view →
// Iters pane"): a scannable list of the run's iters (seq, phase,
// signal_kind, and concise lifecycle metadata). Clicking an iter
// filters the timeline to just that iter's events; clicking it again
// (or "Clear filter") removes the filter.
//
// FILTER CONTRACT (load-bearing — read before changing):
//   The persisted relay event rows do NOT carry an iter id/foreign key
//   (see src/stores/events.ts `StreamEvent` — only seq/kind/payload).
//   The only signal of iter membership in the event stream is the
//   `iter_started` / `iter_ended` boundary events, whose payloads carry
//   the iter SEQ (`payload.seq`; see tests/TimelinePane.spec.ts MIXED
//   and orchestrator/loop.py). So the timeline filter is keyed by iter
//   SEQ, not iter row id: `currentRun.selectedIterId` holds the selected
//   IterOut.seq (a 1-based per-run counter), and TimelinePane computes
//   each event's owning iter by walking the `iter_started`/`iter_ended`
//   boundaries. `currentRun.selectedIterId` is therefore documented as
//   "selected iter SEQ" — this is the smallest correct association given
//   the event schema and avoids touching W4's SSE/store internals.
//
// Source of the iter list: the W4 run-detail Colada query's `iters[]`
// (`RunDetail.iters`, `IterOut`). This pane only READS it + writes the
// ephemeral selection into the `current-run` store; no server data is
// duplicated into Pinia (spec §9.2).

import { computed } from 'vue'
import { useCurrentRunStore } from '@/stores/currentRun'
import type { Iter } from '@/lib/queries'

const props = defineProps<{
  /** The run's iters, from the run-detail query (`RunDetail.iters`). */
  iters: Iter[]
}>()

const currentRun = useCurrentRunStore()

/** The currently selected iter SEQ (filter), or null for "all iters". */
const selectedSeq = computed<number | null>(
  () => currentRun.selectedIterId,
)

/** Click an iter row: select it, or toggle it off if already selected. */
function onSelect(iter: Iter): void {
  currentRun.selectIter(
    selectedSeq.value === iter.seq ? null : iter.seq,
  )
}

function clearFilter(): void {
  currentRun.selectIter(null)
}

const hasIters = computed(() => props.iters.length > 0)
</script>

<template>
  <section
    class="iters-pane"
    data-testid="iters-pane"
  >
    <header class="iters-pane__head">
      <h2 class="iters-pane__title">
        Iters
      </h2>
      <button
        v-if="selectedSeq != null"
        type="button"
        class="iters-pane__clear"
        data-testid="iters-clear-filter"
        @click="clearFilter"
      >
        Clear filter
      </button>
    </header>

    <p
      v-if="!hasIters"
      class="iters-pane__empty"
    >
      No iters yet.
    </p>

    <ol
      v-else
      class="iters-pane__list"
    >
      <li
        v-for="iter in iters"
        :key="iter.id"
      >
        <button
          type="button"
          class="iters-pane__row"
          :class="{
            'iters-pane__row--active': selectedSeq === iter.seq,
          }"
          :aria-pressed="selectedSeq === iter.seq"
          :data-testid="`iter-row-${iter.seq}`"
          @click="onSelect(iter)"
        >
          <span class="iters-pane__seq">#{{ iter.seq }}</span>
          <span class="iters-pane__phase">
            {{ iter.phase ?? '—' }}
          </span>
          <span
            v-if="iter.signal_kind"
            class="iters-pane__signal"
          >
            {{ iter.signal_kind }}
          </span>
          <span
            v-if="iter.exit_reason"
            class="iters-pane__exit"
          >
            {{ iter.exit_reason }}
          </span>
          <span class="iters-pane__state">
            {{ iter.ended_at != null ? 'ended' : 'running' }}
          </span>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.iters-pane {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.iters-pane__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.iters-pane__title {
  margin: 0;
  font-size: 1.05rem;
  flex: 1;
}

.iters-pane__clear {
  padding: 0.3em 0.7em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-size: 0.8em;
  cursor: pointer;
}

.iters-pane__clear:hover {
  border-color: var(--color-accent);
}

.iters-pane__empty {
  color: var(--color-text-dim);
  margin: 0;
}

.iters-pane__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.iters-pane__row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}

.iters-pane__row:hover {
  border-color: var(--color-accent);
}

.iters-pane__row--active {
  border-color: var(--color-accent);
  outline: 2px solid var(--color-accent);
}

.iters-pane__seq {
  font-family: var(--font-mono);
  font-size: 0.85em;
  color: var(--color-text-dim);
}

.iters-pane__phase {
  font-weight: 600;
  flex: 1;
}

.iters-pane__signal,
.iters-pane__exit,
.iters-pane__state {
  font-size: 0.75em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
}
</style>
