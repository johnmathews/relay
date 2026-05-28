<script setup lang="ts">
// Prompt create / edit form (W8). Two modes:
//
//   • create — `mode="create"`: name + body are both editable; submit
//     POSTs `/api/prompts` ({project_id?, name, body}) → version 1. A
//     duplicate `(project_id, name)` → 409 / unknown project → 404,
//     surfaced inline.
//   • edit   — `mode="edit"`: the name is the identity key and is shown
//     read-only; submit PUTs `/api/prompts/{id}` (body only), which is a
//     SNAPSHOT BUMP — the server inserts a NEW version row and KEEPS
//     every old version (history is preserved, never mutated). The UI
//     states this explicitly so the user understands an edit versions.
//
// The body has a raw/preview toggle; preview renders via W6's
// MarkdownRender (already XSS-safe — markdown-it html:false + shiki).
// On success the new/updated Prompt is emitted via `saved` (the parent
// re-selects it); the mutations already invalidate `keys.prompts()` so
// the list refreshes.
//
// Props:
//   mode      — 'create' | 'edit'
//   projectId — the owning project id (sent on create as project_id)
//   prompt    — the prompt being edited (required for mode='edit')
// Emits:
//   saved  — succeeded; payload: the resulting Prompt (new version row)
//   cancel — the form was dismissed

import { ref, computed } from 'vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import {
  useCreatePromptMutation,
  useUpdatePromptMutation,
  ApiError,
  type Prompt,
} from '@/lib/queries'

const props = defineProps<{
  mode: 'create' | 'edit'
  projectId: number
  prompt?: Prompt | null
}>()

const emit = defineEmits<{ saved: [Prompt]; cancel: [] }>()

const name = ref(props.prompt?.name ?? '')
const body = ref(props.prompt?.body ?? '')
/** Body editor view: raw textarea vs. rendered markdown preview. */
const showPreview = ref(false)

const create = useCreatePromptMutation()
const update = useUpdatePromptMutation()

const submitting = computed(
  () => create.isLoading.value || update.isLoading.value,
)

const errorMessage = computed<string | null>(() => {
  const e: unknown = create.error.value ?? update.error.value
  if (e == null) return null
  if (e instanceof ApiError || e instanceof Error) return e.message
  return 'Failed to save prompt.'
})

const canSubmit = computed(
  () =>
    name.value.trim().length > 0 &&
    body.value.trim().length > 0 &&
    !submitting.value,
)

async function onSubmit(): Promise<void> {
  if (!canSubmit.value) return
  try {
    let result: Prompt
    if (props.mode === 'edit') {
      // Identity key (name) is fixed in edit mode; the API takes the
      // body only and snapshot-bumps a new version.
      result = await update.mutateAsync({
        id: props.prompt!.id,
        body: body.value,
      })
    } else {
      result = await create.mutateAsync({
        project_id: props.projectId,
        name: name.value.trim(),
        body: body.value,
      })
    }
    // The mutation's onSuccess already invalidated keys.prompts().
    emit('saved', result)
  } catch {
    // Reflected via create/update.error → errorMessage.
  }
}
</script>

<template>
  <form
    class="prompt-editor"
    data-testid="prompt-editor"
    @submit.prevent="onSubmit"
  >
    <h3 class="prompt-editor__title">
      {{ mode === 'edit' ? 'Edit prompt' : 'New prompt' }}
    </h3>
    <p
      v-if="mode === 'edit'"
      class="prompt-editor__note"
      data-testid="version-note"
    >
      Saving creates a new version. Earlier versions stay readable in the
      version history — editing never overwrites past versions.
    </p>

    <label class="prompt-editor__field">
      <span class="prompt-editor__label">Name</span>
      <input
        v-if="mode === 'create'"
        v-model="name"
        type="text"
        name="name"
        placeholder="Prompt name"
        autocomplete="off"
        data-testid="prompt-name"
        required
      >
      <span
        v-else
        class="prompt-editor__name-fixed"
        data-testid="prompt-name-fixed"
      >
        {{ name }}
      </span>
    </label>

    <div class="prompt-editor__field">
      <div class="prompt-editor__body-head">
        <span class="prompt-editor__label">Body</span>
        <button
          type="button"
          class="prompt-editor__toggle"
          data-testid="preview-toggle"
          @click="showPreview = !showPreview"
        >
          {{ showPreview ? 'Edit' : 'Preview' }}
        </button>
      </div>
      <textarea
        v-show="!showPreview"
        v-model="body"
        class="prompt-editor__body"
        name="body"
        rows="14"
        placeholder="Markdown prompt body…"
        data-testid="prompt-body"
      />
      <div
        v-show="showPreview"
        class="prompt-editor__preview"
        data-testid="prompt-preview"
      >
        <MarkdownRender :source="body" />
      </div>
    </div>

    <p
      v-if="errorMessage"
      class="prompt-editor__error"
      role="alert"
      data-testid="prompt-error"
    >
      {{ errorMessage }}
    </p>

    <div class="prompt-editor__actions">
      <ActionButton
        type="submit"
        :loading="submitting"
        :disabled="!canSubmit"
      >
        {{ mode === 'edit' ? 'Save new version' : 'Create prompt' }}
      </ActionButton>
      <button
        type="button"
        class="prompt-editor__cancel"
        data-testid="prompt-cancel"
        @click="emit('cancel')"
      >
        Cancel
      </button>
    </div>
  </form>
</template>

<style scoped>
.prompt-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.prompt-editor__title {
  margin: 0;
  font-size: 1.05rem;
}

.prompt-editor__note {
  margin: 0;
  font-size: 0.82em;
  color: var(--color-text-dim);
}

.prompt-editor__field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.prompt-editor__label {
  font-size: 0.82em;
  color: var(--color-text-dim);
}

.prompt-editor__body-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.prompt-editor__toggle {
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.2em 0.55em;
  color: var(--color-text-dim);
  font: inherit;
  font-size: 0.8em;
  cursor: pointer;
}

.prompt-editor__toggle:hover {
  color: var(--color-text);
  border-color: var(--color-accent);
}

.prompt-editor input,
.prompt-editor__body {
  padding: 0.45em 0.6em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}

.prompt-editor__body {
  font-family: var(--font-mono);
  resize: vertical;
}

.prompt-editor__name-fixed {
  font-weight: 600;
  padding: 0.2em 0;
}

.prompt-editor__preview {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.6rem 0.85rem;
  background: var(--color-bg);
  min-height: 6rem;
}

.prompt-editor__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.85em;
}

.prompt-editor__actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.prompt-editor__cancel {
  background: none;
  border: none;
  color: var(--color-text-dim);
  font: inherit;
  cursor: pointer;
}

.prompt-editor__cancel:hover {
  color: var(--color-text);
  text-decoration: underline;
}
</style>
