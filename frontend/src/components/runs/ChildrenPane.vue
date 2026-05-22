<script setup lang="ts">
// Children pane (spec.md §9.1, 9e) — lists a parent run's direct child
// runs dispatched via fanout. Conditional: renders nothing until the
// first child appears.
//
// Data sources:
//   - `useRunChildrenQuery(runId)` → the child run rows (status, branch,
//     started_at). Refetched on each fanout lifecycle event via the
//     events store's INVALIDATING_KINDS (no polling).
//   - `useEventsStore().events` → the parent's SSE stream, already in
//     memory. We read `role` from each `subagent_dispatch` event and
//     `summary` from each `subagent_return` event, keyed by
//     `child_run_id`.
//
// One row per direct child. Each row: status badge · short-id link ·
// role · branch · summary excerpt. The short id routes to `/runs/<id>`.

import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useRunChildrenQuery } from '@/lib/queries'
import { useEventsStore } from '@/stores/events'

const props = defineProps<{ runId: string }>()

const childrenQuery = useRunChildrenQuery(() => props.runId)
const children = computed(() => childrenQuery.data.value ?? [])

const eventsStore = useEventsStore()

/** child_run_id → role (from subagent_dispatch payload). */
const rolesByChildId = computed(() => {
  const map = new Map<string, string>()
  for (const ev of eventsStore.events) {
    if (ev.kind !== 'subagent_dispatch') continue
    const cid = ev.payload.child_run_id
    const role = ev.payload.role
    if (typeof cid === 'string' && typeof role === 'string') {
      map.set(cid, role)
    }
  }
  return map
})

/** child_run_id → summary (from subagent_return payload). */
const summariesByChildId = computed(() => {
  const map = new Map<string, string>()
  for (const ev of eventsStore.events) {
    if (ev.kind !== 'subagent_return') continue
    const cid = ev.payload.child_run_id
    const summary = ev.payload.summary
    if (typeof cid === 'string' && typeof summary === 'string') {
      map.set(cid, summary)
    }
  }
  return map
})

function shortId(id: string): string {
  return id.slice(0, 8)
}
</script>

<template>
  <section
    v-if="children.length > 0"
    class="children-pane"
    data-testid="children-pane"
  >
    <h2 class="children-pane__title">
      Children ({{ children.length }})
    </h2>
    <ul class="children-pane__list">
      <li
        v-for="child in children"
        :key="child.id"
        :data-testid="`children-row-${child.id}`"
        class="children-pane__row"
      >
        <StatusBadge :status="child.status" />
        <router-link
          :to="{ name: 'run-detail', params: { id: child.id } }"
          class="children-pane__id"
        >
          {{ shortId(child.id) }}
        </router-link>
        <span
          v-if="rolesByChildId.get(child.id)"
          class="children-pane__role"
        >
          {{ rolesByChildId.get(child.id) }}
        </span>
        <span
          v-if="child.branch"
          class="children-pane__branch"
        >
          {{ child.branch }}
        </span>
        <span
          v-if="summariesByChildId.get(child.id)"
          class="children-pane__summary"
        >
          {{ summariesByChildId.get(child.id) }}
        </span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.children-pane {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.children-pane__title {
  margin: 0.5rem 0 0;
  font-size: 1.05rem;
}

.children-pane__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.children-pane__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.children-pane__id {
  font-family: var(--font-mono);
  font-weight: 600;
  text-decoration: none;
  color: var(--color-accent);
}

.children-pane__id:hover {
  text-decoration: underline;
}

.children-pane__role {
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.children-pane__branch {
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.children-pane__summary {
  font-size: 0.85em;
  color: var(--color-text);
  margin-left: auto;
  max-width: 40ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
