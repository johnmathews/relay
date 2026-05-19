<script setup lang="ts">
// The project's saved-prompt list (W8). Each row is the LATEST version
// of a `(project_id, name)` (server contract — `GET /api/prompts`).
// Selecting a row emits `select`; a "New prompt" affordance emits
// `new`. This is a refactor of the inline list W5 rendered in
// ProjectView's Prompts pane — same data, same testids
// (`prompt-row-<id>`), now a reusable component so the pane can host
// the [list | detail] CRUD layout without restructuring.
//
// Props:
//   prompts    — the latest-version prompt rows (already fetched by the
//                parent via `usePromptsQuery`)
//   selectedId — the currently-selected prompt id, or null
// Emits:
//   select — a prompt row was clicked (payload: the Prompt)
//   new    — the "New prompt" affordance was clicked

import type { Prompt } from '@/lib/queries'

defineProps<{
  prompts: Prompt[]
  selectedId: number | null
}>()

const emit = defineEmits<{ select: [Prompt]; new: [] }>()
</script>

<template>
  <div class="prompt-list">
    <div class="prompt-list__header">
      <span class="prompt-list__title">Prompts</span>
      <button
        type="button"
        class="prompt-list__new"
        data-testid="new-prompt-button"
        @click="emit('new')"
      >
        New prompt
      </button>
    </div>
    <p
      v-if="prompts.length === 0"
      class="prompt-list__empty"
    >
      No saved prompts for this project.
    </p>
    <ul
      v-else
      class="prompt-list__items"
    >
      <li
        v-for="p in prompts"
        :key="p.id"
      >
        <button
          type="button"
          class="prompt-list__item"
          :class="{ 'prompt-list__item--active': selectedId === p.id }"
          :aria-pressed="selectedId === p.id"
          :data-testid="`prompt-row-${p.id}`"
          @click="emit('select', p)"
        >
          <span class="prompt-list__name">{{ p.name }}</span>
          <span class="prompt-list__ver">v{{ p.version }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.prompt-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.prompt-list__header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.prompt-list__title {
  flex: 1;
  font-weight: 600;
  color: var(--color-text-dim);
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.prompt-list__new {
  padding: 0.35em 0.7em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-weight: 600;
  font-size: 0.85em;
  cursor: pointer;
}

.prompt-list__new:hover {
  border-color: var(--color-accent);
}

.prompt-list__empty {
  color: var(--color-text-dim);
  padding: 0.5rem 0;
}

.prompt-list__items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.prompt-list__item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  text-align: left;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}

.prompt-list__item:hover {
  border-color: var(--color-accent);
}

.prompt-list__item--active {
  border-color: var(--color-accent);
  outline: 2px solid var(--color-accent);
}

.prompt-list__name {
  flex: 1;
  font-weight: 600;
}

.prompt-list__ver {
  font-size: 0.78em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}
</style>
