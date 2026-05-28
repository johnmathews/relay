<script setup lang="ts">
// Thin wrapper over `lib/render.ts` renderDiff (spec §9.4 diff). Takes
// two revisions of a file (old/new + filename) and renders a unified or
// side-by-side diff. Reused by W5/W7 for file/artifact comparison.
//
// ── diff2html vs. v-code-diff (W6 DECISION) ──────────────────────────
// We keep `diff2html` (NOT v-code-diff). The Phase-4 scope discussion
// (mandate 5) flagged diff2html as maintenance-inactive and v-code-diff
// as the Vue-native alternative, leaving the call to implementation.
// Decision: stay with diff2html because `docs/spec.md` §9.4 and
// `docs/plan.md` both prescribe it, it is already a pinned dependency
// (^3.4), and this is a single-user localhost MVP where the
// maintenance-inactive concern carries little risk (no untrusted
// multi-tenant surface). NO concrete integration blocker was hit —
// diff2html's stable `html()` formatter is the only API used and the
// unified patch is generated in-house (no extra `diff` dep). Switching
// would add a dependency and a spec deviation for zero MVP benefit.
// Revisit only if diff2html breaks on a future dependency bump.
//
// SECURITY: renderDiff feeds a generated unified patch to diff2html,
// which HTML-escapes content; output is sanitised → the `v-html` is
// safe.

import { ref, watch } from 'vue'
import { renderDiff, type DiffStyle } from '@/lib/render'

const props = withDefaults(
  defineProps<{
    /** The "before" file content. */
    oldText: string
    /** The "after" file content. */
    newText: string
    /** Filename shown in the diff header. */
    filename: string
    /** 'side-by-side' (default) or 'line-by-line' (unified). */
    style?: DiffStyle
  }>(),
  { style: 'side-by-side' },
)

/** Rendered, sanitised diff HTML. */
const html = ref('')
/** A render error message, or null. */
const error = ref<string | null>(null)

watch(
  [
    () => props.oldText,
    () => props.newText,
    () => props.filename,
    () => props.style,
  ],
  async ([oldText, newText, filename, style]) => {
    error.value = null
    try {
      html.value = (
        await renderDiff(oldText, newText, filename, style)
      ).html
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'diff render failed'
      html.value = ''
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="diff-render">
    <p
      v-if="error"
      class="diff-render__error"
      role="alert"
    >
      Could not render diff: {{ error }}
    </p>
    <!-- output is sanitised by renderDiff (diff2html escapes content)
         — safe to inject. -->
    <!-- eslint-disable vue/no-v-html -->
    <div
      v-else
      v-html="html"
    />
    <!-- eslint-enable vue/no-v-html -->
  </div>
</template>

<style scoped>
.diff-render {
  font-size: 0.85em;
}

.diff-render__error {
  color: var(--color-danger);
}

.diff-render :deep(.d2h-wrapper) {
  overflow-x: auto;
}
</style>
