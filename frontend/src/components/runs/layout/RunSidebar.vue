<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import FileTree from '@/components/files/FileTree.vue'
import { runArtifactSource, ApiError } from '@/lib/queries'
import type { RunView } from '@/lib/runView'

interface IterRow {
  seq: number
  phase: string | null
}

interface ChildRow {
  id: string
  status: string
}

interface ProjectRef {
  id: number
  name: string
}

const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled'])

const props = defineProps<{
  runId: string
  /**
   * The project this run belongs to. `null` until RunDetailView's
   * project query resolves; the title row is omitted while null so
   * the layout doesn't reflow on hydration. Once present, renders as
   * a router-link back to the project view.
   */
  project: ProjectRef | null
  selection: RunView
  /**
   * Run status. Drives empty-state copy in the Iters and Artifacts
   * sections (proposal §"Empty states"): a live run gets
   * "Waiting for first iter…" / "No artifacts yet"; a terminal run
   * with zero of either gets "—" / hidden. Defaulting to running is
   * safe — under-render an em-dash is less surprising than
   * over-render a "waiting" copy on a finished run.
   */
  status?: string
  iters: ReadonlyArray<IterRow>
  children: ReadonlyArray<ChildRow>
}>()

const emit = defineEmits<{
  (e: 'update:view', view: RunView): void
}>()

const isOverviewSelected = computed(() => props.selection.kind === 'overview')

function isIterSelected(seq: number): boolean {
  return props.selection.kind === 'iter' && props.selection.seq === seq
}

function selectOverview(): void {
  emit('update:view', { kind: 'overview' })
}

function selectIter(seq: number): void {
  emit('update:view', { kind: 'iter', seq })
}

const artifactSource = computed(() => runArtifactSource(props.runId))
// `useListing` is bound to the source captured at component setup; if
// runId ever changed in place, artifactRoot would stay stale. Safe
// here because vue-router unmounts the whole RunDetailView tree on
// /runs/:id navigation — runId is effectively immutable per mount.
const artifactRoot = artifactSource.value.useListing(() => '')
const artifactsMissing = computed(
  () =>
    artifactRoot.error.value instanceof ApiError &&
    artifactRoot.error.value.status === 404,
)
// Non-404 errors (500, network failure, etc.) collapse to "section
// hidden" — the sidebar has no inline error surface. The right-pane
// ArtifactPanel renders the artifact viewer's own error UI when the
// user navigates into a file; the rail stays quiet. Promote to a
// visible state when/if a sidebar error affordance lands.
const artifactsLoaded = computed(
  () => !artifactsMissing.value && artifactRoot.data.value != null,
)
const artifactsEntries = computed(
  () => artifactRoot.data.value?.entries ?? [],
)
const artifactsEmpty = computed(
  () => artifactsLoaded.value && artifactsEntries.value.length === 0,
)

const isTerminal = computed(
  () => props.status != null && TERMINAL_STATUSES.has(props.status),
)

/**
 * "Waiting for first iter…" placeholder semantics (proposal §"Empty
 * states"): show when the run has produced no iters yet AND the run
 * isn't done. A terminal run with zero iters (which would itself be
 * surprising — orphan-recovery should have left a `run_ended`)
 * collapses the section entirely rather than showing a misleading
 * "waiting" copy.
 */
const showItersWaiting = computed(
  () => props.iters.length === 0 && !isTerminal.value,
)

/**
 * Artifacts-section copy:
 *   - dir loaded with files          → render FileTree
 *   - dir loaded but empty           → "No artifacts yet" / "—"
 *   - dir 404                        → same empty copy (just-started
 *                                       run hasn't created the dir yet)
 *   - non-404 error (network, 500)   → section hidden (no inline
 *                                       error surface in the rail)
 *
 * `null` value means the artifacts section is hidden entirely.
 */
const artifactsEmptyCopy = computed<string | null>(() => {
  if (artifactsLoaded.value && !artifactsEmpty.value) return null
  // 404 with non-terminal status, OR loaded-but-empty with non-terminal.
  if (artifactsMissing.value || artifactsEmpty.value) {
    return isTerminal.value ? '—' : 'No artifacts yet'
  }
  return null
})

function onArtifactSelect(path: string): void {
  emit('update:view', { kind: 'artifact', path })
}
</script>

<template>
  <nav
    class="run-sidebar"
    aria-label="Run navigation"
    data-testid="run-sidebar"
  >
    <RouterLink
      v-if="project"
      :to="`/projects/${project.id}`"
      class="run-sidebar__project"
      data-testid="sidebar-project-title"
      :title="`Open project: ${project.name}`"
    >
      <span class="run-sidebar__project-eyebrow">Project</span>
      <span class="run-sidebar__project-name">{{ project.name }}</span>
    </RouterLink>

    <div
      role="listbox"
      aria-orientation="vertical"
      aria-label="Run views"
      class="run-sidebar__listbox"
      data-testid="sidebar-listbox"
    >
      <button
        type="button"
        role="option"
        :aria-selected="isOverviewSelected"
        class="run-sidebar__row run-sidebar__row--overview"
        :class="{ 'run-sidebar__row--selected': isOverviewSelected }"
        data-testid="sidebar-overview"
        @click="selectOverview"
      >
        Overview
      </button>

      <section
        v-if="iters.length > 0 || showItersWaiting"
        role="group"
        aria-labelledby="sidebar-iters-heading"
        class="run-sidebar__section"
        data-testid="sidebar-iters-section"
      >
        <h3
          id="sidebar-iters-heading"
          class="run-sidebar__heading"
        >
          Iters
          <span
            v-if="iters.length > 0"
            class="run-sidebar__count"
          >{{ iters.length }}</span>
        </h3>
        <button
          v-for="iter in iters"
          :key="iter.seq"
          type="button"
          role="option"
          :aria-selected="isIterSelected(iter.seq)"
          class="run-sidebar__row"
          :class="{ 'run-sidebar__row--selected': isIterSelected(iter.seq) }"
          :data-testid="`sidebar-iter-${iter.seq}`"
          @click="selectIter(iter.seq)"
        >
          <span class="run-sidebar__row-seq">#{{ iter.seq }}</span>
          <span class="run-sidebar__row-label">{{ iter.phase ?? '—' }}</span>
        </button>
        <p
          v-if="showItersWaiting"
          class="run-sidebar__empty"
          data-testid="sidebar-iters-waiting"
        >
          Waiting for first iter…
        </p>
      </section>
    </div>

    <section
      v-if="artifactsLoaded || artifactsEmptyCopy != null"
      role="group"
      aria-labelledby="sidebar-artifacts-heading"
      class="run-sidebar__section"
      data-testid="sidebar-artifacts-section"
    >
      <h3
        id="sidebar-artifacts-heading"
        class="run-sidebar__heading"
      >
        Artifacts
      </h3>
      <FileTree
        v-if="artifactsLoaded && !artifactsEmpty"
        :source="artifactSource"
        aria-label="Run artifacts"
        @select="onArtifactSelect"
      />
      <p
        v-else-if="artifactsEmptyCopy != null"
        class="run-sidebar__empty"
        data-testid="sidebar-artifacts-empty"
      >
        {{ artifactsEmptyCopy }}
      </p>
    </section>

    <section
      v-if="children.length > 0"
      role="group"
      aria-labelledby="sidebar-children-heading"
      class="run-sidebar__section"
      data-testid="sidebar-children-section"
    >
      <h3
        id="sidebar-children-heading"
        class="run-sidebar__heading"
      >
        Children
        <span class="run-sidebar__count">{{ children.length }}</span>
      </h3>
      <router-link
        v-for="child in children"
        :key="child.id"
        :to="`/runs/${child.id}`"
        class="run-sidebar__row run-sidebar__row--link"
        :data-testid="`sidebar-child-${child.id}`"
      >
        <StatusBadge :status="child.status" />
        <span class="run-sidebar__row-label">{{ child.id.slice(0, 8) }}</span>
      </router-link>
    </section>
  </nav>
</template>

<style scoped>
.run-sidebar {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 0.5rem;
  border-right: 1px solid var(--color-border);
  background: var(--color-surface);
  min-height: 100%;
}

.run-sidebar__project {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.6rem 0.6rem;
  margin-bottom: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
}

.run-sidebar__project:hover .run-sidebar__project-name {
  color: var(--color-accent);
}

.run-sidebar__project-eyebrow {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-dim);
  font-weight: 600;
}

.run-sidebar__project-name {
  font-size: 1.05em;
  font-weight: 700;
  letter-spacing: 0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-sidebar__section {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin-top: 0.5rem;
}

.run-sidebar__heading {
  margin: 0 0 0.25rem;
  padding: 0 0.5rem;
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-dim);
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.run-sidebar__count {
  font-size: 0.9em;
  color: var(--color-text-dim);
}

.run-sidebar__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  font: inherit;
  cursor: pointer;
  text-decoration: none;
}

.run-sidebar__row:hover {
  background: var(--color-surface-hover);
}

.run-sidebar__row--selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.run-sidebar__row-seq {
  font-family: var(--font-mono);
  color: var(--color-text-dim);
  min-width: 2rem;
}

.run-sidebar__row-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-sidebar__row--overview {
  font-weight: 600;
}

.run-sidebar__section :deep(.file-tree) {
  font-size: 0.85em;
}

.run-sidebar__listbox {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.run-sidebar__empty {
  margin: 0.25rem 0.5rem;
  font-size: 0.85em;
  color: var(--color-text-dim);
}
</style>
