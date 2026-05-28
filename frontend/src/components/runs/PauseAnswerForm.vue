<script setup lang="ts">
// Shown only when the run status is `paused`. Renders the agent's pause
// question and collects the operator's answer, then resumes the run via
// `POST /api/runs/{id}/resume {answer}` (the `useResumeRunMutation`
// hook). 409 (not-paused / already-running) and 404 (unknown) surface
// inline; on success the parent refetches detail and reopens the live
// stream.
//
// The OPTIONAL `reviewPaths` prop (14c + 14f — ADR-40/ADR-41) comes
// from the paused iter's `signal_args.review_paths` when the agent
// declared one or more reviewable artifacts via the 14b sentinel
// attribute. When non-empty, a richer review pane renders ABOVE the
// existing question/answer block: it fetches the active artifact,
// exposes a textarea + lazy markdown preview, and a Save button that
// fires `PUT /api/runs/:id/artifacts/{path}`. Length 1 renders the
// existing single-pane layout (no tab bar, byte-identical to 14c);
// length > 1 renders a tab bar across the top — one tab per path,
// per-tab dirty state, one Save in flight at a time across the
// component. The Resume button stays present and is disabled only
// while a Save is in flight on the active tab (the soft-warning for
// unsaved changes on non-active tabs is rendered separately and does
// not block Resume — per plan's notes: an abandoned dirty tab must
// not strand the operator).

import { computed, ref, watch } from 'vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import DiffRender from '@/components/files/DiffRender.vue'
import {
  useResumeRunMutation,
  useArtifactContentQuery,
  useArtifactWriteMutation,
  artifactRawUrl,
  ApiError,
} from '@/lib/queries'

const props = defineProps<{
  /** The run id to resume. */
  runId: string
  /** The agent's pause question (from the paused iter's signal_args). */
  question: string
  /** From the paused iter's signal_args.review_paths (14f — ADR-41).
   *  Empty array means the agent did not declare any reviewable
   *  artifact — the form falls back to the minimal pre-14c contract. */
  reviewPaths?: string[]
}>()

const emit = defineEmits<{
  /** Emitted after a successful resume so the parent can refetch. */
  resumed: []
}>()

const answer = ref('')
const inlineError = ref<string | null>(null)
const resume = useResumeRunMutation()

// ── review-pane state (per-path records, 14f) ──────────────────────────

const paths = computed<string[]>(() => props.reviewPaths ?? [])
const hasReviewPath = computed(() => paths.value.length > 0)
const isMulti = computed(() => paths.value.length > 1)

/** Currently-visible tab. Defaults to paths[0] when non-empty. */
const activeTab = ref<string>('')
watch(
  paths,
  (next) => {
    if (next.length === 0) {
      activeTab.value = ''
      return
    }
    // Keep the existing active tab if still present (e.g. a runtime
    // re-render that adds a new path shouldn't yank focus to the first
    // tab); otherwise fall back to the first path.
    if (!next.includes(activeTab.value)) {
      activeTab.value = next[0]!
    }
  },
  { immediate: true },
)

const activePath = computed<string | null>(() =>
  activeTab.value === '' ? null : activeTab.value,
)

const content = useArtifactContentQuery(
  () => props.runId,
  activePath,
  hasReviewPath,
)

const artifactWrite = useArtifactWriteMutation()

/** Last loaded server-side content per path; the "clean" baseline. */
const loadedBaselines = ref<Record<string, string>>({})
/** Editor buffer per path (textarea v-model on the active tab). */
const dirtyByPath = ref<Record<string, string>>({})
/** "Edited at HH:MM:SS" badge text per path; null after a discard
 *  or before any save lands. */
const savedAtByPath = ref<Record<string, string | null>>({})
/** Per-path save error (mapped from ApiError statuses). */
const saveErrorByPath = ref<Record<string, string | null>>({})
/** Per-path view mode (preview vs diff). Defaults to preview. */
const viewModeByPath = ref<Record<string, 'preview' | 'diff'>>({})

/** True between Save click and the PUT response. Single global flag —
 *  one save in flight at a time across tabs (plan §"locked decisions"). */
const saving = ref(false)

/** Current active tab's loaded baseline, or empty string. */
const loadedContent = computed<string | null>(() => {
  const p = activeTab.value
  if (p === '') return null
  return loadedBaselines.value[p] ?? null
})

/** v-model target for the active tab's textarea. Writes propagate
 *  back into the per-path record. */
const dirty = computed<string>({
  get() {
    const p = activeTab.value
    if (p === '') return ''
    return dirtyByPath.value[p] ?? ''
  },
  set(value: string) {
    const p = activeTab.value
    if (p === '') return
    dirtyByPath.value = { ...dirtyByPath.value, [p]: value }
  },
})

/** ApiError for the active tab's GET, if any. */
const loadError = computed<ApiError | null>(() =>
  content.error.value instanceof ApiError ? content.error.value : null,
)

/**
 * Three reviewable states for the active tab:
 *   'binary' — GET returned 415; textarea hidden, download link shown.
 *   '404'    — GET returned 404; empty textarea + "create at this path".
 *   'editor' — content loaded (or other error) → render the editor.
 */
const reviewState = computed<'binary' | '404' | 'editor'>(() => {
  const e = loadError.value
  if (e?.status === 415) return 'binary'
  if (e?.status === 404) return '404'
  return 'editor'
})

/** Whether the local buffer for a given path differs from its loaded
 *  baseline. Used both for the active tab's button gating and for the
 *  per-tab `*` marker in the tab bar. */
function isPathDirty(p: string): boolean {
  return (dirtyByPath.value[p] ?? '') !== (loadedBaselines.value[p] ?? '')
}
const isDirty = computed(() => {
  const p = activeTab.value
  return p !== '' && isPathDirty(p)
})

/** Soft warning: how many non-active tabs have unsaved changes. */
const otherDirtyCount = computed<number>(() =>
  paths.value.filter((p) => p !== activeTab.value && isPathDirty(p)).length,
)

const viewMode = computed<'preview' | 'diff'>({
  get() {
    const p = activeTab.value
    if (p === '') return 'preview'
    return viewModeByPath.value[p] ?? 'preview'
  },
  set(value: 'preview' | 'diff') {
    const p = activeTab.value
    if (p === '') return
    viewModeByPath.value = { ...viewModeByPath.value, [p]: value }
  },
})

const diffDisabled = computed(() => !isDirty.value)
// When the operator dirties the active tab, the Diff tab becomes
// available but we keep whatever mode they chose. When the active
// textarea returns to clean (via Discard or Save), force its mode
// back to Preview — otherwise the right pane would render an empty
// disabled-Diff state.
watch(isDirty, (nowDirty) => {
  if (!nowDirty) viewMode.value = 'preview'
})

/** Save is disabled while clean, except on 404 (where empty save creates). */
const saveDisabled = computed(() => {
  if (saving.value) return true
  if (reviewState.value === '404') return false
  return !isDirty.value
})

/** Discard is disabled when buffer matches the loaded baseline. */
const discardDisabled = computed(() => !isDirty.value)

const saveError = computed<string | null>(() => {
  const p = activeTab.value
  if (p === '') return null
  return saveErrorByPath.value[p] ?? null
})

const savedAt = computed<string | null>(() => {
  const p = activeTab.value
  if (p === '') return null
  return savedAtByPath.value[p] ?? null
})

/** Direct-bytes URL for the binary fallback link (active tab). */
const downloadHref = computed(() =>
  activeTab.value !== '' ? artifactRawUrl(props.runId, activeTab.value) : '#',
)

// When the active tab's GET resolves, capture the content as the new
// baseline and seed the editor buffer iff the buffer hasn't been
// touched relative to its prior baseline (see 14c comment).
watch(
  () => content.data.value,
  (data) => {
    if (data == null) return
    const p = activeTab.value
    if (p === '') return
    const previousBaseline = loadedBaselines.value[p] ?? ''
    const currentDirty = dirtyByPath.value[p] ?? ''
    if (currentDirty === previousBaseline) {
      dirtyByPath.value = { ...dirtyByPath.value, [p]: data.content }
    }
    loadedBaselines.value = { ...loadedBaselines.value, [p]: data.content }
  },
  { immediate: true },
)

function setSaveError(p: string, msg: string | null): void {
  saveErrorByPath.value = { ...saveErrorByPath.value, [p]: msg }
}

async function onSave(): Promise<void> {
  const p = activeTab.value
  if (p === '') return
  setSaveError(p, null)
  saving.value = true
  try {
    const result = await artifactWrite.mutateAsync({
      runId: props.runId,
      path: p,
      content: dirtyByPath.value[p] ?? '',
    })
    loadedBaselines.value = {
      ...loadedBaselines.value,
      [p]: dirtyByPath.value[p] ?? '',
    }
    savedAtByPath.value = {
      ...savedAtByPath.value,
      [p]: new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }),
    }
    void result
  } catch (e) {
    if (e instanceof ApiError) {
      setSaveError(
        p,
        e.status === 409
          ? `Cannot save: ${e.message}`
          : e.status === 413
            ? 'File is too large to save (over 5 MiB).'
            : e.status === 415
              ? 'Content rejected (binary or invalid).'
              : e.status === 400
                ? `Sandbox violation: ${e.message}`
                : e.message || 'Save failed.',
      )
    } else {
      setSaveError(p, 'Save failed.')
    }
  } finally {
    saving.value = false
  }
}

function onDiscard(): void {
  const p = activeTab.value
  if (p === '') return
  setSaveError(p, null)
  dirtyByPath.value = {
    ...dirtyByPath.value,
    [p]: loadedBaselines.value[p] ?? '',
  }
}

async function onSubmit(): Promise<void> {
  inlineError.value = null
  if (answer.value.trim() === '') {
    inlineError.value = 'An answer is required to resume.'
    return
  }
  try {
    await resume.mutateAsync({
      runId: props.runId,
      answer: answer.value,
    })
    answer.value = ''
    emit('resumed')
  } catch (e) {
    // ApiError carries the HTTP status + parsed body (409 not-paused /
    // already-running, 404 unknown) — show it inline, don't crash.
    if (e instanceof ApiError) {
      inlineError.value =
        e.status === 409
          ? `Cannot resume: ${e.message}`
          : e.status === 404
            ? 'Run not found.'
            : e.message
    } else {
      inlineError.value = 'Failed to resume the run.'
    }
  }
}
</script>

<template>
  <form
    class="pause-form"
    data-testid="pause-answer-form"
    @submit.prevent="onSubmit"
  >
    <h3 class="pause-form__title">
      Run paused — answer to continue
    </h3>

    <!-- 14c/14f review pane: renders only when the paused iter declared
         at least one `review_path`. Independent of the answer block
         below — the operator may save zero, one, or many times before
         resuming. -->
    <section
      v-if="hasReviewPath"
      class="pause-review"
      data-testid="pause-review-pane"
    >
      <!-- 14f: tab bar only when N > 1. N == 1 keeps the single-pane
           layout byte-identical to 14c. -->
      <div
        v-if="isMulti"
        class="pause-review__tabs"
        role="tablist"
        aria-label="Reviewable artifacts"
        data-testid="pause-review-tabs"
      >
        <button
          v-for="p in paths"
          :key="p"
          type="button"
          role="tab"
          class="pause-review__tab"
          :class="{ 'pause-review__tab--active': p === activeTab }"
          :aria-selected="p === activeTab"
          :data-testid="`pause-review-tab-${p}`"
          @click="activeTab = p"
        >
          <span class="pause-review__tab-label">{{ p }}</span>
          <span
            v-if="isPathDirty(p)"
            class="pause-review__tab-dirty"
            aria-label="unsaved changes"
          >*</span>
        </button>
      </div>

      <header class="pause-review__header">
        <span class="pause-form__label">Reviewing</span>
        <code class="pause-review__path">{{ activeTab }}</code>
        <span
          v-if="savedAt"
          class="pause-review__badge"
          data-testid="pause-review-saved-badge"
        >Edited at {{ savedAt }}</span>
      </header>

      <div
        v-if="reviewState === 'binary'"
        class="pause-review__binary"
        data-testid="pause-review-binary"
      >
        This artifact is binary; not editable inline.
        <a
          :href="downloadHref"
          download
          data-testid="pause-review-download"
        >Download</a>
      </div>

      <p
        v-else-if="reviewState === '404'"
        class="pause-review__banner"
        data-testid="pause-review-create"
      >
        File not yet on disk. Saving will create it.
      </p>

      <div
        v-if="reviewState !== 'binary'"
        class="pause-review__view-toggle"
        role="tablist"
        aria-label="Right-pane view mode"
        data-testid="pause-review-view-toggle"
      >
        <button
          type="button"
          role="tab"
          class="pause-review__view-tab"
          :class="{ 'pause-review__view-tab--active': viewMode === 'preview' }"
          :aria-selected="viewMode === 'preview'"
          data-testid="pause-review-view-preview"
          @click="viewMode = 'preview'"
        >
          Preview
        </button>
        <button
          type="button"
          role="tab"
          class="pause-review__view-tab"
          :class="{ 'pause-review__view-tab--active': viewMode === 'diff' }"
          :aria-selected="viewMode === 'diff'"
          :disabled="diffDisabled"
          :title="diffDisabled ? 'No unsaved changes — diff is empty' : ''"
          data-testid="pause-review-view-diff"
          @click="viewMode = 'diff'"
        >
          Diff
        </button>
      </div>

      <div
        v-if="reviewState !== 'binary'"
        class="pause-review__editor"
      >
        <textarea
          v-model="dirty"
          class="pause-review__textarea"
          data-testid="pause-review-textarea"
          spellcheck="false"
          rows="10"
          :disabled="saving"
        />
        <div
          v-if="viewMode === 'preview'"
          class="pause-review__preview"
          data-testid="pause-review-preview"
        >
          <MarkdownRender :source="dirty" />
        </div>
        <div
          v-else
          class="pause-review__preview"
          data-testid="pause-review-diff"
        >
          <DiffRender
            :old-text="loadedContent ?? ''"
            :new-text="dirty"
            :filename="activeTab"
          />
        </div>
      </div>

      <div
        v-if="reviewState !== 'binary'"
        class="pause-review__actions"
      >
        <ActionButton
          type="button"
          :loading="saving"
          :disabled="saveDisabled"
          data-testid="pause-review-save"
          @click="onSave"
        >
          {{ reviewState === '404' ? 'Create' : 'Save' }}
        </ActionButton>
        <ActionButton
          type="button"
          :disabled="discardDisabled || saving"
          data-testid="pause-review-discard"
          @click="onDiscard"
        >
          Discard local changes
        </ActionButton>
      </div>

      <p
        v-if="saveError"
        class="pause-form__error"
        role="alert"
        data-testid="pause-review-error"
      >
        {{ saveError }}
      </p>

      <p
        v-if="isMulti && otherDirtyCount > 0"
        class="pause-review__warning"
        data-testid="pause-review-other-dirty"
      >
        Unsaved changes on {{ otherDirtyCount }} other
        {{ otherDirtyCount === 1 ? 'tab' : 'tabs' }} —
        Resume will not save them.
      </p>
    </section>

    <span class="pause-form__label">Question</span>
    <pre
      class="pause-form__question"
      data-testid="pause-question"
    >{{ question }}</pre>

    <label
      class="pause-form__label"
      for="pause-answer"
    >Your answer</label>
    <textarea
      id="pause-answer"
      v-model="answer"
      class="pause-form__input"
      rows="5"
      data-testid="pause-answer-input"
    />

    <p
      v-if="inlineError"
      class="pause-form__error"
      role="alert"
      data-testid="pause-error"
    >
      {{ inlineError }}
    </p>

    <ActionButton
      type="submit"
      :loading="resume.isLoading.value"
      :disabled="saving"
      data-testid="pause-resume-submit"
    >
      Resume run
    </ActionButton>
  </form>
</template>

<style scoped>
.pause-form {
  border: 1px solid var(--color-warning);
  border-radius: 8px;
  padding: 1rem;
  background: var(--color-warning-bg);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.pause-form__title {
  margin: 0 0 0.4rem;
  font-size: 1rem;
}

.pause-form__label {
  font-size: 0.74em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
}

.pause-form__question {
  margin: 0 0 0.6rem;
  padding: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 0.86em;
}

.pause-form__input {
  width: 100%;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  resize: vertical;
}

.pause-form__error {
  color: var(--color-danger);
  margin: 0.3rem 0;
  font-size: 0.88em;
}

.pause-review {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.7rem 0.8rem;
  background: var(--color-surface);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.pause-review__tabs {
  display: flex;
  gap: 0.25rem;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.3rem;
}

.pause-review__tab {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 4px 4px 0 0;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.84em;
  font-family: var(--font-mono);
  cursor: pointer;
}

.pause-review__tab--active {
  background: var(--color-warning-bg-strong);
  border-color: var(--color-warning);
}

.pause-review__tab-dirty {
  color: var(--color-warning);
  font-weight: bold;
}

.pause-review__header {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.pause-review__path {
  font-family: var(--font-mono);
  font-size: 0.88em;
  color: var(--color-text);
}

.pause-review__badge {
  font-size: 0.78em;
  color: var(--color-text-dim);
  font-style: italic;
}

.pause-review__banner {
  margin: 0;
  padding: 0.4rem 0.6rem;
  border-radius: 4px;
  background: var(--color-warning-bg-strong);
  color: var(--color-text);
  font-size: 0.85em;
}

.pause-review__binary {
  padding: 0.5rem;
  color: var(--color-text-dim);
  font-size: 0.9em;
}

.pause-review__editor {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.6rem;
  align-items: stretch;
}

.pause-review__textarea {
  width: 100%;
  min-height: 14em;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-mono);
  font-size: 0.86em;
  resize: vertical;
}

.pause-review__preview {
  min-height: 14em;
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  overflow: auto;
  font-size: 0.9em;
}

.pause-review__view-toggle {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0.25rem;
}

.pause-review__view-tab {
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.84em;
  cursor: pointer;
}

.pause-review__view-tab:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.pause-review__view-tab--active {
  background: var(--color-warning-bg-strong);
  border-color: var(--color-warning);
}

.pause-review__actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.pause-review__warning {
  margin: 0.3rem 0 0;
  padding: 0.4rem 0.6rem;
  border-radius: 4px;
  background: var(--color-warning-bg);
  color: var(--color-text-dim);
  font-size: 0.85em;
}

@media (max-width: 800px) {
  .pause-review__editor {
    grid-template-columns: 1fr;
  }
}
</style>
