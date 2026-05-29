<script setup lang="ts">
// Sticky amber-bordered wrapper around PauseAnswerForm. The form's
// internal contract (14c / 14e / 14f — ADR-40 / ADR-41: review_paths
// tabs, diff toggle, ApiError handling, per-tab dirty state) is
// load-bearing and threaded through verbatim; this component only owns
// the chrome (border + sticky positioning) so Resume / the review pane
// stay reachable as the operator scrolls the body below.
import PauseAnswerForm from '@/components/runs/PauseAnswerForm.vue'

defineProps<{
  runId: string
  question: string
  reviewPaths: ReadonlyArray<string>
}>()

const emit = defineEmits<{
  resumed: []
}>()

function onResumed(): void {
  emit('resumed')
}
</script>

<template>
  <aside
    class="pause-banner"
    data-testid="pause-banner"
  >
    <PauseAnswerForm
      :run-id="runId"
      :question="question"
      :review-paths="(reviewPaths as string[])"
      @resumed="onResumed"
    />
  </aside>
</template>

<style scoped>
/* Amber `#e0b341` is the reserved colour for human-attention
   affordances (see memory: yellow-pause-borders-validated). Inline
   rather than tokenised because the same hex is used by other paused
   chrome already; if a token lands later, swap in one place. */
.pause-banner {
  position: sticky;
  top: 0;
  z-index: 2;
  border: 1px solid #e0b341;
  border-left-width: 4px;
  border-radius: 6px;
  background: var(--color-surface);
  padding: 0.75rem 1rem;
  box-shadow: 0 2px 8px var(--color-shadow);
}
</style>
