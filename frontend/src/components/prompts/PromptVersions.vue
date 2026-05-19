<script setup lang="ts">
// Read-only prompt version history (W8). Lists every version of a
// prompt ascending (`GET /api/prompts/{id}/versions`); selecting a
// version renders that version's body via W6's MarkdownRender —
// READ-ONLY by design. There are NO edit / delete affordances on a
// historical version: version history is strictly read-only (Phase-4
// verification criterion + spec §9.1). Edits go through PromptEditor
// (which snapshot-bumps a new version); this view only inspects.
//
// Props:
//   promptId — the prompt id whose history to load (any version's id
//              resolves the same `(project_id, name)` history server-side)
// Emits:
//   close — the history panel was dismissed

import { ref, computed, watch } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import {
  usePromptVersionsQuery,
  asAsyncState,
  type Prompt,
} from '@/lib/queries'

const props = defineProps<{ promptId: number }>()
const emit = defineEmits<{ close: [] }>()

const promptId = computed(() => props.promptId)
const versionsQuery = usePromptVersionsQuery(promptId)
const versions = computed<Prompt[]>(() => versionsQuery.data.value ?? [])
const versionsState = asAsyncState(versionsQuery)

/** The version row whose body is shown (ephemeral UI state). */
const selected = ref<Prompt | null>(null)

// Default-select the latest (last, list is asc) once data arrives /
// when the prompt changes.
watch(
  versions,
  (vs) => {
    if (vs.length === 0) {
      selected.value = null
      return
    }
    const stillThere =
      selected.value != null &&
      vs.some((v) => v.id === selected.value!.id)
    if (!stillThere) selected.value = vs[vs.length - 1] ?? null
  },
  { immediate: true },
)
</script>

<template>
  <section
    class="prompt-versions"
    data-testid="prompt-versions"
  >
    <header class="prompt-versions__head">
      <h3 class="prompt-versions__title">
        Version history
      </h3>
      <span
        class="prompt-versions__readonly"
        data-testid="versions-readonly"
      >
        Read-only — past versions cannot be edited or deleted.
      </span>
      <button
        type="button"
        class="prompt-versions__close"
        data-testid="versions-close"
        @click="emit('close')"
      >
        Close
      </button>
    </header>

    <AsyncBoundary
      :loading="versionsState.isLoading.value"
      :error="versionsState.error.value"
    >
      <p
        v-if="versions.length === 0"
        class="prompt-versions__empty"
      >
        No version history.
      </p>
      <div
        v-else
        class="prompt-versions__grid"
      >
        <ul class="prompt-versions__list">
          <li
            v-for="v in versions"
            :key="v.id"
          >
            <button
              type="button"
              class="prompt-versions__item"
              :class="{
                'prompt-versions__item--active': selected?.id === v.id,
              }"
              :aria-pressed="selected?.id === v.id"
              :data-testid="`version-row-${v.version}`"
              @click="selected = v"
            >
              <span class="prompt-versions__ver">v{{ v.version }}</span>
              <span class="prompt-versions__when">{{ v.created_at }}</span>
            </button>
          </li>
        </ul>
        <div
          class="prompt-versions__body"
          data-testid="version-body"
        >
          <p
            v-if="!selected"
            class="prompt-versions__empty"
          >
            Select a version to view it.
          </p>
          <MarkdownRender
            v-else
            :source="selected.body"
          />
        </div>
      </div>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.prompt-versions {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.prompt-versions__head {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.prompt-versions__title {
  margin: 0;
  font-size: 1.05rem;
}

.prompt-versions__readonly {
  flex: 1;
  font-size: 0.8em;
  color: var(--color-text-dim);
}

.prompt-versions__close {
  background: none;
  border: none;
  color: var(--color-text-dim);
  font: inherit;
  cursor: pointer;
}

.prompt-versions__close:hover {
  color: var(--color-text);
  text-decoration: underline;
}

.prompt-versions__empty {
  color: var(--color-text-dim);
  padding: 0.5rem 0;
}

.prompt-versions__grid {
  display: grid;
  grid-template-columns: minmax(140px, 200px) 1fr;
  gap: 1rem;
  align-items: start;
}

.prompt-versions__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.prompt-versions__item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  width: 100%;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}

.prompt-versions__item:hover {
  border-color: var(--color-accent);
}

.prompt-versions__item--active {
  border-color: var(--color-accent);
  outline: 2px solid var(--color-accent);
}

.prompt-versions__ver {
  font-weight: 600;
  font-family: var(--font-mono);
}

.prompt-versions__when {
  font-size: 0.74em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.prompt-versions__body {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.85rem 1rem;
  background: var(--color-surface);
}
</style>
