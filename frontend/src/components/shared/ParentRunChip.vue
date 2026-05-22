<script setup lang="ts">
// A small "Parent: <short-id>" chip rendered next to the status badge on a
// child run's detail view (spec.md §9.1, 9e). Closes the upward-navigation
// gap: today nothing links a child back to its parent in the UI.
//
// Renders nothing when `parentRunId` is null (the common case for top-level
// runs). When non-null, links to `/runs/<parentRunId>` via vue-router.

import { computed } from 'vue'

const props = defineProps<{ parentRunId: string | null }>()

const shortId = computed(() =>
  props.parentRunId != null ? props.parentRunId.slice(0, 8) : '',
)
</script>

<template>
  <router-link
    v-if="parentRunId != null"
    :to="{ name: 'run-detail', params: { id: parentRunId } }"
    class="parent-run-chip"
    data-testid="parent-run-chip"
  >
    Parent: {{ shortId }}
  </router-link>
</template>

<style scoped>
.parent-run-chip {
  display: inline-flex;
  align-items: center;
  padding: 0.15em 0.6em;
  border-radius: 999px;
  font-size: 0.78em;
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  font-family: var(--font-mono);
}

.parent-run-chip:hover {
  background: var(--color-surface);
}
</style>
