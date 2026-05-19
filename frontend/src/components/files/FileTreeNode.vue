<script setup lang="ts">
// One node in the shared browser tree: a directory (lazily fetches +
// reveals its children when expanded) or a selectable file. Recursive.
// The listing query is enabled only while the directory is expanded, so
// children are fetched on first expand (spec §9.1 lazy-expand). Backend
// order (dirs-first then name-asc) is preserved — we never re-sort.
//
// W7: source-agnostic. The `source` supplies the listing query and the
// per-source ephemeral UI store (so a project tree and an artifacts
// tree keep independent expand/select state).

import { computed } from 'vue'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import { asAsyncState, type FileEntry, type BrowserSource } from '@/lib/queries'
import { useBrowserUiStore } from '@/stores/files'

const props = defineProps<{
  /** The data source this tree browses (project files or artifacts). */
  source: BrowserSource
  /** This node's entry (name + is_dir + size + modified). */
  entry: FileEntry
  /** Parent directory path ('' = sandbox root). */
  parentPath: string
  /** Indentation depth (root entries are depth 0). */
  depth: number
}>()

const emit = defineEmits<{
  /** A file was selected; payload is its sandbox-relative path. */
  (e: 'select', path: string): void
}>()

const store = useBrowserUiStore(props.source.storeId)

/** This node's full sandbox-relative path. */
const path = computed(() =>
  props.parentPath === '' ? props.entry.name : `${props.parentPath}/${props.entry.name}`,
)

const expanded = computed(() => store.isExpanded(path.value))
const selected = computed(() => store.selectedPath === path.value)

// Children are fetched only while this directory is expanded.
const listing = props.source.useListing(
  () => path.value,
  () => props.entry.is_dir && expanded.value,
)
const { isLoading, error } = asAsyncState(listing)

function onActivate(): void {
  if (props.entry.is_dir) {
    store.toggleDir(path.value)
  } else {
    store.selectFile(path.value)
    emit('select', path.value)
  }
}

function onChildSelect(childPath: string): void {
  emit('select', childPath)
}
</script>

<template>
  <li class="tree-node">
    <button
      type="button"
      class="tree-node__row"
      :class="{ 'tree-node__row--selected': selected }"
      :style="{ paddingLeft: `${depth * 0.9 + 0.3}rem` }"
      :aria-expanded="entry.is_dir ? expanded : undefined"
      @click="onActivate"
    >
      <span
        class="tree-node__icon"
        aria-hidden="true"
      >{{ entry.is_dir ? (expanded ? '▾' : '▸') : '·' }}</span>
      <span class="tree-node__name">{{ entry.name }}</span>
    </button>

    <div
      v-if="entry.is_dir && expanded"
      class="tree-node__children"
    >
      <AsyncBoundary
        :loading="isLoading"
        :error="error"
      >
        <ul class="tree-node__list">
          <FileTreeNode
            v-for="child in listing.data.value?.entries ?? []"
            :key="child.name"
            :source="source"
            :entry="child"
            :parent-path="path"
            :depth="depth + 1"
            @select="onChildSelect"
          />
        </ul>
      </AsyncBoundary>
    </div>
  </li>
</template>

<style scoped>
.tree-node {
  list-style: none;
}

.tree-node__list {
  margin: 0;
  padding: 0;
}

.tree-node__row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.18rem 0.4rem;
  background: none;
  border: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
}

.tree-node__row:hover {
  background: var(--color-surface);
}

.tree-node__row--selected {
  background: var(--color-surface);
  font-weight: 600;
}

.tree-node__icon {
  width: 1em;
  color: var(--color-text-dim);
}

.tree-node__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
