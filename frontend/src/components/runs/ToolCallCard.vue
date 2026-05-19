<script setup lang="ts">
// A tool-call timeline row: the agent invoking a tool and (when paired)
// its result. Backed by relay `tool_use_start` / `tool_use_end` events
// (spec §3.2 payloads: start `{tool_id,name,args}`, end
// `{tool_id,result,is_error,duration_ms}`).
//
// Args and result are pretty-printed JSON, collapsed to <8 lines by
// default with a "show full" toggle (plan.md timeline note). Rendering
// is intentionally minimal text/<pre> — the real markdown/shiki pipeline
// is W6 (`lib/render.ts` is a stub by mandate); a plain <pre> is the
// correct minimal contract here.

import { computed, ref } from 'vue'

const props = defineProps<{
  /** Tool name (from the `tool_use_start` payload). */
  name: string
  /** Invocation args (from `tool_use_start`). */
  args: unknown
  /** Result (from the paired `tool_use_end`), or undefined if pending. */
  result?: unknown
  /** Whether the tool returned an error (`tool_use_end.is_error`). */
  isError?: boolean
  /** Tool wall-clock in ms (`tool_use_end.duration_ms`). */
  durationMs?: number
}>()

const COLLAPSE_LINES = 8

function pretty(v: unknown): string {
  if (v === undefined) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

const argsText = computed(() => pretty(props.args))
const resultText = computed(() => pretty(props.result))

const expanded = ref(false)

function lineCount(s: string): number {
  return s === '' ? 0 : s.split('\n').length
}

const overflows = computed(
  () =>
    lineCount(argsText.value) > COLLAPSE_LINES ||
    lineCount(resultText.value) > COLLAPSE_LINES,
)

function clamp(s: string): string {
  if (expanded.value) return s
  const lines = s.split('\n')
  if (lines.length <= COLLAPSE_LINES) return s
  return lines.slice(0, COLLAPSE_LINES).join('\n')
}
</script>

<template>
  <div
    class="tool-card"
    :class="{ 'tool-card--error': isError }"
    data-testid="tool-call-card"
  >
    <div class="tool-card__head">
      <span class="tool-card__name">{{ name }}</span>
      <span
        v-if="isError"
        class="tool-card__badge"
      >error</span>
      <span
        v-if="durationMs != null"
        class="tool-card__meta"
      >{{ durationMs }}ms</span>
    </div>

    <div class="tool-card__section">
      <span class="tool-card__label">args</span>
      <pre class="tool-card__block">{{ clamp(argsText) }}</pre>
    </div>

    <div
      v-if="result !== undefined"
      class="tool-card__section"
    >
      <span class="tool-card__label">result</span>
      <pre class="tool-card__block">{{ clamp(resultText) }}</pre>
    </div>

    <button
      v-if="overflows"
      type="button"
      class="tool-card__toggle"
      data-testid="tool-card-toggle"
      @click="expanded = !expanded"
    >
      {{ expanded ? 'Show less' : 'Show full' }}
    </button>
  </div>
</template>

<style scoped>
.tool-card {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.6rem 0.75rem;
  background: var(--color-surface);
}

.tool-card--error {
  border-color: #ff6b6b;
}

.tool-card__head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.4rem;
}

.tool-card__name {
  font-weight: 600;
  font-family: var(--font-mono);
}

.tool-card__badge {
  font-size: 0.72em;
  color: #ff6b6b;
  border: 1px solid currentcolor;
  border-radius: 999px;
  padding: 0 0.5em;
}

.tool-card__meta {
  margin-left: auto;
  font-size: 0.78em;
  color: var(--color-text-dim);
}

.tool-card__section {
  margin-top: 0.35rem;
}

.tool-card__label {
  display: block;
  font-size: 0.72em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
  margin-bottom: 0.2rem;
}

.tool-card__block {
  margin: 0;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  font-family: var(--font-mono);
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}

.tool-card__toggle {
  margin-top: 0.45rem;
  background: none;
  border: none;
  color: var(--color-accent);
  font: inherit;
  font-size: 0.82em;
  cursor: pointer;
  padding: 0;
}
</style>
