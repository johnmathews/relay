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
// parent locates it and passes it as a prop.
//
// Rendering is INTENTIONALLY MINIMAL: the question is shown as plain
// text in a <pre>, and the answer is a plain <textarea>. The real
// markdown-capable editor/render pipeline is W6 (`lib/render.ts` is a
// stub by mandate); a plain textarea + <pre> question is the correct
// minimal contract for this unit.

import { ref } from 'vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import { useResumeRunMutation, ApiError } from '@/lib/queries'

const props = defineProps<{
  /** The run id to resume. */
  runId: string
  /** The agent's pause question (from the paused iter's signal_args). */
  question: string
}>()

const emit = defineEmits<{
  /** Emitted after a successful resume so the parent can refetch. */
  resumed: []
}>()

const answer = ref('')
const inlineError = ref<string | null>(null)
const resume = useResumeRunMutation()

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
</style>
