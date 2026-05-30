<script setup lang="ts">
// Field-aware payload renderer for signal / boundary / generic
// timeline rows. The old `<pre>{{ JSON.stringify(payload, null, 2) }}`
// path produced an unreadable wall of escaped `\n` inside fields
// like `prompt`, `preamble`, `text` — operators couldn't scan the
// most important context (the iter prompt) without manually
// un-escaping in their head.
//
// Layout:
//   • each top-level field is a labeled section
//   • multi-line strings (containing actual `\n`) render in a
//     readable `<pre>` with real newlines + collapse-after-N-lines
//   • short scalar values render inline next to the label
//   • nested objects / arrays render as indented JSON
//
// A `[ View raw ]` toggle swaps the whole view for a shiki-
// highlighted JSON block via `lib/render.renderCode`. The raw view
// is the previous behaviour, kept as an opt-in for cases where the
// structural shape matters (e.g. when copying for a bug report).

import { computed, ref, watch } from 'vue'
import { renderCode } from '@/lib/render'

const props = defineProps<{
  /** The payload object to render. Usually an `event.payload` or a
   *  signal_emit's `args`. `null` / `undefined` render nothing. */
  payload: unknown
  /**
   * Lines to show before a multi-line string collapses. The "Show all
   * N lines" toggle reveals the rest. Default 12.
   */
  collapseLinesAt?: number
}>()

const COLLAPSE_AT = computed(() => props.collapseLinesAt ?? 12)

interface Field {
  key: string
  value: unknown
  /** Pre-classified value flavour for the template. */
  flavour: 'multiline' | 'short' | 'long' | 'scalar' | 'nested'
}

/**
 * Top-level field list. Order is the object's own key order
 * (declaration order in the JSON wire shape). Non-object payloads
 * are wrapped in a single-field shape `[{ key: '', value: raw }]`
 * so the renderer still has something to walk.
 */
const fields = computed<Field[]>(() => {
  const p = props.payload
  if (p == null) return []
  if (typeof p !== 'object' || Array.isArray(p)) {
    return [{ key: '', value: p, flavour: classify(p) }]
  }
  const out: Field[] = []
  for (const [k, v] of Object.entries(p as Record<string, unknown>)) {
    out.push({ key: k, value: v, flavour: classify(v) })
  }
  return out
})

function classify(v: unknown): Field['flavour'] {
  if (v == null) return 'scalar'
  if (typeof v === 'string') {
    if (v.includes('\n')) return 'multiline'
    return v.length > 120 ? 'long' : 'short'
  }
  if (typeof v === 'number' || typeof v === 'boolean') return 'scalar'
  return 'nested'
}

function asString(v: unknown): string {
  if (v == null) return String(v)
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return '[unserializable]'
  }
}

// Per-field "show all lines" override for collapsed multi-line
// strings. Keyed by field index since duplicate keys don't exist on
// a JSON object (top-level walk).
const expandedFields = ref<Set<number>>(new Set())

function lineCount(s: string): number {
  if (s === '') return 0
  return s.split('\n').length
}

function previewLines(s: string, n: number): string {
  const lines = s.split('\n')
  if (lines.length <= n) return s
  return lines.slice(0, n).join('\n')
}

function isExpanded(idx: number): boolean {
  return expandedFields.value.has(idx)
}

function toggleExpanded(idx: number): void {
  const next = new Set(expandedFields.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  expandedFields.value = next
}

// ── Raw JSON toggle ──────────────────────────────────────────────────
const showRaw = ref(false)

const rawJson = computed(() => {
  try {
    return JSON.stringify(props.payload, null, 2)
  } catch {
    return ''
  }
})

const rawHtml = ref('')
const rawError = ref<string | null>(null)

watch(
  [showRaw, rawJson],
  async ([on, src]) => {
    if (!on || src === '') {
      rawHtml.value = ''
      return
    }
    rawError.value = null
    try {
      rawHtml.value = (await renderCode(src, 'json')).html
    } catch (e) {
      rawError.value = e instanceof Error ? e.message : 'render failed'
      rawHtml.value = ''
    }
  },
  { immediate: true },
)

const hasContent = computed(() => fields.value.length > 0)
</script>

<template>
  <div
    class="payload-view"
    data-testid="event-payload-view"
  >
    <div class="payload-view__toolbar">
      <button
        type="button"
        class="payload-view__toggle"
        :class="{ 'is-on': showRaw }"
        data-testid="payload-view-toggle-raw"
        :aria-pressed="showRaw"
        @click="showRaw = !showRaw"
      >
        {{ showRaw ? 'View formatted' : 'View raw JSON' }}
      </button>
    </div>

    <!-- Raw mode: one shiki-highlighted JSON block. Falls back to
         plain monospace on render failure. -->
    <div
      v-if="showRaw"
      class="payload-view__raw"
    >
      <p
        v-if="rawError"
        class="payload-view__error"
        role="alert"
      >
        Could not highlight JSON: {{ rawError }}
      </p>
      <!-- renderCode HTML is sanitised by shiki (tokeniser escapes) — safe to inject. -->
      <!-- eslint-disable vue/no-v-html -->
      <div
        v-else-if="rawHtml !== ''"
        class="payload-view__raw-body"
        v-html="rawHtml"
      />
      <!-- eslint-enable vue/no-v-html -->
      <pre
        v-else
        class="payload-view__raw-fallback"
      >{{ rawJson }}</pre>
    </div>

    <!-- Field-aware mode (default). Each top-level key gets its own
         labeled row; multi-line strings render with real newlines. -->
    <dl
      v-else-if="hasContent"
      class="payload-view__fields"
    >
      <template
        v-for="(f, idx) in fields"
        :key="f.key === '' ? `_v${idx}` : f.key"
      >
        <dt
          v-if="f.key !== ''"
          class="payload-view__key"
        >
          {{ f.key }}
        </dt>
        <dd
          class="payload-view__value"
          :data-flavour="f.flavour"
        >
          <span
            v-if="f.flavour === 'scalar' || f.flavour === 'short'"
            class="payload-view__scalar"
          >{{ asString(f.value) }}</span>

          <template v-else-if="f.flavour === 'multiline'">
            <pre
              class="payload-view__pre"
              data-testid="payload-multiline"
            >{{
              isExpanded(idx) || lineCount(asString(f.value)) <= COLLAPSE_AT
                ? asString(f.value)
                : previewLines(asString(f.value), COLLAPSE_AT)
            }}</pre>
            <button
              v-if="lineCount(asString(f.value)) > COLLAPSE_AT"
              type="button"
              class="payload-view__more"
              :data-testid="`payload-more-${idx}`"
              @click="toggleExpanded(idx)"
            >
              {{
                isExpanded(idx)
                  ? 'Show less'
                  : `Show all ${lineCount(asString(f.value))} lines`
              }}
            </button>
          </template>

          <pre
            v-else-if="f.flavour === 'long'"
            class="payload-view__pre payload-view__pre--wrap"
          >{{ asString(f.value) }}</pre>

          <pre
            v-else
            class="payload-view__pre payload-view__pre--nested"
          >{{ asString(f.value) }}</pre>
        </dd>
      </template>
    </dl>
  </div>
</template>

<style scoped>
.payload-view {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.payload-view__toolbar {
  display: flex;
  justify-content: flex-end;
}

.payload-view__toggle {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.72em;
  padding: 0.18em 0.6em;
  transition: background-color 0.12s ease, color 0.12s ease,
    border-color 0.12s ease;
}

.payload-view__toggle:hover,
.payload-view__toggle:focus-visible {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
  outline: none;
}

.payload-view__toggle.is-on {
  color: var(--color-text);
}

.payload-view__fields {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 0.75rem;
  margin: 0;
}

.payload-view__key {
  font-family: var(--font-mono);
  font-size: 0.78em;
  color: var(--color-text-dim);
  text-transform: lowercase;
  letter-spacing: 0.02em;
  padding-top: 0.2rem;
  white-space: nowrap;
}

.payload-view__value {
  margin: 0;
  min-width: 0; /* allow children to shrink + wrap inside the grid cell */
}

/* Top-level non-object payloads (`f.key === ''`) — span both columns. */
.payload-view__fields dd:only-child,
.payload-view__fields dd:first-child:last-child {
  grid-column: 1 / -1;
}

.payload-view__scalar {
  font-family: var(--font-mono);
  font-size: 0.84em;
  color: var(--color-text);
  word-break: break-word;
}

.payload-view__pre {
  margin: 0;
  padding: 0.55rem 0.7rem;
  border-radius: 4px;
  background: var(--color-bg);
  font-family: var(--font-mono);
  font-size: 0.82em;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}

.payload-view__pre--wrap {
  white-space: pre-wrap;
}

.payload-view__pre--nested {
  color: var(--color-text-dim);
}

.payload-view__more {
  margin-top: 0.3rem;
  background: none;
  border: none;
  color: var(--color-text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.78em;
  padding: 0;
  text-decoration: underline;
}

.payload-view__more:hover,
.payload-view__more:focus-visible {
  color: var(--color-text);
  outline: none;
}

.payload-view__raw {
  margin: 0;
}

.payload-view__raw-body :deep(pre) {
  margin: 0;
  padding: 0.55rem 0.7rem;
  border-radius: 4px;
  background: var(--color-bg);
  font-size: 0.82em;
  line-height: 1.45;
  overflow-x: auto;
}

.payload-view__raw-fallback {
  margin: 0;
  padding: 0.55rem 0.7rem;
  border-radius: 4px;
  background: var(--color-bg);
  font-family: var(--font-mono);
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-word;
}

.payload-view__error {
  color: var(--color-danger);
  font-size: 0.85em;
  margin: 0 0 0.4rem;
}
</style>
