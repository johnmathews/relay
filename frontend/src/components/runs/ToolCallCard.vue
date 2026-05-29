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

import { computed, inject, ref } from 'vue'
import type { ToolCallDrawerPayload } from './ToolCallDetailDrawer.vue'

/**
 * Provided by `RunRightPane.vue`. When present, the "View full" button
 * is rendered and opens the right-side detail drawer with this card's
 * payload. When absent (older call-sites / unit tests that mount the
 * card directly), the affordance is hidden — the inline "Show full"
 * 5-line toggle stays the only expand mechanism.
 */
const openToolDetail = inject<((p: ToolCallDrawerPayload) => void) | null>(
  'openToolDetail',
  null,
)

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
  /**
   * When true, render without the outer card border / background / head row.
   * The timeline step-card already supplies the container chrome + name +
   * duration + ok/err glyph in its header; rendering a second outer card
   * inside it produced visible card-in-card nesting.
   */
  embedded?: boolean
}>()

// Per the live-stream UX work (2026-05-25): tool args / result are
// the worst offenders for vertical noise in a long timeline. 5 lines
// is enough to scan the first paragraph of a bash command or the
// first few keys of a JSON args blob, but not enough to dominate the
// viewport. Toggle reveals the rest in place.
const COLLAPSE_LINES = 5

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

function onViewFull(): void {
  if (openToolDetail == null) return
  openToolDetail({
    name: props.name,
    args: props.args,
    result: props.result,
    isError: props.isError === true,
    durationMs: props.durationMs ?? null,
  })
}
</script>

<template>
  <div
    class="tool-card"
    :class="{ 'tool-card--error': isError, 'tool-card--embedded': embedded }"
    data-testid="tool-call-card"
  >
    <div
      v-if="!embedded"
      class="tool-card__head"
    >
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

    <div
      v-if="overflows || openToolDetail != null"
      class="tool-card__actions"
    >
      <button
        v-if="overflows"
        type="button"
        class="tool-card__toggle"
        data-testid="tool-card-toggle"
        @click="expanded = !expanded"
      >
        {{ expanded ? 'Show less' : 'Show full' }}
      </button>
      <button
        v-if="openToolDetail != null"
        type="button"
        class="tool-card__view-full"
        data-testid="tool-card-view-full"
        @click="onViewFull"
      >
        View full
      </button>
    </div>
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
  border-color: var(--color-danger);
}

/* Embedded inside a timeline step-card's body: drop the outer chrome
   (step-card supplies it) and let the inner blocks sit flush. */
.tool-card--embedded {
  border: none;
  border-radius: 0;
  padding: 0;
  background: transparent;
}
.tool-card--embedded.tool-card--error {
  border: none;
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
  color: var(--color-danger);
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

/* Embedded blocks lose their own border + bg (the step-card's body
   already supplies a contrasting surface). They keep the monospace
   font, scroll behavior, and the small inset so a code block reads
   as code rather than running into the body padding. */
.tool-card--embedded .tool-card__block {
  border: none;
  background: transparent;
  padding: 0;
}
.tool-card--embedded .tool-card__section {
  margin-top: 0;
}
.tool-card--embedded .tool-card__section + .tool-card__section {
  margin-top: 0.55rem;
}

.tool-card__actions {
  margin-top: 0.45rem;
  display: flex;
  gap: 0.9rem;
  align-items: center;
}

.tool-card__toggle,
.tool-card__view-full {
  background: none;
  border: none;
  color: var(--color-accent);
  font: inherit;
  font-size: 0.82em;
  cursor: pointer;
  padding: 0;
}

.tool-card__view-full {
  color: var(--color-text-dim);
}

.tool-card__view-full:hover,
.tool-card__view-full:focus-visible {
  color: var(--color-accent);
}
</style>
