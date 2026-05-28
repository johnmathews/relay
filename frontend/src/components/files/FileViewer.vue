<script setup lang="ts">
// Right-side content area for the file browser (spec §9.1/§9.4).
// Fetches the selected file's content and dispatches by type:
//   .md/.markdown                 → MarkdownRender (mermaid handled within)
//   recognised code extensions    → CodeRender (shiki)
//   plain / unknown text          → escaped monospace <pre>
//   binary (backend 415)          → "binary content (N bytes) — download"
//   oversized (413) / 400 / 404   → friendly message
// Size + last-modified metadata are surfaced in the header.

import { computed } from 'vue'
import MarkdownRender from './MarkdownRender.vue'
import CodeRender from './CodeRender.vue'
import { asAsyncState, ApiError, type BrowserSource } from '@/lib/queries'

const props = defineProps<{
  /** The data source the file lives in (project files or artifacts). */
  source: BrowserSource
  /** Selected file path (sandbox-relative) or `null` for no selection. */
  path: string | null
}>()

const content = props.source.useContent(() => props.path)
const { isLoading } = asAsyncState(content)

/** The basename, for the header + extension dispatch. */
const basename = computed(() => (props.path ?? '').split('/').pop() ?? '')
/** Lower-cased file extension without the dot ('' if none). */
const ext = computed(() => {
  const name = basename.value
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(i + 1).toLowerCase() : ''
})

const MARKDOWN_EXTS = new Set(['md', 'markdown'])
// Extensions → shiki language token. Only langs render.ts can highlight
// are mapped; anything else falls through to the plain monospace path
// (renderCode itself also degrades unknowns safely).
const CODE_EXTS: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'typescript',
  js: 'typescript',
  mjs: 'typescript',
  vue: 'vue',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
}

type Kind = 'idle' | 'markdown' | 'code' | 'plain'

const kind = computed<Kind>(() => {
  if (props.path == null) return 'idle'
  if (MARKDOWN_EXTS.has(ext.value)) return 'markdown'
  if (ext.value in CODE_EXTS) return 'code'
  return 'plain'
})

const codeLang = computed(() => CODE_EXTS[ext.value] ?? '')

/** The error as an ApiError (so we can branch on HTTP status). */
const apiError = computed<ApiError | null>(() =>
  content.error.value instanceof ApiError ? content.error.value : null,
)

/** Friendly, status-specific message for a failed fetch. */
const errorMessage = computed<string | null>(() => {
  const e = apiError.value
  if (e == null) {
    return content.error.value != null ? 'Could not load this file.' : null
  }
  switch (e.status) {
    case 413:
      return 'This file is too large to display (over 5 MiB).'
    case 404:
      return 'This file no longer exists.'
    case 400:
      return 'This path is outside the project sandbox.'
    default:
      return e.message || 'Could not load this file.'
  }
})

/** A 415 means the file is binary — offer a raw-bytes download instead. */
const isBinary = computed(() => apiError.value?.status === 415)

/** Best-effort byte size for the binary message (from the error body). */
const binarySize = computed<number | null>(() => {
  const body = apiError.value?.body
  if (
    body != null &&
    typeof body === 'object' &&
    'size' in body &&
    typeof (body as { size: unknown }).size === 'number'
  ) {
    return (body as { size: number }).size
  }
  return null
})

const rawHref = computed(() =>
  props.path == null ? '#' : props.source.rawUrl(props.path),
)

/** "binary content (N bytes)" — N omitted if the size is unknown. */
const binaryLabel = computed(() =>
  binarySize.value != null
    ? `binary content (${binarySize.value} bytes)`
    : 'binary content',
)

/** Human-readable byte size for the metadata header. */
function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${(n / (1024 * 1024)).toFixed(1)} MiB`
}
</script>

<template>
  <section class="file-viewer">
    <p
      v-if="kind === 'idle'"
      class="file-viewer__idle"
    >
      Select a file to view it.
    </p>

    <template v-else>
      <header class="file-viewer__head">
        <span class="file-viewer__name">{{ basename }}</span>
        <span
          v-if="content.data.value"
          class="file-viewer__meta"
        >
          {{ fmtBytes(content.data.value.size) }} ·
          {{ new Date(content.data.value.modified * 1000).toLocaleString() }}
        </span>
      </header>

      <div
        v-if="isLoading"
        class="file-viewer__status"
        role="status"
      >
        Loading…
      </div>

      <div
        v-else-if="isBinary"
        class="file-viewer__binary"
      >
        <span>{{ binaryLabel }} — </span>
        <a
          :href="rawHref"
          download
        >
          download
        </a>
      </div>

      <p
        v-else-if="errorMessage"
        class="file-viewer__error"
        role="alert"
      >
        {{ errorMessage }}
      </p>

      <MarkdownRender
        v-else-if="kind === 'markdown' && content.data.value"
        :source="content.data.value.content"
      />

      <CodeRender
        v-else-if="kind === 'code' && content.data.value"
        :source="content.data.value.content"
        :lang="codeLang"
      />

      <pre
        v-else-if="content.data.value"
        class="file-viewer__plain"
      >{{ content.data.value.content }}</pre>
    </template>
  </section>
</template>

<style scoped>
.file-viewer {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  min-width: 0;
}

.file-viewer__idle,
.file-viewer__status {
  color: var(--color-text-dim);
  padding: 1rem;
}

.file-viewer__head {
  display: flex;
  align-items: baseline;
  gap: 0.8rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.4rem;
}

.file-viewer__name {
  font-family: var(--font-mono);
  font-weight: 600;
}

.file-viewer__meta {
  font-size: 0.78em;
  color: var(--color-text-dim);
}

.file-viewer__error {
  color: var(--color-danger);
}

.file-viewer__binary {
  padding: 1rem;
  color: var(--color-text-dim);
}

.file-viewer__plain {
  margin: 0;
  padding: 0.85rem 1rem;
  overflow-x: auto;
  background: var(--color-surface);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 0.85em;
  line-height: 1.5;
}
</style>
