<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import type { RunView } from '@/lib/runView'

interface IterRow {
  seq: number
  phase: string | null
  status_kind?: string | null
}

interface ChildRow {
  id: string
  status: string
}

const props = defineProps<{
  runId: string
  selection: RunView
  iters: ReadonlyArray<IterRow>
  children: ReadonlyArray<ChildRow>
}>()

const emit = defineEmits<{
  (e: 'update:view', view: RunView): void
}>()

const isOverviewSelected = computed(() => props.selection.kind === 'overview')

function isIterSelected(seq: number): boolean {
  return props.selection.kind === 'iter' && props.selection.seq === seq
}

function selectOverview(): void {
  emit('update:view', { kind: 'overview' })
}

function selectIter(seq: number): void {
  emit('update:view', { kind: 'iter', seq })
}

const childCount = computed(() => props.children.length)
</script>

<template>
  <aside
    class="run-sidebar"
    role="listbox"
    aria-orientation="vertical"
    aria-label="Run navigation"
    data-testid="run-sidebar"
  >
    <button
      type="button"
      role="option"
      class="run-sidebar__row run-sidebar__row--overview"
      :class="{ 'run-sidebar__row--selected': isOverviewSelected }"
      :aria-selected="isOverviewSelected ? 'true' : 'false'"
      data-testid="sidebar-overview"
      @click="selectOverview"
    >
      Overview
    </button>

    <section
      v-if="iters.length > 0"
      role="group"
      aria-labelledby="sidebar-iters-heading"
      class="run-sidebar__section"
    >
      <h3
        id="sidebar-iters-heading"
        class="run-sidebar__heading"
      >
        Iters
        <span class="run-sidebar__count">{{ iters.length }}</span>
      </h3>
      <button
        v-for="iter in iters"
        :key="iter.seq"
        type="button"
        role="option"
        class="run-sidebar__row"
        :class="{ 'run-sidebar__row--selected': isIterSelected(iter.seq) }"
        :aria-selected="isIterSelected(iter.seq) ? 'true' : 'false'"
        :data-testid="`sidebar-iter-${iter.seq}`"
        @click="selectIter(iter.seq)"
      >
        <span class="run-sidebar__row-seq">#{{ iter.seq }}</span>
        <span class="run-sidebar__row-label">{{ iter.phase ?? '—' }}</span>
      </button>
    </section>

    <section
      v-if="childCount > 0"
      role="group"
      aria-labelledby="sidebar-children-heading"
      class="run-sidebar__section"
      data-testid="sidebar-children-section"
    >
      <h3
        id="sidebar-children-heading"
        class="run-sidebar__heading"
      >
        Children
        <span class="run-sidebar__count">{{ childCount }}</span>
      </h3>
      <router-link
        v-for="child in children"
        :key="child.id"
        :to="`/runs/${child.id}`"
        class="run-sidebar__row run-sidebar__row--link"
        :data-testid="`sidebar-child-${child.id}`"
      >
        <StatusBadge :status="child.status" />
        <span class="run-sidebar__row-label">{{ child.id.slice(0, 14) }}</span>
      </router-link>
    </section>
  </aside>
</template>

<style scoped>
.run-sidebar {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 0.5rem;
  border-right: 1px solid var(--color-border);
  background: var(--color-surface);
  min-height: 100%;
}

.run-sidebar__section {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin-top: 0.5rem;
}

.run-sidebar__heading {
  margin: 0 0 0.25rem;
  padding: 0 0.5rem;
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-dim);
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.run-sidebar__count {
  font-size: 0.9em;
  color: var(--color-text-dim);
}

.run-sidebar__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  font: inherit;
  cursor: pointer;
  text-decoration: none;
}

.run-sidebar__row:hover {
  background: var(--color-surface-hover, rgba(255, 255, 255, 0.04));
}

.run-sidebar__row--selected {
  border-color: var(--color-accent, #4a90d9);
  background: rgba(74, 144, 217, 0.08);
}

.run-sidebar__row-seq {
  font-family: var(--font-mono);
  color: var(--color-text-dim);
  min-width: 2rem;
}

.run-sidebar__row-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-sidebar__row--overview {
  font-weight: 600;
}
</style>
