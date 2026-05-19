// Ephemeral UI state for the shared read-only file/artifacts browser
// (W6 project files; W7 run artifacts).
//
// Spec §9.2: plain Pinia holds ONLY ephemeral UI state; server data
// (directory listings, file content) lives in the Colada cache via the
// query hooks — NEVER duplicated here. This store tracks only what the
// user has interacted with: which directories are expanded in the tree,
// which file is selected, and an optional two-path diff selection.
//
// W7 generalised this from a single `files` store into a per-SOURCE
// keyed store *factory* (`useBrowserUiStore(storeId)`): the project
// browser and an artifacts pane each get their OWN isolated instance
// (keyed by the `BrowserSource.storeId`, e.g. `project:1` / `run:abc`)
// so expanding/selecting in one never bleeds into the other. The store
// SHAPE is unchanged from W6 — only the id is now parameterised.
// `useFilesStore()` remains as the W6 back-compat default (the
// `files:default` instance) so nothing that imported it has to change.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

/** A selected pair of file paths to diff (old vs. new). */
export interface DiffSelection {
  /** The "before" file path (relative to the sandbox root). */
  oldPath: string
  /** The "after" file path (relative to the sandbox root). */
  newPath: string
}

/**
 * The browser UI store's public surface (one instance per source). This
 * is exactly the W6 store shape; it is the type FileTreeNode/FileViewer
 * and the host views consume.
 */
export interface BrowserUiStore {
  isExpanded(path: string): boolean
  toggleDir(path: string): void
  expandDir(path: string): void
  selectFile(path: string | null): void
  setDiffSelection(selection: DiffSelection | null): void
  reset(): void
  readonly selectedPath: string | null
  readonly isComparing: boolean
  readonly diffSelection: DiffSelection | null
}

/**
 * Resolve (and lazily create) the ephemeral UI store for one browser
 * source. `storeId` is the `BrowserSource.storeId` (e.g. `project:1`,
 * `run:abc`); each distinct id is its own Pinia store instance, so the
 * project browser and the artifacts pane keep independent expand/select
 * state. The store body is identical to the original W6 `files` store.
 */
export function useBrowserUiStore(storeId: string): BrowserUiStore {
  const useStore = defineStore(`browser-ui:${storeId}`, () => {
    /**
     * Directory paths the user has expanded (relative to the sandbox
     * root; '' is the always-expanded root). The tree fetches a
     * directory's children lazily the first time it appears here.
     */
    const expandedDirs = ref<Set<string>>(new Set<string>())

    /** The currently-selected file path, or `null` if none is open. */
    const selectedPath = ref<string | null>(null)

    /**
     * The diff-compare selection (two paths) or `null` when not in
     * compare mode. W6's DiffRender consumes the contents fetched via
     * the Colada queries.
     */
    const diffSelection = ref<DiffSelection | null>(null)

    /** True while a two-file diff comparison is active. */
    const isComparing = computed(() => diffSelection.value !== null)

    /** Whether a directory path is currently expanded. */
    function isExpanded(path: string): boolean {
      return expandedDirs.value.has(path)
    }

    /** Expand or collapse a directory (toggles its presence in the set). */
    function toggleDir(path: string): void {
      const next = new Set(expandedDirs.value)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      expandedDirs.value = next
    }

    /** Idempotently mark a directory expanded. */
    function expandDir(path: string): void {
      if (expandedDirs.value.has(path)) return
      const next = new Set(expandedDirs.value)
      next.add(path)
      expandedDirs.value = next
    }

    /** Select a file for the viewer (`null` clears the selection). */
    function selectFile(path: string | null): void {
      selectedPath.value = path
    }

    /** Enter diff-compare mode for two file paths. */
    function setDiffSelection(selection: DiffSelection | null): void {
      diffSelection.value = selection
    }

    /** Reset all browser UI state (e.g. on source change). */
    function reset(): void {
      expandedDirs.value = new Set<string>()
      selectedPath.value = null
      diffSelection.value = null
    }

    return {
      expandedDirs,
      selectedPath,
      diffSelection,
      isComparing,
      isExpanded,
      toggleDir,
      expandDir,
      selectFile,
      setDiffSelection,
      reset,
    }
  })
  return useStore() as unknown as BrowserUiStore
}

/**
 * W6 back-compat default browser UI store (the `files:default`
 * instance). Existing imports (`useFilesStore()`) keep working
 * unchanged; new code passes a `BrowserSource` and resolves its store
 * via {@link useBrowserUiStore} with `source.storeId`.
 */
export function useFilesStore(): BrowserUiStore {
  return useBrowserUiStore('files:default')
}
