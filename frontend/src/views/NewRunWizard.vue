<script setup lang="ts">
// New Run wizard (`/projects/:id/new-run`, `:id` = PROJECT id).
// Spec §9.1: a 4-step flow — Prompt → Options → Preview → Start.
//
// Acceptance behaviors (docs/plan.md Phase 4 Verification):
//  1. Preview step shows the full prompt + preamble before commit
//     (StepPreview renders the complete untruncated preamble/body).
//  2. Start is DISABLED until the preview has been viewed: `previewed`
//     only flips true on a successful preview load for the CURRENT
//     selection; changing the prompt/options resets it (re-preview
//     required — the spec's safe "nothing has happened yet" behavior).
//  3. Cancelling creates NO run row: cancel just routes away. Preview
//     is side-effect-free by contract; only `useCreateRunMutation`
//     (step 4) ever issues `POST /api/runs`.
//
// Wizard step/selection state is ephemeral UI state held locally (not
// in a Pinia store, not in the Colada cache).

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ActionButton from '@/components/shared/ActionButton.vue'
import StepPromptSelect from '@/components/runs/wizard/StepPromptSelect.vue'
import StepOptions from '@/components/runs/wizard/StepOptions.vue'
import StepPreview from '@/components/runs/wizard/StepPreview.vue'
import {
  useCreateRunMutation,
  useSettingsDefaultsQuery,
  ApiError,
  type PromptSource,
  type PreviewSelection,
  type RunCreate,
} from '@/lib/queries'

const props = defineProps<{ id: string }>()

const router = useRouter()
const projectId = computed(() => Number(props.id))

// ── ephemeral wizard state ──────────────────────────────────────────
type Step = 0 | 1 | 2 | 3
const step = ref<Step>(0)
const STEP_TITLES = ['Prompt', 'Options', 'Preview', 'Start'] as const

const promptMode = ref<'existing' | 'inline'>('existing')
const promptSource = ref<PromptSource | null>(null)
const maxIters = ref<number | null>(null)
const iterTimeout = ref<number | null>(null)

// Fetch the server-side defaults so the Options step renders concrete
// numbers ("12", "1800") in the inputs instead of an opaque "server
// default" placeholder. Prefill happens once on first arrival; the user
// can still clear a field — a blank value means "use whatever default
// the server has at create time" (handled by `start()` below).
const defaultsQuery = useSettingsDefaultsQuery()
watch(
  () => defaultsQuery.data.value,
  (d) => {
    if (d == null) return
    if (maxIters.value == null) maxIters.value = d.max_iters
    if (iterTimeout.value == null) iterTimeout.value = d.iter_timeout
  },
  { immediate: true },
)

// Gate for Start: true only after a successful preview load for the
// CURRENT selection. Any change to the prompt source or the options
// resets it — the user must re-preview (spec safety: re-confirm what
// will run, since nothing has happened yet at that point).
const previewed = ref(false)
watch([promptSource, maxIters, iterTimeout], () => {
  previewed.value = false
})

const hasPrompt = computed(() => promptSource.value != null)

/** The selection passed to the side-effect-free preview query. */
const previewSelection = computed<PreviewSelection | null>(() => {
  if (promptSource.value == null) return null
  return {
    projectId: projectId.value,
    source: promptSource.value,
  }
})

// ── navigation ──────────────────────────────────────────────────────
const canAdvance = computed(() => {
  if (step.value === 0) return hasPrompt.value
  return true
})

function next(): void {
  if (step.value < 3 && canAdvance.value) {
    step.value = (step.value + 1) as Step
  }
}

function back(): void {
  if (step.value > 0) step.value = (step.value - 1) as Step
}

function onPreviewLoaded(): void {
  previewed.value = true
}

// ── cancel — routes away, NEVER touches POST /api/runs ──────────────
function cancel(): void {
  void router.push(
    Number.isNaN(projectId.value)
      ? '/'
      : `/projects/${projectId.value}`,
  )
}

// ── start — the only path that issues POST /api/runs ────────────────
const createRun = useCreateRunMutation()
const starting = computed(() => createRun.isLoading.value)

const startError = computed<string | null>(() => {
  const e: unknown = createRun.error.value
  if (e == null) return null
  if (e instanceof ApiError || e instanceof Error) return e.message
  return 'Failed to start the run.'
})

const canStart = computed(
  () => hasPrompt.value && previewed.value && !starting.value,
)

async function start(): Promise<void> {
  if (!canStart.value || promptSource.value == null) return
  const body: RunCreate = { project_id: projectId.value }
  if ('promptId' in promptSource.value) {
    body.prompt_id = promptSource.value.promptId
  } else {
    body.prompt_body = promptSource.value.promptBody
  }
  // Only send options the user actually set (blank = server default).
  if (maxIters.value != null) body.max_iters = maxIters.value
  if (iterTimeout.value != null) body.iter_timeout = iterTimeout.value
  // NOTE: no `model` field — RunCreate has none in the MVP schema
  // (see StepOptions.vue); the override input is disabled there.
  try {
    const run = await createRun.mutateAsync(body)
    void router.push(`/runs/${run.id}`)
  } catch {
    // Surfaced inline via `startError`; stay on the wizard.
  }
}
</script>

<template>
  <main class="wizard">
    <header class="wizard__head">
      <h1 class="wizard__title">
        New run
      </h1>
      <ol class="wizard__steps">
        <li
          v-for="(label, i) in STEP_TITLES"
          :key="label"
          :class="{
            'wizard__step--active': i === step,
            'wizard__step--done': i < step,
          }"
        >
          {{ i + 1 }}. {{ label }}
        </li>
      </ol>
    </header>

    <StepPromptSelect
      v-if="step === 0"
      :project-id="projectId"
      :source="promptSource"
      :mode="promptMode"
      @update:source="promptSource = $event"
      @update:mode="promptMode = $event"
    />
    <StepOptions
      v-else-if="step === 1"
      :max-iters="maxIters"
      :iter-timeout="iterTimeout"
      @update:max-iters="maxIters = $event"
      @update:iter-timeout="iterTimeout = $event"
    />
    <StepPreview
      v-else-if="step === 2"
      :selection="previewSelection"
      :active="step === 2"
      @loaded="onPreviewLoaded"
    />
    <section
      v-else
      class="step"
    >
      <h2 class="step__title">
        4. Start
      </h2>
      <p class="step__hint">
        Ready to launch. This creates the run and opens its detail view.
      </p>
      <p
        v-if="startError"
        class="wizard__error"
        role="alert"
      >
        {{ startError }}
      </p>
    </section>

    <footer class="wizard__nav">
      <button
        type="button"
        class="wizard__cancel"
        data-testid="wizard-cancel"
        @click="cancel"
      >
        Cancel
      </button>
      <div class="wizard__nav-right">
        <button
          v-if="step > 0"
          type="button"
          class="wizard__back"
          data-testid="wizard-back"
          @click="back"
        >
          Back
        </button>
        <ActionButton
          v-if="step < 3"
          data-testid="wizard-next"
          :disabled="!canAdvance"
          @click="next"
        >
          Next
        </ActionButton>
        <ActionButton
          v-else
          data-testid="wizard-start"
          :loading="starting"
          :disabled="!canStart"
          @click="start"
        >
          Start run
        </ActionButton>
      </div>
    </footer>
  </main>
</template>

<style scoped>
.wizard {
  max-width: 820px;
  margin: 0 auto;
  padding: 1.5rem;
}

.wizard__title {
  font-size: 1.4rem;
  margin: 0 0 1rem;
}

.wizard__steps {
  display: flex;
  gap: 1rem;
  list-style: none;
  padding: 0;
  margin: 0 0 1.5rem;
  font-size: 0.85em;
  color: var(--color-text-dim);
  flex-wrap: wrap;
}

.wizard__step--active {
  color: var(--color-accent);
  font-weight: 700;
}

.wizard__step--done {
  color: var(--color-text);
}

.wizard__error {
  color: #ff6b6b;
  font-size: 0.88em;
  margin: 0.5rem 0 0;
}

.wizard__nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 2rem;
  border-top: 1px solid var(--color-border);
  padding-top: 1rem;
}

.wizard__nav-right {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.wizard__cancel,
.wizard__back {
  background: none;
  border: none;
  color: var(--color-text-dim);
  font: inherit;
  cursor: pointer;
}

.wizard__cancel:hover,
.wizard__back:hover {
  color: var(--color-text);
  text-decoration: underline;
}
</style>
