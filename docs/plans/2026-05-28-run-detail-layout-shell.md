# Run-detail layout shell — Phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-arrange `RunDetailView.vue` into a two-column master-detail
layout — left rail (Overview / Iters / Artifacts / Children) + right
pane that routes on selection — with URL-reflected selection state and
status-driven smart-default. No new pane internals; existing components
(`TimelinePane`, `PauseAnswerForm`, `ChildrenPane`, `WorktreePane`,
`FileTree`, `FileViewer`) render inside the new shape unchanged.

**Architecture:** Three new layout components under
`frontend/src/components/runs/layout/` (`RunSidebar`, `RunRightPane`,
plus three thin body panels: `OverviewPanel`, `IterTimelinePanel`,
`ArtifactPanel`). A new URL-state helper module (`frontend/src/lib/runView.ts`)
owns view parsing / serialisation / smart-default. `RunDetailView`
becomes a thin orchestrator that wires the URL ↔ layout
binding and threads run/event state to the panes.

**Tech Stack:** Vue 3 SFC, TypeScript strict, Pinia + Pinia Colada,
vue-router v5 (`useRoute` / `useRouter`), Vitest + jsdom + Vue Test
Utils. No new runtime deps.

**Project context — MVP acceptance-testing exception (2026-05-28).**
CLAUDE.md flags relay-v2 as paused for MVP acceptance testing
(`docs/acceptance-testing.md`); feature work is normally frozen. This
plan is an **explicit exception** authorised by the user
(2026-05-28): acceptance testing surfaced the run-detail view as a
usability blocker — operators can't reliably make sense of a live run
under the current vertical stack. Phase 1 is the smallest shippable
slice that fixes the structural problem. Phases 2–7 from the
proposal (`docs/proposals/run-detail-layout.md`) follow their own
plans; each one re-evaluates whether the freeze still holds.

---

## File structure

**New files (8):**

- `frontend/src/lib/runView.ts` — `RunView` discriminated union +
  `parseView(query)` / `serializeView(view)` / `smartDefault(detail)`.
  No Vue / no router import — pure functions.
- `frontend/src/components/runs/layout/RunSidebar.vue` — left rail.
  Sections: Overview, Iters, Artifacts (wraps `FileTree`), Children
  (hidden when empty). Emits `update:view`.
- `frontend/src/components/runs/layout/RunRightPane.vue` — right pane
  shell. Header + routed body. Hosts `OverviewPanel`,
  `IterTimelinePanel`, `ArtifactPanel`. Renders the existing
  `PauseAnswerForm` inline above the routed body when paused
  (extracted into `PauseBanner` in Phase 4, not here).
- `frontend/src/components/runs/layout/OverviewPanel.vue` — prompt
  block + cross-iter timeline (renders the existing `TimelinePane`
  with `selectedIterSeq = null`).
- `frontend/src/components/runs/layout/IterTimelinePanel.vue` — thin
  wrapper that renders `TimelinePane` scoped to one iter seq.
- `frontend/src/components/runs/layout/ArtifactPanel.vue` — renders
  `FileViewer` for a selected artifact path. Reads from the same
  `runArtifactSource(runId)` browser source `ArtifactsPane` uses.
- `frontend/tests/runView.spec.ts`
- `frontend/tests/RunSidebar.spec.ts`
- `frontend/tests/RunRightPane.spec.ts`

**Modified files (2):**

- `frontend/src/views/RunDetailView.vue` — gut the vertical-stack
  template; replace with `<RunSidebar>` + `<RunRightPane>` driven by a
  `currentView` computed bound to `?view=` query. Smart-default fires
  on first `detail` arrival when `?view=` is absent. Preserves: status
  badge, run id, started_at, iter count, phase, parent chip, failure
  banner, cancel mutation, lifecycle / SSE plumbing, `onResumed`. The
  Cancel button moves into `RunRightPane`'s header. The
  `ArtifactsPane` + `WorktreePane` direct renders are removed (their
  data is now consumed by the rail / right-pane bodies).
- `frontend/tests/RunDetailView.spec.ts` — update selectors to find
  status / timeline / iters / artifacts in their new homes
  (`[data-testid="run-sidebar"]`, `[data-testid="run-right-pane"]`,
  etc.). Add new tests for URL ↔ selection round-trip and
  smart-default.

**Unchanged (asserted by the build):**

- `TimelinePane.vue`, `ItersPane.vue` (wrapped — see Task 4),
  `ArtifactsPane.vue` (deleted in Task 9; data sources reused),
  `PauseAnswerForm.vue`, `ChildrenPane.vue`, `WorktreePane.vue`,
  `FileTree.vue`, `FileTreeNode.vue`, `FileViewer.vue`,
  `RunHealthBadge.vue`, `ParentRunChip.vue`, `StatusBadge.vue`.
- `stores/events.ts`, `stores/currentRun.ts`, `stores/files.ts`,
  `lib/queries.ts`, `lib/routes.ts`.
- Backend, REST, SSE, OTel.

---

## Existing contracts that must survive

These are load-bearing for tests and live behaviour; any task that
appears to break them is a sign the task is wrong.

1. **SSE open() once, on first detail arrival.** The `watch(detail, …, {
   immediate: true })` that calls `eventsStore.open()` exactly once
   (guarded by the local `opened` flag) stays in `RunDetailView`. Do not
   move it into the right pane.
2. **`onLifecycle` + `markTerminal()` defuse.** The lifecycle handler
   refetches detail and, if terminal, calls `eventsStore.markTerminal()`
   to prevent reconnect-storms. Preserve verbatim.
3. **`onResumed` reopens SSE from current cursor.** After
   `PauseAnswerForm` emits `@resumed`, `RunDetailView` refetches detail
   and calls `eventsStore.open(...)` again. Preserve verbatim.
4. **Cancel cascade label (9e).** `cancelLabel` reads "Cancel run and N
   children" when `childCount > 0`. The button moves into
   `RunRightPane`'s header but the computed lives in `RunDetailView` and
   is passed down as a prop.
5. **Failure banner (`agent_end_no_signal` hint).** Preserved verbatim
   inside `RunRightPane`'s header region, below the meta row.
6. **`onBeforeUnmount` resets stores.** `eventsStore.reset()` +
   `currentRun.reset()` stay in `RunDetailView`.
7. **`pauseReviewPaths` plural-first / legacy-scalar fallback (14f).**
   The computed stays in `RunDetailView` and is passed into
   `PauseAnswerForm` unchanged. Do not duplicate the migration logic in
   the new components.
8. **Dual-list contract.** Phase 1 introduces no new event kind so
   `KNOWN_EVENT_TYPES` × `INVALIDATING_KINDS` are untouched.

---

## URL contract (Phase 1 surface area)

Only `view` is parsed in Phase 1. `kinds` ships in Phase 2.

| URL                                              | `RunView`                             |
|--------------------------------------------------|---------------------------------------|
| `/runs/:id`                                      | smart-default (computed from detail)  |
| `/runs/:id?view=overview`                        | `{ kind: 'overview' }`                |
| `/runs/:id?view=iter:2`                          | `{ kind: 'iter', seq: 2 }`            |
| `/runs/:id?view=artifact:improvement-plan.md`    | `{ kind: 'artifact', path: '…' }`     |
| `/runs/:id?view=artifact:discussions%2Ffoo.md`   | `{ kind: 'artifact', path: 'discussions/foo.md' }` (URL-decoded) |

**Malformed `view` falls back to smart-default silently** — never
throws, never blanks the view. (Examples: `view=iter:abc`, `view=foo`,
`view=artifact:` with empty path.)

Smart-default rules (Phase 1 — paused branch ships in Phase 4):

- `status ∈ {running, awaiting_children}` → `{ kind: 'iter', seq: <latest> }`
  if at least one iter exists; otherwise `{ kind: 'overview' }`.
- `status ∈ {done, failed, cancelled, paused}` → `{ kind: 'overview' }`.

(Phase 4 will change the `paused` branch to auto-select the first
`review_path`.)

---

## Tasks

### Task 1: `runView.ts` — URL state helpers

**Files:**
- Create: `frontend/src/lib/runView.ts`
- Test: `frontend/tests/runView.spec.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/tests/runView.spec.ts
import { describe, it, expect } from 'vitest'
import {
  parseView,
  serializeView,
  smartDefault,
  type RunView,
} from '../src/lib/runView'

describe('parseView', () => {
  it('returns null when ?view is absent', () => {
    expect(parseView({})).toBeNull()
  })

  it('parses view=overview', () => {
    expect(parseView({ view: 'overview' })).toEqual({ kind: 'overview' })
  })

  it('parses view=iter:N as { kind: iter, seq }', () => {
    expect(parseView({ view: 'iter:2' })).toEqual({ kind: 'iter', seq: 2 })
  })

  it('parses view=artifact:<path> and URL-decodes nested paths', () => {
    expect(parseView({ view: 'artifact:improvement-plan.md' })).toEqual({
      kind: 'artifact',
      path: 'improvement-plan.md',
    })
    expect(parseView({ view: 'artifact:discussions%2Ffoo.md' })).toEqual({
      kind: 'artifact',
      path: 'discussions/foo.md',
    })
  })

  it('falls back to null on malformed inputs (silent)', () => {
    expect(parseView({ view: 'iter:abc' })).toBeNull()
    expect(parseView({ view: 'iter:' })).toBeNull()
    expect(parseView({ view: 'foo' })).toBeNull()
    expect(parseView({ view: 'artifact:' })).toBeNull()
  })

  it('accepts an array query value (router quirk) by taking the first', () => {
    expect(parseView({ view: ['iter:1', 'iter:2'] })).toEqual({
      kind: 'iter',
      seq: 1,
    })
  })
})

describe('serializeView', () => {
  it('serialises overview', () => {
    expect(serializeView({ kind: 'overview' })).toBe('overview')
  })

  it('serialises iter', () => {
    expect(serializeView({ kind: 'iter', seq: 3 })).toBe('iter:3')
  })

  it('serialises artifact and URL-encodes nested paths', () => {
    expect(serializeView({ kind: 'artifact', path: 'plan.md' })).toBe(
      'artifact:plan.md',
    )
    expect(
      serializeView({ kind: 'artifact', path: 'discussions/foo.md' }),
    ).toBe('artifact:discussions%2Ffoo.md')
  })
})

describe('smartDefault', () => {
  const baseIters = [{ seq: 1, phase: 'planning' }, { seq: 2, phase: 'planning' }]

  it('returns latest iter for running with iters', () => {
    expect(smartDefault({ status: 'running', iters: baseIters })).toEqual({
      kind: 'iter',
      seq: 2,
    })
  })

  it('returns latest iter for awaiting_children with iters', () => {
    expect(
      smartDefault({ status: 'awaiting_children', iters: baseIters }),
    ).toEqual({ kind: 'iter', seq: 2 })
  })

  it('returns overview for running with no iters yet', () => {
    expect(smartDefault({ status: 'running', iters: [] })).toEqual({
      kind: 'overview',
    })
  })

  it('returns overview for terminal statuses', () => {
    for (const s of ['done', 'failed', 'cancelled']) {
      expect(smartDefault({ status: s, iters: baseIters })).toEqual({
        kind: 'overview',
      })
    }
  })

  it('returns overview for paused (Phase 1 — Phase 4 changes this)', () => {
    expect(smartDefault({ status: 'paused', iters: baseIters })).toEqual({
      kind: 'overview',
    })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/runView.spec.ts`
Expected: FAIL with "Cannot find module '../src/lib/runView'".

- [ ] **Step 3: Implement `runView.ts`**

```typescript
// frontend/src/lib/runView.ts

/**
 * URL-reflected selection state for `RunDetailView`. The right pane
 * routes its body on this discriminated union; the left rail
 * highlights the matching row.
 *
 * Serialised to/from `?view=` in the URL — see {@link parseView} and
 * {@link serializeView}. Absent `?view=` resolves to {@link smartDefault}.
 */
export type RunView =
  | { kind: 'overview' }
  | { kind: 'iter'; seq: number }
  | { kind: 'artifact'; path: string }

type QueryShape = Record<string, string | string[] | null | undefined>

function firstOf(v: string | string[] | null | undefined): string | null {
  if (v == null) return null
  if (Array.isArray(v)) return v[0] ?? null
  return v
}

/**
 * Parse `?view=…` into a {@link RunView}. Returns `null` when absent or
 * malformed — callers fall back to {@link smartDefault}. NEVER throws.
 */
export function parseView(query: QueryShape): RunView | null {
  const raw = firstOf(query.view)
  if (raw == null || raw === '') return null

  if (raw === 'overview') return { kind: 'overview' }

  if (raw.startsWith('iter:')) {
    const tail = raw.slice('iter:'.length)
    if (tail === '') return null
    const seq = Number(tail)
    if (!Number.isInteger(seq) || seq < 1) return null
    return { kind: 'iter', seq }
  }

  if (raw.startsWith('artifact:')) {
    const tail = raw.slice('artifact:'.length)
    if (tail === '') return null
    let path: string
    try {
      path = decodeURIComponent(tail)
    } catch {
      return null
    }
    if (path === '') return null
    return { kind: 'artifact', path }
  }

  return null
}

/**
 * Serialise a {@link RunView} into the `?view=` form. Inverse of
 * {@link parseView}.
 */
export function serializeView(view: RunView): string {
  switch (view.kind) {
    case 'overview':
      return 'overview'
    case 'iter':
      return `iter:${view.seq}`
    case 'artifact':
      return `artifact:${encodeURIComponent(view.path)}`
  }
}

/**
 * Compute the default {@link RunView} when `?view=` is absent. Driven
 * by run status (Phase 1 — Phase 4 of the run-detail-layout proposal
 * adds the paused→review_path branch).
 */
export function smartDefault(detail: {
  status: string
  iters: ReadonlyArray<{ seq: number }>
}): RunView {
  if (detail.status === 'running' || detail.status === 'awaiting_children') {
    const latest = detail.iters[detail.iters.length - 1]
    if (latest != null) return { kind: 'iter', seq: latest.seq }
    return { kind: 'overview' }
  }
  return { kind: 'overview' }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/runView.spec.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/runView.ts frontend/tests/runView.spec.ts
git commit -m "$(cat <<'EOF'
feat(frontend): runView — URL state helpers for run-detail layout

parseView / serializeView / smartDefault for ?view=overview|iter:N|
artifact:<path>. Pure functions, no Vue / router imports. Phase 1 of
run-detail-layout (docs/proposals/run-detail-layout.md).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `RunSidebar` skeleton (Overview + Iters + Children)

**Files:**
- Create: `frontend/src/components/runs/layout/RunSidebar.vue`
- Test: `frontend/tests/RunSidebar.spec.ts`

This task adds the rail with Overview, Iters, and Children sections;
Artifacts comes in Task 3.

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/tests/RunSidebar.spec.ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import RunSidebar from '../src/components/runs/layout/RunSidebar.vue'
import type { RunView } from '../src/lib/runView'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
    ],
  })
}

function mountSidebar(props: {
  selection: RunView
  iters?: Array<{ seq: number; phase: string; status_kind?: string | null }>
  children?: Array<{ id: string; status: string }>
  runId?: string
}) {
  return mount(RunSidebar, {
    props: {
      runId: props.runId ?? 'run-1',
      selection: props.selection,
      iters: props.iters ?? [],
      children: props.children ?? [],
    },
    global: {
      plugins: [createPinia(), makeRouter()],
    },
  })
}

describe('RunSidebar', () => {
  it('renders the Overview entry, always selectable', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    const row = w.get('[data-testid="sidebar-overview"]')
    expect(row.text()).toContain('Overview')
    expect(row.attributes('aria-selected')).toBe('true')
  })

  it('renders one row per iter under the ITERS section', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [
        { seq: 1, phase: 'planning' },
        { seq: 2, phase: 'planning' },
      ],
    })
    const rows = w.findAll('[data-testid^="sidebar-iter-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('#1')
    expect(rows[1]!.text()).toContain('#2')
  })

  it('marks the selected iter as aria-selected', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 2 },
      iters: [
        { seq: 1, phase: 'planning' },
        { seq: 2, phase: 'planning' },
      ],
    })
    const sel = w.get('[data-testid="sidebar-iter-2"]')
    expect(sel.attributes('aria-selected')).toBe('true')
    const other = w.get('[data-testid="sidebar-iter-1"]')
    expect(other.attributes('aria-selected')).toBe('false')
  })

  it('emits update:view when an iter row is clicked', async () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    await w.get('[data-testid="sidebar-iter-1"]').trigger('click')
    expect(w.emitted('update:view')).toEqual([[{ kind: 'iter', seq: 1 }]])
  })

  it('emits update:view when Overview is clicked', async () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 1 },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    await w.get('[data-testid="sidebar-overview"]').trigger('click')
    expect(w.emitted('update:view')).toEqual([[{ kind: 'overview' }]])
  })

  it('hides the CHILDREN section when children is empty', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="sidebar-children-section"]').exists()).toBe(
      false,
    )
  })

  it('renders one row per child when children is non-empty', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      children: [
        { id: 'child-a', status: 'running' },
        { id: 'child-b', status: 'done' },
      ],
    })
    const rows = w.findAll('[data-testid^="sidebar-child-"]')
    expect(rows).toHaveLength(2)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/RunSidebar.spec.ts`
Expected: FAIL with "Cannot find module … RunSidebar.vue".

- [ ] **Step 3: Implement `RunSidebar.vue`**

```vue
<!-- frontend/src/components/runs/layout/RunSidebar.vue -->
<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import type { RunView } from '@/lib/runView'

interface IterRow {
  seq: number
  phase: string | null
  status_kind?: string | null
}

interface ChildRow {
  id: string
  status: string
}

const props = defineProps<{
  runId: string
  selection: RunView
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

const childCount = computed(() => props.children.length)
</script>

<template>
  <aside
    class="run-sidebar"
    role="listbox"
    aria-orientation="vertical"
    aria-label="Run navigation"
    data-testid="run-sidebar"
  >
    <button
      type="button"
      role="option"
      class="run-sidebar__row run-sidebar__row--overview"
      :class="{ 'run-sidebar__row--selected': isOverviewSelected }"
      :aria-selected="isOverviewSelected ? 'true' : 'false'"
      data-testid="sidebar-overview"
      @click="selectOverview"
    >
      Overview
    </button>

    <section
      v-if="iters.length > 0"
      role="group"
      aria-labelledby="sidebar-iters-heading"
      class="run-sidebar__section"
    >
      <h3 id="sidebar-iters-heading" class="run-sidebar__heading">
        Iters
        <span class="run-sidebar__count">{{ iters.length }}</span>
      </h3>
      <button
        v-for="iter in iters"
        :key="iter.seq"
        type="button"
        role="option"
        class="run-sidebar__row"
        :class="{ 'run-sidebar__row--selected': isIterSelected(iter.seq) }"
        :aria-selected="isIterSelected(iter.seq) ? 'true' : 'false'"
        :data-testid="`sidebar-iter-${iter.seq}`"
        @click="selectIter(iter.seq)"
      >
        <span class="run-sidebar__row-seq">#{{ iter.seq }}</span>
        <span class="run-sidebar__row-label">{{ iter.phase ?? '—' }}</span>
      </button>
    </section>

    <section
      v-if="childCount > 0"
      role="group"
      aria-labelledby="sidebar-children-heading"
      class="run-sidebar__section"
      data-testid="sidebar-children-section"
    >
      <h3 id="sidebar-children-heading" class="run-sidebar__heading">
        Children
        <span class="run-sidebar__count">{{ childCount }}</span>
      </h3>
      <router-link
        v-for="child in children"
        :key="child.id"
        :to="`/runs/${child.id}`"
        class="run-sidebar__row run-sidebar__row--link"
        :data-testid="`sidebar-child-${child.id}`"
      >
        <StatusBadge :status="child.status" />
        <span class="run-sidebar__row-label">{{ child.id.slice(0, 14) }}</span>
      </router-link>
    </section>
  </aside>
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
  background: var(--color-surface-hover, rgba(255, 255, 255, 0.04));
}

.run-sidebar__row--selected {
  border-color: var(--color-accent, #4a90d9);
  background: rgba(74, 144, 217, 0.08);
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
</style>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/RunSidebar.spec.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/layout/RunSidebar.vue \
        frontend/tests/RunSidebar.spec.ts
git commit -m "$(cat <<'EOF'
feat(frontend): RunSidebar — Overview / Iters / Children rail

Left-rail master selector for run-detail layout. Overview entry is
always present; Iters section lists each iter; Children section
hidden when empty. Emits update:view on row click. Artifacts section
arrives in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `RunSidebar` — Artifacts section (wraps `FileTree`)

**Files:**
- Modify: `frontend/src/components/runs/layout/RunSidebar.vue`
- Modify: `frontend/tests/RunSidebar.spec.ts`

The Artifacts section uses the same `runArtifactSource(runId)` source
as the existing `ArtifactsPane` so the rail and the right-pane viewer
share one cache entry. Selection emits
`{ kind: 'artifact', path }`. A 404 root listing (no artifacts yet)
renders an empty-state label, NOT an error.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/RunSidebar.spec.ts`:

```typescript
import { vi } from 'vitest'

// Mock the api client so runArtifactSource's listing query is observable.
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    GET: (...a: unknown[]) => GET(...a),
    POST: vi.fn(),
  },
}))

import { PiniaColada } from '@pinia/colada'
import { flushPromises } from '@vue/test-utils'

function ok<T>(data: T) {
  return { data, error: undefined, response: new Response(null, { status: 200 }) }
}

function err(status: number, detail = 'not found') {
  return {
    data: undefined,
    error: { detail },
    response: new Response(null, { status }),
  }
}

function mountWithColada(props: Parameters<typeof mountSidebar>[0]) {
  return mount(RunSidebar, {
    props: {
      runId: props.runId ?? 'run-1',
      selection: props.selection,
      iters: props.iters ?? [],
      children: props.children ?? [],
    },
    global: {
      plugins: [createPinia(), PiniaColada, makeRouter()],
    },
  })
}

describe('RunSidebar — Artifacts section', () => {
  beforeEach(() => GET.mockReset())

  it('hides the Artifacts section while listing 404s ("no artifacts yet")', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) return Promise.resolve(err(404))
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({ selection: { kind: 'overview' } })
    await flushPromises()
    expect(
      w.find('[data-testid="sidebar-artifacts-section"]').exists(),
    ).toBe(false)
  })

  it('renders one row per artifact when listing succeeds', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) {
        return Promise.resolve(
          ok([
            { name: 'evaluation-report.md', kind: 'file', size: 100 },
            { name: 'improvement-plan.md', kind: 'file', size: 200 },
          ]),
        )
      }
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({ selection: { kind: 'overview' } })
    await flushPromises()
    const section = w.get('[data-testid="sidebar-artifacts-section"]')
    expect(section.text()).toContain('evaluation-report.md')
    expect(section.text()).toContain('improvement-plan.md')
  })

  it('emits update:view with { kind: artifact, path } on file select', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) {
        return Promise.resolve(
          ok([{ name: 'plan.md', kind: 'file', size: 100 }]),
        )
      }
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({ selection: { kind: 'overview' } })
    await flushPromises()
    // FileTree emits `select` with the path string.
    await w.findComponent({ name: 'FileTree' }).vm.$emit('select', 'plan.md')
    expect(w.emitted('update:view')).toEqual([
      [{ kind: 'artifact', path: 'plan.md' }],
    ])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/RunSidebar.spec.ts`
Expected: FAIL — Artifacts section assertions.

- [ ] **Step 3: Add the Artifacts section to `RunSidebar.vue`**

Add to the `<script setup>` block (after the existing imports):

```typescript
import FileTree from '@/components/files/FileTree.vue'
import { runArtifactSource, ApiError } from '@/lib/queries'

const artifactSource = computed(() => runArtifactSource(props.runId))
const artifactRoot = artifactSource.value.useListing(() => '')
const artifactsMissing = computed(
  () =>
    artifactRoot.error.value instanceof ApiError &&
    artifactRoot.error.value.status === 404,
)
const artifactsLoaded = computed(
  () => !artifactsMissing.value && artifactRoot.data.value != null,
)

function onArtifactSelect(path: string): void {
  emit('update:view', { kind: 'artifact', path })
}
```

Add to the template, after the Iters section:

```vue
<section
  v-if="artifactsLoaded"
  role="group"
  aria-labelledby="sidebar-artifacts-heading"
  class="run-sidebar__section"
  data-testid="sidebar-artifacts-section"
>
  <h3 id="sidebar-artifacts-heading" class="run-sidebar__heading">
    Artifacts
  </h3>
  <FileTree
    :source="artifactSource"
    aria-label="Run artifacts"
    @select="onArtifactSelect"
  />
</section>
```

Add styles to make `FileTree` fit the rail width (the rail is narrow):

```css
.run-sidebar__section :deep(.file-tree) {
  font-size: 0.85em;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/RunSidebar.spec.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/layout/RunSidebar.vue \
        frontend/tests/RunSidebar.spec.ts
git commit -m "$(cat <<'EOF'
feat(frontend): RunSidebar — Artifacts section via runArtifactSource

Wraps the shared FileTree against the run's artifact source so the
rail and (forthcoming) ArtifactPanel right-pane viewer share one
Pinia Colada cache entry. 404 root listing renders nothing (run has
no artifacts yet, not an error). File select emits
update:view = { kind: artifact, path }.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Body panels — `OverviewPanel`, `IterTimelinePanel`, `ArtifactPanel`

**Files:**
- Create: `frontend/src/components/runs/layout/OverviewPanel.vue`
- Create: `frontend/src/components/runs/layout/IterTimelinePanel.vue`
- Create: `frontend/src/components/runs/layout/ArtifactPanel.vue`

Three thin wrappers. No new logic — each delegates to an existing
component. They live in their own files so `RunRightPane` can route on
selection without an `<component :is>` zoo.

- [ ] **Step 1: Create `OverviewPanel.vue`**

```vue
<!-- frontend/src/components/runs/layout/OverviewPanel.vue -->
<script setup lang="ts">
// Right-pane body when selection.kind === 'overview'. Renders the
// run's prompt + the cross-iter live timeline (TimelinePane with
// selectedIterSeq = null = no scope filter).

import TimelinePane from '@/components/runs/TimelinePane.vue'
import type { TimelineEvent, PendingTurn } from '@/stores/events'

defineProps<{
  runId: string
  promptBody: string
  events: ReadonlyArray<TimelineEvent>
  pendingTurns: ReadonlyArray<PendingTurn>
}>()
</script>

<template>
  <div class="overview-panel" data-testid="overview-panel">
    <section class="overview-panel__prompt">
      <h2 class="overview-panel__heading">Prompt</h2>
      <pre class="overview-panel__prompt-body">{{ promptBody }}</pre>
    </section>

    <section class="overview-panel__timeline">
      <h2 class="overview-panel__heading">Timeline</h2>
      <TimelinePane
        :events="events"
        :selected-iter-seq="null"
        :pending-turns="pendingTurns"
        :run-id="runId"
      />
    </section>
  </div>
</template>

<style scoped>
.overview-panel {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.overview-panel__heading {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
}

.overview-panel__prompt-body {
  margin: 0;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  font-family: var(--font-mono);
  font-size: 0.85em;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 30vh;
  overflow-y: auto;
}
</style>
```

⚠ **Type-import check.** Verify `TimelineEvent` and `PendingTurn` are
exported from `@/stores/events`. If they are not, replace the import
with the equivalent local type aliases inferred from
`useEventsStore().events` and `.pendingTurns` (i.e.
`ReturnType<typeof useEventsStore>['events']`) — adjust *before*
running the type-check. Mirror the same import pattern as the existing
`RunDetailView.vue` uses today (it references both via inferred
types — no explicit imports), so if you're unsure, drop the imports and
write `unknown[]` props that the caller types via `defineProps`.

- [ ] **Step 2: Create `IterTimelinePanel.vue`**

```vue
<!-- frontend/src/components/runs/layout/IterTimelinePanel.vue -->
<script setup lang="ts">
// Right-pane body when selection.kind === 'iter'. Thin wrapper around
// TimelinePane scoped to one iter seq via its existing
// selected-iter-seq prop (the same prop ItersPane drives today).

import TimelinePane from '@/components/runs/TimelinePane.vue'
import ItersPane from '@/components/runs/ItersPane.vue'

defineProps<{
  runId: string
  iterSeq: number
  iters: ReadonlyArray<{ seq: number; phase: string | null }>
  events: ReadonlyArray<unknown>
  pendingTurns: ReadonlyArray<unknown>
}>()
</script>

<template>
  <div class="iter-timeline-panel" data-testid="iter-timeline-panel">
    <header class="iter-timeline-panel__header">
      <h2 class="iter-timeline-panel__heading">Iter #{{ iterSeq }}</h2>
    </header>

    <TimelinePane
      :events="events as never[]"
      :selected-iter-seq="iterSeq"
      :pending-turns="pendingTurns as never[]"
      :run-id="runId"
    />

    <!-- The iter-row inspector (existing ItersPane) stays visible for
         status/timing detail of the selected iter. Phase 1 keeps it
         rendered intact below the timeline; later phases (5 — drawer)
         may move per-iter detail into a richer view. -->
    <ItersPane :iters="iters as never[]" />
  </div>
</template>

<style scoped>
.iter-timeline-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.iter-timeline-panel__heading {
  margin: 0;
  font-size: 1.05rem;
}
</style>
```

Note: the `as never[]` casts are a pragmatic shim because Phase 1 keeps
existing pane prop types unchanged; future phases will tighten this.
The casts are confined to this wrapper.

- [ ] **Step 3: Create `ArtifactPanel.vue`**

```vue
<!-- frontend/src/components/runs/layout/ArtifactPanel.vue -->
<script setup lang="ts">
// Right-pane body when selection.kind === 'artifact'. Renders the
// shared FileViewer against the run's artifact source. Phase 1: read
// only. Phase 4 (PauseBanner) wires `reviewPaths` to enable in-place
// editing when the file is a paused-review target.

import { computed } from 'vue'
import FileViewer from '@/components/files/FileViewer.vue'
import { runArtifactSource } from '@/lib/queries'

const props = defineProps<{
  runId: string
  path: string
}>()

const source = computed(() => runArtifactSource(props.runId))
</script>

<template>
  <div class="artifact-panel" data-testid="artifact-panel">
    <FileViewer
      :source="source"
      :path="path"
    />
  </div>
</template>

<style scoped>
.artifact-panel {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
</style>
```

- [ ] **Step 4: Quick smoke — TypeScript builds**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: clean (no new errors). If `OverviewPanel`'s type imports
fail, apply the fallback noted in Step 1.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/layout/OverviewPanel.vue \
        frontend/src/components/runs/layout/IterTimelinePanel.vue \
        frontend/src/components/runs/layout/ArtifactPanel.vue
git commit -m "$(cat <<'EOF'
feat(frontend): body panels — Overview, IterTimeline, Artifact

Three thin wrappers around existing TimelinePane / FileViewer that
the upcoming RunRightPane routes between on selection.kind. No new
behaviour — these slot existing components into per-selection homes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `RunRightPane` — header + routed body

**Files:**
- Create: `frontend/src/components/runs/layout/RunRightPane.vue`
- Test: `frontend/tests/RunRightPane.spec.ts`

The right pane:

- Renders the run header (status, id, started, iter count, phase,
  cancel button, parent chip, failure banner).
- Renders the existing `PauseAnswerForm` inline above the body when
  paused (Phase 4 promotes this to `PauseBanner` with sticky styles).
- Routes the body on `selection.kind`.

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/tests/RunRightPane.spec.ts
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/client', () => ({
  api: {
    GET: vi.fn(() => Promise.resolve({ data: [], error: undefined, response: new Response(null, { status: 200 }) })),
    POST: vi.fn(),
  },
}))

import RunRightPane from '../src/components/runs/layout/RunRightPane.vue'

const baseDetail = {
  id: 'run-1',
  status: 'running',
  started_at: '2026-05-19T10:00:00Z',
  max_iters: 5,
  prompt_id: 7,
  prompt_body: 'do the thing',
  parent_run_id: null,
  iters: [{ seq: 1, phase: 'planning', signal_kind: null, signal_args: null }],
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
    ],
  })
}

function mountPane(over: Record<string, unknown> = {}) {
  return mount(RunRightPane, {
    props: {
      detail: baseDetail,
      selection: { kind: 'overview' },
      events: [],
      pendingTurns: [],
      lastHeartbeat: null,
      childCount: 0,
      cancelLabel: 'Cancel run',
      cancelling: false,
      pauseQuestion: '',
      pauseReviewPaths: [],
      ...over,
    },
    global: { plugins: [createPinia(), PiniaColada, makeRouter()] },
  })
}

describe('RunRightPane — header', () => {
  it('renders run id, status badge, started-at, iter count, phase', () => {
    const w = mountPane()
    expect(w.text()).toContain('run-1')
    expect(w.text()).toContain('1 / 5')
    expect(w.text()).toContain('planning')
  })

  it('shows Cancel button only when cancellable', async () => {
    const running = mountPane({ detail: { ...baseDetail, status: 'running' } })
    expect(running.find('[data-testid="cancel-run"]').exists()).toBe(true)

    const done = mountPane({ detail: { ...baseDetail, status: 'done' } })
    expect(done.find('[data-testid="cancel-run"]').exists()).toBe(false)
  })

  it('emits cancel on Cancel-button click', async () => {
    const w = mountPane()
    await w.get('[data-testid="cancel-run"]').trigger('click')
    expect(w.emitted('cancel')).toBeTruthy()
  })
})

describe('RunRightPane — body routing', () => {
  it('renders OverviewPanel for kind=overview', () => {
    const w = mountPane({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(true)
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-panel"]').exists()).toBe(false)
  })

  it('renders IterTimelinePanel for kind=iter', () => {
    const w = mountPane({ selection: { kind: 'iter', seq: 1 } })
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(true)
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(false)
  })

  it('renders ArtifactPanel for kind=artifact', async () => {
    const w = mountPane({
      selection: { kind: 'artifact', path: 'plan.md' },
    })
    await flushPromises()
    expect(w.find('[data-testid="artifact-panel"]').exists()).toBe(true)
  })
})

describe('RunRightPane — paused', () => {
  it('renders PauseAnswerForm above the body when status=paused', () => {
    const paused = {
      ...baseDetail,
      status: 'paused',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: 'pause',
          signal_args: { question: 'approve?' },
        },
      ],
    }
    const w = mountPane({
      detail: paused,
      pauseQuestion: 'approve?',
      pauseReviewPaths: [],
    })
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(true)
  })

  it('does NOT render PauseAnswerForm when status != paused', () => {
    const w = mountPane()
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/RunRightPane.spec.ts`
Expected: FAIL (component does not exist).

- [ ] **Step 3: Implement `RunRightPane.vue`**

```vue
<!-- frontend/src/components/runs/layout/RunRightPane.vue -->
<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RunHealthBadge from '@/components/runs/RunHealthBadge.vue'
import ParentRunChip from '@/components/shared/ParentRunChip.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import PauseAnswerForm from '@/components/runs/PauseAnswerForm.vue'
import OverviewPanel from './OverviewPanel.vue'
import IterTimelinePanel from './IterTimelinePanel.vue'
import ArtifactPanel from './ArtifactPanel.vue'
import type { RunView } from '@/lib/runView'

interface IterRow {
  seq: number
  phase: string | null
  signal_kind?: string | null
  signal_args?: Record<string, unknown> | null
  exit_reason?: string | null
}

interface RunDetail {
  id: string
  status: string
  started_at: string | null
  ended_at: string | null
  max_iters: number
  prompt_id: number | null
  prompt_body: string
  parent_run_id: string | null
  iters: ReadonlyArray<IterRow>
}

const props = defineProps<{
  detail: RunDetail
  selection: RunView
  events: ReadonlyArray<unknown>
  pendingTurns: ReadonlyArray<unknown>
  lastHeartbeat: number | null
  childCount: number
  cancelLabel: string
  cancelling: boolean
  pauseQuestion: string
  pauseReviewPaths: ReadonlyArray<string>
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'resumed'): void
}>()

const iterCount = computed(() => props.detail.iters.length)
const currentPhase = computed(() => {
  const last = props.detail.iters[props.detail.iters.length - 1]
  return last?.phase ?? '—'
})

const isCancellable = computed(
  () =>
    props.detail.status === 'running' ||
    props.detail.status === 'awaiting_children',
)

const isPaused = computed(() => props.detail.status === 'paused')

function formatStarted(iso: string | null): string {
  if (iso == null || iso === '') return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  return new Date(t).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

const FAILURE_STATUSES = new Set(['failed', 'cancelled'])
const failureInfo = computed<{
  reason: string
  marker_error: string | null
  hint: string | null
} | null>(() => {
  if (!FAILURE_STATUSES.has(props.detail.status)) return null
  const last = props.detail.iters[props.detail.iters.length - 1] ?? null
  const reason = last?.exit_reason ?? props.detail.status
  const args = last?.signal_args ?? null
  const markerError =
    args != null && typeof args.marker_error === 'string'
      ? args.marker_error
      : null
  let hint: string | null = null
  if (reason === 'agent_end_no_signal' && markerError == null) {
    hint =
      'The agent finished its turn without emitting a closing sentinel ' +
      '([[engteam:done]], [[engteam:handoff]], or [[engteam:pause-for-input]]). ' +
      'Relay bundles the engineering-team skill and injects it into every ' +
      'pi spawn automatically (no per-project install needed). Start the ' +
      'prompt with `/engineering-team …` to trigger it; if the skill is ' +
      'already loaded but you got this error, the agent may have aborted ' +
      'early (token budget, transient API failure) — check the timeline ' +
      'for the last tool result.'
  } else if (reason === 'timeout') {
    hint =
      'The iter exceeded its wall-clock budget (iter_timeout). The next ' +
      'iter would have started fresh; raise iter_timeout if the work ' +
      'legitimately needs longer.'
  } else if (reason === 'max_iters') {
    hint =
      'The run hit its max_iters cap before emitting a `done` sentinel. ' +
      'Raise max_iters or break the work into smaller handoffs.'
  } else if (reason === 'internal_error') {
    hint =
      'The orchestrator caught an unexpected exception while driving the ' +
      'loop. Check the server log for the stack trace.'
  }
  return { reason, marker_error: markerError, hint }
})

function onCancel(): void {
  emit('cancel')
}

function onResumed(): void {
  emit('resumed')
}
</script>

<template>
  <section class="right-pane" data-testid="run-right-pane">
    <header class="right-pane__header">
      <div class="right-pane__title-row">
        <h1 class="right-pane__title">Run {{ detail.id }}</h1>
        <StatusBadge :status="detail.status" />
        <RunHealthBadge
          :status="detail.status"
          :last-heartbeat="lastHeartbeat"
        />
        <ParentRunChip :parent-run-id="detail.parent_run_id" />
      </div>

      <dl class="right-pane__meta">
        <div>
          <dt>Prompt</dt>
          <dd>{{ detail.prompt_id != null ? `#${detail.prompt_id}` : 'inline' }}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd :title="detail.started_at ?? ''" data-testid="run-started-at">
            {{ formatStarted(detail.started_at) }}
          </dd>
        </div>
        <div>
          <dt>Iters</dt>
          <dd>{{ iterCount }} / {{ detail.max_iters }}</dd>
        </div>
        <div>
          <dt>Phase</dt>
          <dd>{{ currentPhase }}</dd>
        </div>
      </dl>

      <div v-if="isCancellable" class="right-pane__actions">
        <ActionButton
          :loading="cancelling"
          data-testid="cancel-run"
          @click="onCancel"
        >
          {{ cancelLabel }}
        </ActionButton>
      </div>

      <aside
        v-if="failureInfo"
        data-testid="run-failure-banner"
        class="right-pane__failure"
        :data-reason="failureInfo.reason"
      >
        <strong class="right-pane__failure-title">
          Run {{ detail.status }} — {{ failureInfo.reason }}
        </strong>
        <p
          v-if="failureInfo.marker_error"
          class="right-pane__failure-marker"
        >
          <span class="right-pane__failure-label">Marker error:</span>
          <code>{{ failureInfo.marker_error }}</code>
        </p>
        <p
          v-if="failureInfo.hint"
          class="right-pane__failure-hint"
        >
          {{ failureInfo.hint }}
        </p>
      </aside>
    </header>

    <PauseAnswerForm
      v-if="isPaused"
      :run-id="detail.id"
      :question="pauseQuestion"
      :review-paths="pauseReviewPaths as string[]"
      @resumed="onResumed"
    />

    <div class="right-pane__body">
      <OverviewPanel
        v-if="selection.kind === 'overview'"
        :run-id="detail.id"
        :prompt-body="detail.prompt_body"
        :events="events"
        :pending-turns="pendingTurns"
      />
      <IterTimelinePanel
        v-else-if="selection.kind === 'iter'"
        :run-id="detail.id"
        :iter-seq="selection.seq"
        :iters="detail.iters"
        :events="events"
        :pending-turns="pendingTurns"
      />
      <ArtifactPanel
        v-else-if="selection.kind === 'artifact'"
        :run-id="detail.id"
        :path="selection.path"
      />
    </div>
  </section>
</template>

<style scoped>
.right-pane {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem 1.25rem;
  min-width: 0;
}

.right-pane__header {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.75rem;
}

.right-pane__title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.right-pane__title {
  margin: 0;
  font-size: 1.3rem;
  font-family: var(--font-mono);
}

.right-pane__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}

.right-pane__meta dt {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.right-pane__meta dd {
  margin: 0.15rem 0 0;
  font-weight: 600;
}

.right-pane__actions {
  display: flex;
  gap: 0.5rem;
}

.right-pane__failure {
  border: 1px solid #d04a4a;
  border-left: 4px solid #d04a4a;
  background: rgba(208, 74, 74, 0.08);
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.right-pane__failure-title {
  font-family: var(--font-mono);
  color: #b03a3a;
}

.right-pane__failure-marker,
.right-pane__failure-hint {
  margin: 0;
  font-size: 0.9em;
  line-height: 1.4;
}

.right-pane__failure-label {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
  margin-right: 0.4rem;
}

.right-pane__body {
  flex: 1;
  min-height: 0;
}
</style>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/RunRightPane.spec.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/layout/RunRightPane.vue \
        frontend/tests/RunRightPane.spec.ts
git commit -m "$(cat <<'EOF'
feat(frontend): RunRightPane — header + selection-routed body

Run-scoped header (status/badges/meta/cancel/failure) + pause form
(when paused) + body that routes on selection.kind to one of
OverviewPanel / IterTimelinePanel / ArtifactPanel. PauseAnswerForm
stays inline above the body for Phase 1; promoted to a sticky
PauseBanner in Phase 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Refactor `RunDetailView` to two-column layout

**Files:**
- Modify: `frontend/src/views/RunDetailView.vue`

This is the biggest task. The view becomes a thin layout orchestrator:
parses `?view=`, computes smart-default when the URL view is null,
threads run state into `RunSidebar` + `RunRightPane`, and pushes
selection back to the URL when the sidebar emits `update:view`.

Critically: all the existing SSE / lifecycle / cancel / resume logic
moves *up* unchanged. Only the template changes shape.

- [ ] **Step 1: Replace the imports block**

Replace lines 27–48 (the existing imports + `defineProps`) with:

```typescript
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import RunSidebar from '@/components/runs/layout/RunSidebar.vue'
import RunRightPane from '@/components/runs/layout/RunRightPane.vue'
import {
  useRunDetailQuery,
  useCancelRunMutation,
  useInvalidate,
  useRunChildrenQuery,
  asAsyncState,
  type RunDetail,
} from '@/lib/queries'
import { useEventsStore } from '@/stores/events'
import { useCurrentRunStore } from '@/stores/currentRun'
import {
  parseView,
  serializeView,
  smartDefault,
  type RunView,
} from '@/lib/runView'

const props = defineProps<{ id: string }>()
```

Delete the now-unused component imports (`StatusBadge`,
`RunHealthBadge`, `ActionButton`, `TimelinePane`, `ItersPane`,
`PauseAnswerForm`, `ArtifactsPane`, `WorktreePane`, `ChildrenPane`,
`ParentRunChip`) — they're imported by the new layout components, not
the view.

⚠ Keep `formatStarted` deleted (moved into `RunRightPane`).

- [ ] **Step 2: Add URL ↔ selection binding**

After the `cancelRun = useCancelRunMutation()` line, add:

```typescript
const route = useRoute()
const router = useRouter()

/**
 * URL-derived view selection. Returns null while the URL has no
 * ?view=; falls back to {@link smartDefault} below once detail lands.
 *
 * Source of truth = URL. We never store selection in component state;
 * mutations always push through the router.
 */
const urlView = computed<RunView | null>(() => parseView(route.query))

/**
 * The effective view threaded into the layout components. Resolves
 * the smart-default in one place so RunSidebar / RunRightPane don't
 * need detail to choose a default.
 */
const currentView = computed<RunView>(() => {
  if (urlView.value != null) return urlView.value
  const d = detail.value
  if (d == null) return { kind: 'overview' }
  return smartDefault({ status: d.status, iters: d.iters })
})

/**
 * One-shot bootstrap: when detail first lands and the URL has no
 * view=, hydrate the URL with the smart-default. This makes the
 * default shareable / refreshable. Subsequent navigation uses the
 * push from {@link onSelectView}.
 */
let viewBootstrapped = false
watch(
  detail,
  (d) => {
    if (d == null || viewBootstrapped) return
    if (urlView.value != null) {
      viewBootstrapped = true
      return
    }
    viewBootstrapped = true
    const v = smartDefault({ status: d.status, iters: d.iters })
    void router.replace({
      query: { ...route.query, view: serializeView(v) },
    })
  },
  { immediate: true },
)

function onSelectView(view: RunView): void {
  void router.push({
    query: { ...route.query, view: serializeView(view) },
  })
}
```

- [ ] **Step 3: Compute pause + signal_args derivations (kept here)**

Keep the existing `pauseQuestion` and `pauseReviewPaths` computeds —
they live in `RunDetailView` and are passed down to `RunRightPane`
which forwards them to `PauseAnswerForm`. NO change to their bodies.

Delete `latestActivity`, `showPrompt`, and the
`run-detail__activity` markup — they belong to the old vertical-stack
template. (`OverviewPanel` renders the prompt in its own box.)

- [ ] **Step 4: Replace the entire `<template>`**

Replace lines 319–480 with:

```vue
<template>
  <section class="run-detail">
    <AsyncBoundary :loading="isLoading" :error="error">
      <template v-if="detail">
        <div class="run-detail__layout">
          <RunSidebar
            :run-id="detail.id"
            :selection="currentView"
            :iters="detail.iters"
            :children="children"
            @update:view="onSelectView"
          />
          <RunRightPane
            :detail="detail"
            :selection="currentView"
            :events="eventList"
            :pending-turns="pendingTurns"
            :last-heartbeat="lastHeartbeat"
            :child-count="childCount"
            :cancel-label="cancelLabel"
            :cancelling="cancelling"
            :pause-question="pauseQuestion"
            :pause-review-paths="pauseReviewPaths"
            @cancel="onCancel"
            @resumed="onResumed"
          />
        </div>
      </template>
    </AsyncBoundary>
  </section>
</template>
```

- [ ] **Step 5: Replace `<style scoped>`**

Replace the entire `<style scoped>` block (lines 482–632) with:

```css
.run-detail {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.run-detail__layout {
  display: grid;
  grid-template-columns: minmax(220px, 280px) 1fr;
  min-height: 100%;
  align-items: stretch;
}

@media (max-width: 899px) {
  /* Stacking under 900px is Phase 6 work — for Phase 1, simply allow
     the rail to fall below the right pane in narrow viewports. The
     visual result is acceptable for the localhost dev use case. */
  .run-detail__layout {
    grid-template-columns: 1fr;
  }
}
</style>
```

- [ ] **Step 6: Run all frontend tests**

Run: `cd frontend && npm run check`

Expected: `tests/RunDetailView.spec.ts` will FAIL — it references
selectors and structure that no longer exist. That's Task 7's work.
All OTHER tests should PASS.

If anything outside `RunDetailView.spec.ts` fails, stop and diagnose
before continuing.

- [ ] **Step 7: Commit (intermediate — tests not yet green)**

Use a draft-style commit message that signals the failing spec:

```bash
git add frontend/src/views/RunDetailView.vue
git commit -m "$(cat <<'EOF'
refactor(frontend): RunDetailView — two-column layout shell

Gut the vertical-stack template; wire RunSidebar + RunRightPane via
URL-reflected view selection (?view=overview|iter:N|artifact:<path>).
Smart-default hydrates the URL on first detail arrival.

Existing SSE / lifecycle / cancel / resume / pause-review-paths
plumbing moves up unchanged. Cancel button + meta header move into
RunRightPane.

KNOWN FAILING: tests/RunDetailView.spec.ts — updated in the next
commit (Task 7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Update `RunDetailView.spec.ts` for the new layout

**Files:**
- Modify: `frontend/tests/RunDetailView.spec.ts`

The existing spec is ~17k of assertions written against the old
vertical-stack DOM. Most assertions can be ported one-to-one by
looking up the same `data-testid` in its new pane home:

| Old location                                | New location                              |
|---------------------------------------------|-------------------------------------------|
| `[data-testid="cancel-run"]` in view header | `[data-testid="cancel-run"]` in `RunRightPane` |
| `[data-testid="run-started-at"]`            | inside `[data-testid="run-right-pane"]`   |
| `[data-testid="run-failure-banner"]`        | inside `RunRightPane` header              |
| `[data-testid="iters-pane-slot"]`           | DELETED — iters now live in `RunSidebar`  |
| `[data-testid="artifacts-pane-slot"]`       | DELETED — artifacts in rail + `ArtifactPanel` |
| `[data-testid="latest-activity"]`           | DELETED — superseded by `OverviewPanel` prompt + timeline |
| `[data-testid="rendered-event-count"]`      | DELETED — moved off the header for Phase 1 (re-introduce in Phase 7 polish if needed) |

⚠ `latestActivity` (the "agent" activity peek) is **deleted** in
Phase 1, NOT moved. The Overview panel's live timeline supersedes it.
If a follow-up needs this affordance back, it lives in Phase 7
polish.

⚠ Likewise `rendered-event-count` is dropped from the visible header
in Phase 1 (the count was always more of a debug affordance). If a
spec test depended on it, drop the assertion.

- [ ] **Step 1: Run the failing spec to see what breaks**

```bash
cd frontend && npx vitest run tests/RunDetailView.spec.ts 2>&1 | tee /tmp/run-detail-spec-fails.txt
```

Open `/tmp/run-detail-spec-fails.txt`. For each failed test, decide
which bucket it falls into:

1. **Port** — change selector to new location (most tests).
2. **Drop** — assertion was on UI removed in Phase 1.
3. **New** — selection/URL behaviour now tested below.

- [ ] **Step 2: Port each failed assertion**

For each test, replace selectors as follows:

```typescript
// OLD
w.get('h1.run-detail__title')
// NEW
w.find('[data-testid="run-right-pane"]').get('.right-pane__title')
```

```typescript
// OLD
w.find('[data-testid="iters-pane-slot"]')
// NEW (testing iter rows live in the sidebar)
w.find('[data-testid="run-sidebar"]').findAll('[data-testid^="sidebar-iter-"]')
```

```typescript
// OLD — checking a button was rendered above the timeline
w.find('[data-testid="cancel-run"]')
// NEW — same testid, now inside the right pane
w.find('[data-testid="run-right-pane"]').find('[data-testid="cancel-run"]')
// (Plain `[data-testid="cancel-run"]` also still works because the
// id is unique on the page; the scoped find documents intent.)
```

Drop assertions on:
- `latestActivity` / `[data-testid="latest-activity"]`
- `rendered-event-count`
- The `<details>` prompt disclosure (`showPrompt`) — the Overview
  panel renders the prompt directly, no disclosure.

- [ ] **Step 3: Add new tests for URL ↔ selection round-trip**

Append to `tests/RunDetailView.spec.ts`:

```typescript
import { createRouter, createMemoryHistory } from 'vue-router'

describe('RunDetailView — URL ↔ view binding', () => {
  it('hydrates ?view= with smart-default on first detail (running with iters)', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
      ],
    })
    await router.push('/runs/run-1')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(
          ok(
            makeDetail({
              status: 'running',
              iters: [
                { seq: 1, phase: 'planning', signal_kind: null, signal_args: null },
                { seq: 2, phase: 'planning', signal_kind: null, signal_args: null },
              ],
            }),
          ),
        )
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: { plugins: [createPinia(), PiniaColada, router] },
    })
    await flushPromises()

    expect(router.currentRoute.value.query.view).toBe('iter:2')
  })

  it('respects an existing ?view= from the URL', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
      ],
    })
    await router.push('/runs/run-1?view=overview')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(ok(makeDetail({ status: 'running' })))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: { plugins: [createPinia(), PiniaColada, router] },
    })
    await flushPromises()

    expect(router.currentRoute.value.query.view).toBe('overview')
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(true)
  })

  it('pushes ?view=iter:N when a sidebar iter row is clicked', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
      ],
    })
    await router.push('/runs/run-1?view=overview')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(
          ok(
            makeDetail({
              status: 'running',
              iters: [{ seq: 1, phase: 'planning', signal_kind: null, signal_args: null }],
            }),
          ),
        )
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: { plugins: [createPinia(), PiniaColada, router] },
    })
    await flushPromises()

    await w.get('[data-testid="sidebar-iter-1"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.view).toBe('iter:1')
  })
})
```

⚠ Existing tests in `RunDetailView.spec.ts` use a router built once at
module top-level. The new tests build their own per-test router so
`router.push` doesn't pollute siblings. Keep both patterns.

- [ ] **Step 4: Run the spec and iterate until green**

```bash
cd frontend && npx vitest run tests/RunDetailView.spec.ts
```

Expected: all PASS. If new failures appear, port the assertion per
Step 2 or drop it per the dropped-affordance list.

- [ ] **Step 5: Run the full frontend gate**

```bash
cd frontend && npm run check
```

Expected: PASS (eslint --max-warnings 0, vue-tsc, vitest). If
`vue-tsc` complains about the `OverviewPanel` type imports, apply the
fallback noted in Task 4 Step 1.

- [ ] **Step 6: Commit**

```bash
git add frontend/tests/RunDetailView.spec.ts
git commit -m "$(cat <<'EOF'
test(frontend): RunDetailView — port spec to new layout

Reroute selectors to find Cancel / status / iters / artifacts in
their new pane homes. Drop assertions for affordances removed in
Phase 1 (latestActivity peek, rendered-event-count header chip,
prompt-disclosure summary). Add new tests covering URL ↔ view
binding and smart-default hydration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Delete dead code — `ArtifactsPane` and `WorktreePane` from the view

**Files:**
- Delete or modify (see below): `frontend/src/components/runs/ArtifactsPane.vue`
- Modify: `frontend/src/components/runs/WorktreePane.vue` (or its consumers)
- Modify: `frontend/tests/ArtifactsPane.spec.ts`, `frontend/tests/WorktreePane.spec.ts`

`ArtifactsPane` is no longer mounted — its data sources are now used
directly by `RunSidebar` (FileTree) and `ArtifactPanel` (FileViewer).
Decide between two paths:

**Option A — delete the pane entirely (preferred).**

`ArtifactsPane.vue`'s only behaviour was composing FileTree + FileViewer
side-by-side with a "no artifacts yet" empty-state. Both pieces now
live in the sidebar / right pane. The empty-state collapse rule moves
into `RunSidebar` (Task 3 already handles this) and `ArtifactPanel`
inherits FileViewer's own "no path selected" empty state.

Delete:
- `frontend/src/components/runs/ArtifactsPane.vue`
- `frontend/tests/ArtifactsPane.spec.ts`

**Option B — keep the pane but stop mounting it.**

If your `simplify` skill or a reviewer flags an "in case we need it
later" risk, Option B leaves the file untouched and just removes the
`RunDetailView` import + render. Less code-churn but more cruft.

Recommendation: **Option A.** The data sources (`runArtifactSource`,
`useBrowserUiStore`) are the load-bearing pieces; the pane was a
thin presentation wrapper.

For `WorktreePane`: it was rendered as a placeholder in the old view.
Phase 1 drops it from the layout. We do NOT delete it — Phase 5's
tool-call drawer (or a future iter-detail expansion) is a candidate
re-mount. Leave the file + spec intact.

- [ ] **Step 1: Delete `ArtifactsPane`**

```bash
git rm frontend/src/components/runs/ArtifactsPane.vue \
       frontend/tests/ArtifactsPane.spec.ts
```

- [ ] **Step 2: Re-run the full gate**

```bash
cd frontend && npm run check
```

Expected: PASS. Any reference to `ArtifactsPane` left behind in the
codebase is an import error — fix the dangling import.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(frontend): delete ArtifactsPane (superseded by layout shell)

RunSidebar now wraps FileTree directly via runArtifactSource;
ArtifactPanel hosts FileViewer. The composite pane is no longer
mounted anywhere and adds maintenance cost. Data sources unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Manual smoke + commit pointer to spec

**Files:**
- No code changes. Adds a journal entry.
- Create: `journal/260528-run-detail-layout-shell.md`

- [ ] **Step 1: Run the full backend + frontend gate**

```bash
uv run ruff check . && \
  uv run mypy && \
  uv run pytest -q && \
  cd frontend && npm run check && cd ..
```

Expected: all green.

- [ ] **Step 2: Manual UI smoke**

Verify in a browser via the `verify` skill (or simply
`relay serve` + open a run):

1. Open a running run → URL gains `?view=iter:N` (smart-default).
2. Click "Overview" in the rail → URL becomes `?view=overview`; the
   right pane shows the prompt + cross-iter timeline.
3. Click a different iter → URL becomes `?view=iter:M`; the right
   pane shows that iter's timeline.
4. Click an artifact in the rail → URL becomes `?view=artifact:<path>`;
   the right pane shows the file contents.
5. Refresh the browser at each URL → the same view re-hydrates.
6. Open a paused run → the existing `PauseAnswerForm` renders inline
   above the routed body. Resume works.
7. Open a failed run → failure banner renders inside the right pane
   header with the `agent_end_no_signal` hint when applicable.
8. Open a parent run with children → CHILDREN section renders in the
   rail; clicking a child navigates to that run's detail page.

If any step fails, fix forward; do not paper over with a journal
entry that says "mostly working."

- [ ] **Step 3: Write the journal entry**

```markdown
# 260528 — Run-detail layout shell (Phase 1)

Landed the two-column master-detail shell for `RunDetailView`
(spec: docs/proposals/run-detail-layout.md; plan: this file).

What changed visibly:
- Left rail (Overview / Iters / Artifacts / Children) lives on every
  run-detail page.
- Right pane renders one of OverviewPanel / IterTimelinePanel /
  ArtifactPanel based on selection.
- URL reflects selection (`?view=overview|iter:N|artifact:<path>`);
  refresh preserves view; smart-default hydrates the URL when absent.

What did NOT change:
- Backend, REST, SSE, OTel, sentinel grammar, schema.
- TimelinePane, PauseAnswerForm, ChildrenPane, WorktreePane,
  FileTree, FileViewer internals.
- Events store dual-list contract (no new event kind in Phase 1).

Deferred to later phases:
- Filter chips + color-coded event kinds (Phase 2).
- Follow-live pin (Phase 3).
- Sticky pause banner (Phase 4).
- Tool-call detail drawer (Phase 5).
- Responsive collapse below 900px (Phase 6 — current behaviour:
  rail falls under the right pane).
- Keyboard nav, ARIA polish, empty-state copy (Phase 3 + Phase 7).

MVP-acceptance-testing exception authorised by the user; this is
the smallest shippable slice that fixes the structural problem
acceptance testing surfaced.
```

- [ ] **Step 4: Commit**

```bash
git add journal/260528-run-detail-layout-shell.md
git commit -m "$(cat <<'EOF'
docs(journal): 260528 — run-detail layout shell (Phase 1)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

Running the writing-plans self-review against the spec.

**Spec coverage.** Walking each section of
`docs/proposals/run-detail-layout.md`:

- Layout / nav bar / two columns — Task 6 (`RunDetailView`
  template + grid styles).
- Left rail (Overview / Iters / Artifacts / Children) — Tasks 2, 3.
- Right pane: run header + pause banner placement + body routing —
  Task 5.
- URL contract (`?view=`) — Task 1 (helpers) + Task 6 (binding).
  `?kinds=` is explicitly Phase 2, called out.
- Smart-default selection — Task 1 (`smartDefault`) + Task 6
  (hydration). Paused branch is Phase 4, called out.
- Filter chips — **Phase 2.** Out of scope for this plan.
- Follow-live behaviour — **Phase 3.** Out of scope.
- Keyboard navigation — **Phase 3.** Out of scope.
- Empty states — partially covered (Task 3 hides Artifacts when
  empty; Task 2 hides Children when empty). The polish pass is
  Phase 7.
- Accessibility — partial (Task 2 sets `role="listbox"` +
  `aria-selected`). Full ARIA / focus order / contrast spot-check
  is Phase 7.
- Component tree delta — Tasks 2, 3, 4, 5, 6 create the new files;
  Task 8 deletes `ArtifactsPane`.
- State management — Task 6 (URL is source of truth; Follow-live
  pin is Phase 3).
- "What does NOT change" — preserved by carrying SSE / lifecycle /
  cancel / resume / pauseReviewPaths through Task 6.

No coverage gaps for Phase 1's scope. Phases 2–7 surface explicitly.

**Placeholder scan.** No "TBD" / "implement later" / "similar to" /
"add appropriate handling" tokens in any step. Every step shows the
code or the exact command.

**Type consistency.**
- `RunView` is the same discriminated union across `runView.ts`,
  `RunSidebar`, `RunRightPane`, `RunDetailView`.
- `parseView` / `serializeView` / `smartDefault` signatures match
  the call sites in Task 6.
- `update:view` emit shape is `{ kind: 'overview' | 'iter' | 'artifact', … }`
  consistently in Tasks 2, 3, 6.
- The `as never[]` cast inside `IterTimelinePanel` is acknowledged
  as a Phase 1 shim (Task 4 Step 2 note).

No inconsistencies.

---

## Execution handoff

Plan complete and saved to
`docs/plans/2026-05-28-run-detail-layout-shell.md`. Two execution
options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent
per task, review between tasks, fast iteration. Best fit because the
plan has 9 fairly large tasks; isolating each one keeps the main
context clean.

**2. Inline Execution** — Execute tasks in this session using
`executing-plans`, batch execution with checkpoints. Better if you
want to watch the work happen and intervene mid-task.

Which approach?
