<script setup lang="ts">
// Left-side tree for the shared read-only browser (spec §9.1). Fetches
// the sandbox-root listing, then each directory expands lazily
// (FileTreeNode owns the per-directory fetch). Backend order (dirs-first
// then name-asc) is preserved. Selecting a file emits its
// sandbox-relative path so the host drives the FileViewer. Loading/error
// use the shared AsyncBoundary.
//
// W7: this is now source-agnostic — it renders whatever `BrowserSource`
// it is handed (W6 project files OR W7 run artifacts). The component
// holds NO endpoint knowledge; the `source` supplies the listing query
// + the per-source UI-state store id. ONE tree serves both (ADR-25's
// single-sourced backend, mirrored on the frontend).

import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import FileTreeNode from './FileTreeNode.vue'
import { asAsyncState, type BrowserSource } from '@/lib/queries'

const props = defineProps<{
  /** The data source to browse (project files or run artifacts). */
  source: BrowserSource
  /** Accessible label for the tree's <nav> (defaults to "Files"). */
  ariaLabel?: string
}>()

const emit = defineEmits<{
  /** A file was selected; payload is its sandbox-relative path. */
  (e: 'select', path: string): void
}>()

// The root listing is always fetched (no expand gate at the root).
const rootListing = props.source.useListing(() => '')
const { isLoading, error } = asAsyncState(rootListing)

function onSelect(path: string): void {
  emit('select', path)
}
</script>

<template>
  <nav
    class="file-tree"
    :aria-label="ariaLabel ?? 'Files'"
  >
    <AsyncBoundary
      :loading="isLoading"
      :error="error"
    >
      <p
        v-if="(rootListing.data.value?.entries.length ?? 0) === 0"
        class="file-tree__empty"
      >
        No files.
      </p>
      <ul
        v-else
        class="file-tree__list"
      >
        <FileTreeNode
          v-for="entry in rootListing.data.value?.entries ?? []"
          :key="entry.name"
          :source="source"
          :entry="entry"
          parent-path=""
          :depth="0"
          @select="onSelect"
        />
      </ul>
    </AsyncBoundary>
  </nav>
</template>

<style scoped>
.file-tree {
  font-size: 0.85rem;
  overflow-y: auto;
}

.file-tree__list {
  margin: 0;
  padding: 0;
}

.file-tree__empty {
  color: var(--color-text-dim);
  padding: 0.5rem;
}
</style>
