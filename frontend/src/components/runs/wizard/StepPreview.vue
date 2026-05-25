<script setup lang="ts">
// New Run wizard — step 3: preview (spec §9.1, the "not scary" step).
//
// Calls the side-effect-free preview query: PROJECT id in the path
// segment, prompt_id|prompt_body as a query param (+ optional phase).
// By contract this creates NO run row/event/dir (`docs/api.md`).
//
// The COMPLETE returned preamble + body are shown, scrollable and
// untruncated — this is the review the user reads before anything
// happens. A SUCCESSFUL load here is what unlocks the Start button: the
// parent watches `previewQuery.data` and flips its `previewed` flag.

import { computed, watch } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import {
  usePreviewQuery,
  asAsyncState,
  type PreviewSelection,
  type Preview,
} from '@/lib/queries'

const props = defineProps<{
  /** The full selection (project + prompt source + phase), or null. */
  selection: PreviewSelection | null
  /** When true the preview query is active (we're on this step). */
  active: boolean
}>()

const emit = defineEmits<{
  /** Emitted once the preview has successfully loaded for `selection`. */
  loaded: []
}>()

const previewQuery = usePreviewQuery(
  () => props.selection,
  () => props.active,
)
const preview = computed<Preview | null>(
  () => previewQuery.data.value ?? null,
)
const { isLoading, error } = asAsyncState(previewQuery)

// A successful load is the gate for Start. Emit once data lands.
watch(
  () => previewQuery.data.value,
  (data) => {
    if (data != null) emit('loaded')
  },
  { immediate: true },
)
</script>

<template>
  <section class="step">
    <h2 class="step__title">
      3. Preview &amp; start
    </h2>
    <p class="step__hint">
      This is exactly what will run. Nothing has happened yet — no run
      has been created. Review below, then click <strong>Start run</strong>
      to launch.
    </p>

    <AsyncBoundary
      :loading="isLoading"
      :error="error"
    >
      <div
        v-if="preview"
        class="preview"
        data-testid="preview-content"
      >
        <h3 class="preview__heading">
          Preamble
        </h3>
        <pre
          class="preview__block"
          data-testid="preview-preamble"
        >{{ preview.preamble }}</pre>
        <h3 class="preview__heading">
          Prompt body
        </h3>
        <pre
          class="preview__block"
          data-testid="preview-body"
        >{{ preview.body }}</pre>
        <p class="preview__meta">
          Run directory: <code>{{ preview.run_dir }}</code>
        </p>
      </div>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.step__title {
  font-size: 1.1rem;
  margin: 0 0 0.5rem;
}

.step__hint {
  color: var(--color-text-dim);
  font-size: 0.88em;
  margin: 0 0 1rem;
}

.preview__heading {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
  margin: 1rem 0 0.4rem;
}

.preview__block {
  margin: 0;
  padding: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  max-height: 22rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono, monospace);
  font-size: 0.85em;
}

.preview__meta {
  margin-top: 0.8rem;
  font-size: 0.82em;
  color: var(--color-text-dim);
}
</style>
