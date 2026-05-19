<script setup lang="ts">
// W7 Artifacts pane (spec §9.1) — "what did the agent actually do?".
// Browses the run's artifacts dir (`data_dir/runs/<id>/`; ADR-25)
// inline: markdown artifacts (improvement-plan.md, evaluation-report.md,
// …) render formatted, code via shiki, binary → download — ALL via the
// SAME shared FileTree + FileViewer used by the W6 project file browser
// (no duplicate tree/viewer; the only difference is the BrowserSource).
//
// The artifacts endpoint family (ADR-25) is a thin adapter over the
// project file browser, so the sandbox guards are identical: binary →
// 415, >5 MiB → 413, sandbox → 400. A 404 on the artifacts ROOT listing
// means EITHER the run doesn't exist OR the run exists but has no
// artifacts dir ("no artifacts for run"). The two are not reliably
// distinguishable from the client (both are a 404 with a `detail`
// string), and from this pane's vantage point — it is only ever mounted
// inside RunDetailView for an already-loaded run — the actionable
// meaning is the same: there is nothing to show yet. So we collapse
// both into one clear empty state (see `noArtifacts`).

import { computed } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import FileTree from '@/components/files/FileTree.vue'
import FileViewer from '@/components/files/FileViewer.vue'
import {
  runArtifactSource,
  asAsyncState,
  ApiError,
} from '@/lib/queries'
import { useBrowserUiStore } from '@/stores/files'

const props = defineProps<{
  /** The run whose artifacts dir to browse. */
  runId: string
}>()

// The shared browser, wired to the run-artifacts source. Its per-source
// ephemeral UI store (keyed `run:<id>`) holds the selection so it never
// collides with the project file browser's state.
const source = computed(() => runArtifactSource(props.runId))
const store = computed(() => useBrowserUiStore(source.value.storeId))
const selectedPath = computed(() => store.value.selectedPath)

// Probe the artifacts ROOT listing directly so we can show the
// "no artifacts" empty state (a 404 here) WITHOUT FileTree having to
// surface backend semantics. FileTree fetches the same key, so this is
// a shared cache entry (no double network request).
const rootListing = source.value.useListing(() => '')
const rootState = asAsyncState(rootListing)

/** The root error as an ApiError (so we can branch on HTTP status). */
const rootApiError = computed<ApiError | null>(() =>
  rootListing.error.value instanceof ApiError
    ? rootListing.error.value
    : null,
)

/**
 * True when the run simply has no artifacts yet: the ADR-25 backend
 * answers a missing run OR a run with no artifacts dir with a 404. We
 * present that as a friendly empty state rather than an error (it is the
 * expected state for a run that hasn't produced artifacts).
 */
const noArtifacts = computed(() => rootApiError.value?.status === 404)

/** A non-404 failure is a real error worth surfacing via AsyncBoundary. */
const realError = computed(() =>
  noArtifacts.value ? null : rootState.error.value,
)

function onSelect(path: string): void {
  store.value.selectFile(path)
}
</script>

<template>
  <section
    class="artifacts-pane"
    data-testid="artifacts-pane"
  >
    <h2 class="artifacts-pane__title">
      Artifacts
    </h2>

    <p
      v-if="noArtifacts"
      class="artifacts-pane__empty"
      data-testid="artifacts-empty"
    >
      This run has no artifacts yet.
    </p>

    <AsyncBoundary
      v-else
      :loading="rootState.isLoading.value"
      :error="realError"
    >
      <div class="artifacts-pane__browser">
        <FileTree
          :source="source"
          aria-label="Run artifacts"
          @select="onSelect"
        />
        <FileViewer
          :source="source"
          :path="selectedPath"
        />
      </div>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.artifacts-pane {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.artifacts-pane__title {
  margin: 0.5rem 0 0;
  font-size: 1.05rem;
}

.artifacts-pane__empty {
  color: var(--color-text-dim);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  padding: 1rem;
  margin: 0;
}

.artifacts-pane__browser {
  display: grid;
  grid-template-columns: minmax(200px, 320px) 1fr;
  gap: 1rem;
  align-items: start;
}
</style>
