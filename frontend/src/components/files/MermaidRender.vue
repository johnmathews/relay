<script setup lang="ts">
// Thin wrapper over `lib/render.ts` renderMermaid (spec §9.4). Renders
// a standalone mermaid diagram source to inline SVG via a dynamic
// import (MANDATE 3 — never a static mermaid import). Markdown's
// embedded ```mermaid fences are handled inside renderMarkdown; this
// component is for a diagram shown on its own (W7 may use it directly).
//
// SECURITY: renderMermaid initialises mermaid with securityLevel
// 'strict' and on error emits an escaped monospace block, so the output
// is sanitised and safe to inject.

import { ref, watch } from 'vue'
import { renderMermaid } from '@/lib/render'

const props = defineProps<{
  /** Mermaid diagram source (e.g. a flowchart definition). */
  source: string
}>()

/** Rendered, sanitised HTML (SVG, or an escaped error block). */
const html = ref('')

watch(
  () => props.source,
  async (src) => {
    try {
      html.value = (await renderMermaid(src)).html
    } catch {
      html.value = ''
    }
  },
  { immediate: true },
)
</script>

<template>
  <!-- output is sanitised by renderMermaid (strict mermaid; error path
       escapes the raw source) — safe to inject. -->
  <!-- eslint-disable vue/no-v-html -->
  <div
    class="mermaid-render"
    v-html="html"
  />
  <!-- eslint-enable vue/no-v-html -->
</template>

<style scoped>
.mermaid-render {
  display: flex;
  justify-content: center;
}

.mermaid-render :deep(.render-mermaid-error__note) {
  color: #ff6b6b;
  font-size: 0.85em;
  margin: 0 0 0.4rem;
}
</style>
