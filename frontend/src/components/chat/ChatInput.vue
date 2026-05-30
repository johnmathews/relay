<script setup lang="ts">
// Chat composer (W4 — docs/proposals/chat-mode.md). A growing-textarea
// at the bottom of the chat view: Enter submits, Shift+Enter inserts a
// newline. Disabled while the run is not paused — the user can only
// send when pi has finished the previous turn (the conversational
// counterpart to the `paused` state: an iter is mid-flight on
// `running`, so a new message would race the loop's reply).
//
// Submission flow: optimistically clear the textarea AFTER the resume
// mutation resolves with success; on `ApiError` re-surface the text and
// render an inline message. The textarea text is what becomes the next
// iter's prompt_body (W2 chat resume path), so a duplicate submit is
// avoided by the per-mutation in-flight guard rather than by debouncing.
//
// Reused widget: nothing component-level — the textarea is plain HTML
// because the chat composer wants different sizing/affordances from
// `PauseAnswerForm` (no review pane, no Save button, Enter-to-submit).

import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, useResumeRunMutation } from '@/lib/queries'

const props = defineProps<{
  /** The chat run id (POST /api/runs/:id/resume target). */
  runId: string
  /** Current run status; gates the disabled state of the composer. */
  status: string
}>()

const emit = defineEmits<{
  /** Fired after a successful submit. Parent uses it to scroll the
   *  transcript to the bottom and re-open the live stream from the
   *  current cursor (same pattern as PauseAnswerForm). */
  sent: []
}>()

const draft = ref('')
const inlineError = ref<string | null>(null)
const resume = useResumeRunMutation()
const textarea = ref<HTMLTextAreaElement | null>(null)

/**
 * Send-blocked while: the run is not paused (the only state in which a
 * resume is legal — running / awaiting_children / any terminal status
 * would 409 server-side), the resume mutation is in flight (prevents
 * double-submit), or the draft is whitespace-only (matches
 * PauseAnswerForm's "answer is required" guard).
 */
const submitting = computed(() => resume.isLoading.value)
const sendable = computed(
  () =>
    props.status === 'paused' &&
    !submitting.value &&
    draft.value.trim() !== '',
)

const closed = computed(
  () => props.status === 'closed' || props.status === 'cancelled' ||
        props.status === 'done' || props.status === 'failed',
)

/**
 * Disabled-reason copy below the input — only renders when the composer
 * is unsendable for a non-empty reason (i.e. NOT because the textarea is
 * empty — that's the expected default state, not a user-fixable error).
 */
const statusHint = computed<string>(() => {
  if (closed.value) return 'This chat has ended.'
  if (props.status === 'running') return 'Waiting for the agent…'
  return ''
})

async function submit(): Promise<void> {
  if (!sendable.value) return
  inlineError.value = null
  const text = draft.value
  try {
    await resume.mutateAsync({ runId: props.runId, answer: text })
    draft.value = ''
    autoresize()
    emit('sent')
    // Refocus after a successful send so the composer is ready for the
    // next message without a click — matches the chat-app reflex.
    await nextTick()
    textarea.value?.focus()
  } catch (e) {
    if (e instanceof ApiError) {
      inlineError.value =
        e.status === 409
          ? 'Cannot send right now — the chat is busy or has ended.'
          : e.status === 404
            ? 'This chat no longer exists.'
            : e.message
    } else {
      inlineError.value = 'Failed to send the message.'
    }
  }
}

function onKeydown(ev: KeyboardEvent): void {
  // Shift+Enter (or with any modifier the user might use for newline)
  // falls through to the textarea's native behaviour. Bare Enter is the
  // submit gesture — the dominant convention across chat UIs.
  if (ev.key !== 'Enter') return
  if (ev.shiftKey || ev.altKey || ev.ctrlKey || ev.metaKey) return
  // IME composition: don't intercept Enter while the user is committing
  // an input-method candidate. The browser fires `keydown` with
  // `isComposing=true` during composition; treating that as submit
  // would eat the candidate-selection keystroke.
  if (ev.isComposing) return
  ev.preventDefault()
  void submit()
}

/**
 * Auto-grow the textarea to fit content up to a fixed cap. The textarea
 * starts at one row and expands as the draft grows; past the cap a
 * vertical scrollbar takes over so a 200-line paste doesn't push the
 * transcript off-screen. We measure via scrollHeight after resetting
 * the height to 'auto' so a delete shrinks the box.
 */
const MAX_HEIGHT_PX = 220
function autoresize(): void {
  const el = textarea.value
  if (el == null) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`
}

watch(draft, () => {
  // Schedule after the DOM reflects the new value (Vue's reactivity is
  // post-DOM by default for `v-model`, but `autoresize` reads
  // scrollHeight, which needs the textarea's content to match).
  void nextTick(autoresize)
})
</script>

<template>
  <form
    class="chat-input"
    data-testid="chat-input-form"
    @submit.prevent="submit"
  >
    <div
      v-if="inlineError"
      class="chat-input__error"
      role="alert"
    >
      {{ inlineError }}
    </div>

    <div class="chat-input__row">
      <textarea
        ref="textarea"
        v-model="draft"
        class="chat-input__textarea"
        data-testid="chat-input-textarea"
        rows="1"
        :placeholder="closed ? 'Chat ended.' : 'Message…'"
        :disabled="closed"
        :aria-label="'Message'"
        @keydown="onKeydown"
      />
      <button
        type="submit"
        class="chat-input__send"
        data-testid="chat-input-send"
        :disabled="!sendable"
        :aria-busy="submitting"
      >
        <span v-if="submitting">…</span>
        <span v-else>Send</span>
      </button>
    </div>

    <div
      v-if="statusHint"
      class="chat-input__hint"
      data-testid="chat-input-hint"
    >
      {{ statusHint }}
    </div>
  </form>
</template>

<style scoped>
.chat-input {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.75rem 1rem 1rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

.chat-input__row {
  display: flex;
  align-items: flex-end;
  gap: 0.6rem;
}

.chat-input__textarea {
  flex: 1;
  min-height: 2.4rem;
  max-height: 220px;
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.95rem;
  line-height: 1.4;
  resize: none;
  overflow-y: auto;
  /* Subtle focus ring — distinct from a hard outline so the composer
     reads as the page's primary surface, not a generic form field. */
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.chat-input__textarea:focus-visible {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--color-accent) 25%, transparent);
}

.chat-input__textarea:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.chat-input__send {
  align-self: flex-end;
  height: 2.4rem;
  padding: 0 1.1rem;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--color-accent);
  color: var(--color-accent-fg);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 120ms ease, opacity 120ms ease;
}

.chat-input__send:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.chat-input__send:not(:disabled):hover,
.chat-input__send:not(:disabled):focus-visible {
  outline: none;
  filter: brightness(1.08);
}

.chat-input__error {
  font-size: 0.85rem;
  color: var(--color-danger);
}

.chat-input__hint {
  font-size: 0.8rem;
  color: var(--color-text-dim);
}
</style>
