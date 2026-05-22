<script setup lang="ts">
// Shown only when the run status is `paused`. Renders the agent's pause
// question and collects the operator's answer, then resumes the run via
// `POST /api/runs/{id}/resume {answer}` (the `useResumeRunMutation`
// hook). 409 (not-paused / already-running) and 404 (unknown) surface
// inline; on success the parent refetches detail and reopens the live
// stream.
//
// The question text comes from the run-detail `iters[]`: the paused
// iter has `signal_kind === 'pause'` and `signal_args.question`. The
// parent locates it and passes it as a prop. The OPTIONAL `reviewPath`
// prop (14c — ADR-40) comes from the same iter's `signal_args.review_path`
// when the agent declared a reviewable artifact via the 14b sentinel
// attribute. When present, a richer review pane renders ABOVE the
// existing question/answer block: it fetches the artifact, exposes a
// textarea + lazy markdown preview, and a Save button that fires
// `PUT /api/runs/:id/artifacts/{path}`. The Resume button is
// independent — it stays present and labelled "Resume run" — and is
// disabled ONLY while a Save is in flight (proposal §"Tradeoffs"
// choice (a)). When `reviewPath` is absent (any pre-14b paused run, or
// a 14b skill that didn't opt in) the form is byte-for-byte the
// previous minimal contract: question pre + answer textarea + submit.

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
  /** From the paused iter's signal_args; absent when the agent did
   *  not declare a reviewable artifact (14b). */
  reviewPath?: string | null
}>()

const emit = defineEmits<{
  /** Emitted after a successful resume so the parent can refetch. */
  resumed: []
}>()

const answer = ref('')
const inlineError = ref<string | null>(null)
const resume = useResumeRunMutation()

// ── 14c review pane state ────────────────────────────────────────────
//
// All review-pane state is gated on a non-empty `reviewPath` — when the
// prop is null/empty the form falls back to the minimal pre-14c
// behaviour. The Colada query for the artifact is `enabled` only when
// `reviewPath` is a non-empty string, so a non-review pause makes no
// extra network call.

/** True iff the agent declared a reviewable artifact for this pause. */
const hasReviewPath = computed(
  () =>
    typeof props.reviewPath === 'string' && props.reviewPath.length > 0,
)

const reviewPathValue = computed(() =>
  hasReviewPath.value ? (props.reviewPath as string) : null,
)

const content = useArtifactContentQuery(
  () => props.runId,
  reviewPathValue,
  hasReviewPath,
)

const artifactWrite = useArtifactWriteMutation()

/** Last loaded server-side content; the "clean" baseline for the
 *  textarea. `null` until the first GET resolves. */
const loadedContent = ref<string | null>(null)
/** Editor buffer (v-model on the textarea). */
const dirty = ref<string>('')
/** True between Save click and the PUT response; gates Resume. */
const saving = ref(false)
/** Inline error for the save action (mapped from ApiError statuses). */
const saveError = ref<string | null>(null)
/** "Edited at HH:MM:SS" badge after a successful save. */
const savedAt = ref<string | null>(null)
/** The first error from the GET, used to switch into 404/415 states. */
const loadError = computed<ApiError | null>(() =>
  content.error.value instanceof ApiError ? content.error.value : null,
)

/**
 * Three reviewable states:
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

/** Whether the local buffer differs from the loaded baseline. */
const isDirty = computed(() => dirty.value !== (loadedContent.value ?? ''))

// ── 14e Preview/Diff view-mode toggle ────────────────────────────────
//
// Right pane switches between markdown Preview (the loaded view) and a
// unified-diff render of dirty-vs-loadedBaseline. Diff is disabled while
// the textarea is clean (the diff would be empty). The renderer is the
// existing lazy `DiffRender.vue` entry (which dynamic-imports diff2html
// on first render — no eager bundle weight). Baseline updates on Save
// per OQ-5 (locked decisions): single-user MVP means there is no other
// writer, so "dirty-vs-server-current" and "dirty-vs-loaded-baseline"
// collapse to one comparison.
const viewMode = ref<'preview' | 'diff'>('preview')
const diffDisabled = computed(() => !isDirty.value)
// When the operator dirties the textarea, the Diff tab becomes
// available but we keep whatever mode they chose. When the textarea
// returns to clean (via Discard or Save), force Preview — otherwise
// the right pane would render an empty disabled-Diff state.
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

/** Direct-bytes URL for the binary fallback link. */
const downloadHref = computed(() =>
  hasReviewPath.value
    ? artifactRawUrl(props.runId, props.reviewPath as string)
    : '#',
)

// When the GET resolves, capture the content as the new baseline and
// seed the editor buffer. A 404/415 also lands here (data.value stays
// null in that case); only a successful read seeds the buffer.
//
// Operator-edit preservation: we want a cache refetch (SSE-driven, or
// triggered by our own write mutation's onSuccess) to NOT clobber an
// in-progress edit. The right invariant: only seed `dirty` when it
// matches the *previous* baseline (i.e. the operator hasn't typed
// anything that diverges from the last loaded view). Critically, we
// check this BEFORE updating `loadedContent` — otherwise the
// freshly-set baseline would make `isDirty` true and seeding would be
// skipped on the very first load (when `dirty` is empty and there's no
// prior baseline). This was the case-2 regression in the 14c vitest
// suite — see "review pane fetches and renders the artifact on mount".
watch(
  () => content.data.value,
  (data) => {
    if (data == null) return
    const previousBaseline = loadedContent.value ?? ''
    if (dirty.value === previousBaseline) {
      dirty.value = data.content
    }
    loadedContent.value = data.content
  },
  { immediate: true },
)

// 404 path: `content.data.value` stays null, so the data watcher above
// never seeds `dirty` — it remains the initial empty string, which is
// exactly the buffer we want for a "create at this path" save.
//
// 415 path: same — `dirty` stays empty, but the template branches to
// `pause-review-binary` so the textarea is never rendered; nothing to
// reset.

async function onSave(): Promise<void> {
  if (!hasReviewPath.value) return
  saveError.value = null
  saving.value = true
  try {
    const result = await artifactWrite.mutateAsync({
      runId: props.runId,
      path: props.reviewPath as string,
      content: dirty.value,
    })
    loadedContent.value = dirty.value
    savedAt.value = new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    // Touching `result` so a future change (e.g. surfacing sha256 in
    // the badge) doesn't need a structural rewrite.
    void result
  } catch (e) {
    if (e instanceof ApiError) {
      saveError.value =
        e.status === 409
          ? `Cannot save: ${e.message}`
          : e.status === 413
            ? 'File is too large to save (over 5 MiB).'
            : e.status === 415
              ? 'Content rejected (binary or invalid).'
              : e.status === 400
                ? `Sandbox violation: ${e.message}`
                : e.message || 'Save failed.'
    } else {
      saveError.value = 'Save failed.'
    }
  } finally {
    saving.value = false
  }
}

function onDiscard(): void {
  saveError.value = null
  dirty.value = loadedContent.value ?? ''
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

    <!-- 14c review pane: renders only when the paused iter declared a
         `review_path`. Independent of the answer block below — the
         operator may save zero, one, or many times before resuming. -->
    <section
      v-if="hasReviewPath"
      class="pause-review"
      data-testid="pause-review-pane"
    >
      <header class="pause-review__header">
        <span class="pause-form__label">Reviewing</span>
        <code class="pause-review__path">{{ reviewPath }}</code>
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
            :filename="reviewPath ?? ''"
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
  border: 1px solid #e0b341;
  border-radius: 8px;
  padding: 1rem;
  background: rgba(224, 179, 65, 0.07);
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
  color: #ff6b6b;
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
  background: rgba(224, 179, 65, 0.12);
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
  background: rgba(224, 179, 65, 0.18);
  border-color: #e0b341;
}

.pause-review__actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

@media (max-width: 800px) {
  .pause-review__editor {
    grid-template-columns: 1fr;
  }
}
</style>
