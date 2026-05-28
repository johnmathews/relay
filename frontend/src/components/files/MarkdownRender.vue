<script setup lang="ts">
// Thin wrapper over `lib/render.ts` renderMarkdown (spec §9.4). Renders
// a markdown document to sanitised HTML: tables, task lists, footnotes;
// ```mermaid fences → inline SVG; other ```lang fences → shiki. Reused
// by W5 (Files pane) and W7 (Artifacts pane) — keep the prop API clean.
//
// SECURITY: renderMarkdown uses markdown-it `html:false` (raw HTML
// escaped) and shiki/escape for code, so its output is sanitised and
// safe to inject. This is the ONLY sanctioned `v-html` site here; the
// source is untrusted agent output but the renderer escapes it.

import { ref, watch } from 'vue'
import { renderMarkdown } from '@/lib/render'

const props = defineProps<{
  /** Raw markdown source (e.g. a `.md` artifact's content). */
  source: string
}>()

/** Rendered, sanitised HTML (empty until the async render resolves). */
const html = ref('')
/** A render error message, or null. */
const error = ref<string | null>(null)

watch(
  () => props.source,
  async (src) => {
    error.value = null
    try {
      html.value = (await renderMarkdown(src)).html
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'render failed'
      html.value = ''
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="markdown-render">
    <p
      v-if="error"
      class="markdown-render__error"
      role="alert"
    >
      Could not render markdown: {{ error }}
    </p>
    <!-- output is sanitised by renderMarkdown (markdown-it html:false
         + shiki escaping) — safe to inject. -->
    <!-- eslint-disable vue/no-v-html -->
    <div
      v-else
      class="markdown-render__body"
      v-html="html"
    />
    <!-- eslint-enable vue/no-v-html -->
  </div>
</template>

<style scoped>
.markdown-render__body {
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.markdown-render__error {
  color: var(--color-danger);
}

.markdown-render__body :deep(pre) {
  overflow-x: auto;
  padding: 0.75rem;
  border-radius: 6px;
  background: var(--color-surface);
}

.markdown-render__body :deep(table) {
  border-collapse: collapse;
}

.markdown-render__body :deep(th),
.markdown-render__body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 0.3rem 0.6rem;
}

.markdown-render__body :deep(.render-mermaid) {
  display: flex;
  justify-content: center;
}
</style>
