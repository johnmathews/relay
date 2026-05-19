<script setup lang="ts">
// Thin wrapper over `lib/render.ts` renderCode (spec §9.4). Highlights
// a code file with shiki (lazy core + JS engine + per-lang grammar). An
// unknown language degrades to escaped monospace (renderCode never
// throws). Reused by W5/W7.
//
// SECURITY: shiki tokenises and HTML-escapes the source; the fallback
// path escapes manually. The output is sanitised → the single `v-html`
// here is safe.

import { ref, watch } from 'vue'
import { renderCode } from '@/lib/render'

const props = defineProps<{
  /** Raw source code. */
  source: string
  /** Language token (e.g. 'python', 'ts'); unknown → plain monospace. */
  lang: string
}>()

/** Rendered, sanitised HTML. */
const html = ref('')

watch(
  [() => props.source, () => props.lang],
  async ([src, lang]) => {
    // renderCode swallows its own errors (returns escaped monospace),
    // so no try/catch is needed for correctness — but guard anyway so a
    // truly unexpected throw cannot blank the pane.
    try {
      html.value = (await renderCode(src, lang)).html
    } catch {
      html.value = ''
    }
  },
  { immediate: true },
)
</script>

<template>
  <!-- output is sanitised by renderCode (shiki tokenises + escapes;
       unknown-lang fallback escapes) — safe to inject. -->
  <!-- eslint-disable vue/no-v-html -->
  <div
    class="code-render"
    v-html="html"
  />
  <!-- eslint-enable vue/no-v-html -->
</template>

<style scoped>
.code-render :deep(pre) {
  margin: 0;
  padding: 0.85rem 1rem;
  overflow-x: auto;
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 0.85em;
  line-height: 1.5;
}
</style>
