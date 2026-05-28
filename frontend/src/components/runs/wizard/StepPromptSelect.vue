<script setup lang="ts">
// New Run wizard — step 1: prompt selection (spec §9.1).
//
// The user EITHER picks an existing saved prompt for this project
// (selecting one uses `prompt_id`) OR writes a prompt inline in a
// textarea with a lightweight raw/preview toggle (inline uses
// `prompt_body`). Mode + selection are owned by the parent via
// v-model:source / v-model:mode so the parent can detect changes and
// require a re-preview.
//
// The inline "preview" is intentionally MINIMAL: a raw monospace
// render of the textarea content (no markdown-it / shiki / mermaid).
// The full sanitized file-render pipeline is W6; here the user only
// needs to eyeball what they typed, so this is deliberately light (the
// W3 brief explicitly allows a plain `<pre>` for this) — keeping it
// `<pre>` also avoids any untrusted-HTML / `v-html` surface.

import { computed, ref } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import {
  usePromptsQuery,
  asAsyncState,
  type Prompt,
  type PromptSource,
} from '@/lib/queries'

const props = defineProps<{
  projectId: number
  /** Current prompt source (null until the user picks/types something). */
  source: PromptSource | null
  /** Which input mode the user is in. */
  mode: 'existing' | 'inline'
}>()

const emit = defineEmits<{
  'update:source': [PromptSource | null]
  'update:mode': ['existing' | 'inline']
}>()

const promptsQuery = usePromptsQuery(() => props.projectId)
const prompts = computed<Prompt[]>(() => promptsQuery.data.value ?? [])
const { isLoading, error } = asAsyncState(promptsQuery)

// ── existing-prompt selection ───────────────────────────────────────
const selectedPromptId = computed<number | null>(() =>
  props.source != null && 'promptId' in props.source
    ? props.source.promptId
    : null,
)

function selectPrompt(id: number): void {
  emit('update:mode', 'existing')
  emit('update:source', { promptId: id })
}

// ── inline body ─────────────────────────────────────────────────────
const inlineBody = computed<string>(() =>
  props.source != null && 'promptBody' in props.source
    ? props.source.promptBody
    : '',
)

function onInlineInput(ev: Event): void {
  const value = (ev.target as HTMLTextAreaElement).value
  emit('update:mode', 'inline')
  emit('update:source', value.trim() === '' ? null : { promptBody: value })
}

const showInlinePreview = ref(false)
</script>

<template>
  <section class="step">
    <h2 class="step__title">
      1. Choose a prompt
    </h2>

    <div class="step__modes">
      <label>
        <input
          type="radio"
          name="prompt-mode"
          value="inline"
          :checked="mode === 'inline'"
          @change="emit('update:mode', 'inline')"
        >
        Write one inline
      </label>
      <label>
        <input
          type="radio"
          name="prompt-mode"
          value="existing"
          :checked="mode === 'existing'"
          @change="emit('update:mode', 'existing')"
        >
        Use a saved prompt
      </label>
    </div>

    <div v-if="mode === 'existing'">
      <AsyncBoundary
        :loading="isLoading"
        :error="error"
      >
        <ul
          v-if="prompts.length > 0"
          class="prompt-list"
          data-testid="prompt-list"
        >
          <li
            v-for="p in prompts"
            :key="p.id"
          >
            <label class="prompt-list__row">
              <input
                type="radio"
                name="existing-prompt"
                :value="p.id"
                :checked="selectedPromptId === p.id"
                @change="selectPrompt(p.id)"
              >
              <span class="prompt-list__name">{{ p.name }}</span>
              <span class="prompt-list__ver">v{{ p.version }}</span>
            </label>
          </li>
        </ul>
        <p
          v-else
          class="step__empty"
        >
          No saved prompts for this project yet — write one inline.
        </p>
      </AsyncBoundary>
    </div>

    <div v-else>
      <div class="inline-toolbar">
        <button
          type="button"
          :aria-pressed="!showInlinePreview"
          @click="showInlinePreview = false"
        >
          Write
        </button>
        <button
          type="button"
          :aria-pressed="showInlinePreview"
          @click="showInlinePreview = true"
        >
          Preview
        </button>
      </div>
      <textarea
        v-show="!showInlinePreview"
        :value="inlineBody"
        name="inline-body"
        rows="12"
        placeholder="Write the prompt for this run…"
        class="inline-body"
        @input="onInlineInput"
      />
      <!-- Intentionally minimal raw render (no markdown pipeline — W6
           owns that); just shows what the user typed before commit. -->
      <pre
        v-show="showInlinePreview"
        class="inline-preview"
        data-testid="inline-preview"
      >{{ inlineBody }}</pre>
    </div>
  </section>
</template>

<style scoped>
.step__title {
  font-size: 1.1rem;
  margin: 0 0 1rem;
}

.step__modes {
  display: flex;
  gap: 1.5rem;
  margin-bottom: 1rem;
  font-size: 0.9em;
}

.prompt-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.prompt-list__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
}

.prompt-list__name {
  font-weight: 600;
}

.prompt-list__ver {
  color: var(--color-text-dim);
  font-size: 0.82em;
}

.step__empty {
  color: var(--color-text-dim);
}

.inline-toolbar {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
}

.inline-toolbar button {
  font: inherit;
  padding: 0.3em 0.7em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
}

.inline-toolbar button[aria-pressed='true'] {
  background: var(--color-accent);
  color: var(--color-accent-fg);
}

.inline-body {
  width: 100%;
  font: inherit;
  font-family: var(--font-mono, monospace);
  padding: 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  resize: vertical;
}

.inline-preview {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.8rem;
  background: var(--color-surface);
  max-height: 24rem;
  overflow: auto;
}
</style>
