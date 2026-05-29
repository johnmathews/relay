import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { PiniaColada } from '@pinia/colada'
import { flushPromises } from '@vue/test-utils'
import RunSidebar from '../src/components/runs/layout/RunSidebar.vue'
import type { RunView } from '../src/lib/runView'

/**
 * Phase 6 — stub `window.matchMedia` for the narrow-viewport tests.
 * jsdom's default matchMedia always returns `matches: false`, so the
 * sidebar's `useViewportBreakpoint` composable always sees "wide". The
 * helper lets a test pretend the viewport is below 899px AND fire a
 * `change` event mid-test so the resize transition can be exercised.
 */
function makeMqlStub(initialMatches: boolean): {
  matches: boolean
  listeners: Array<(e: { matches: boolean }) => void>
  addEventListener: (t: 'change', cb: (e: { matches: boolean }) => void) => void
  removeEventListener: (t: 'change', cb: (e: { matches: boolean }) => void) => void
  fire: (matches: boolean) => void
} {
  return {
    matches: initialMatches,
    listeners: [],
    addEventListener(_t, cb) { this.listeners.push(cb) },
    removeEventListener(_t, cb) {
      const i = this.listeners.indexOf(cb)
      if (i >= 0) this.listeners.splice(i, 1)
    },
    fire(matches) {
      this.matches = matches
      for (const cb of [...this.listeners]) cb({ matches })
    },
  }
}

// Mock the api client so runArtifactSource's listing query is observable.
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    GET: (...a: unknown[]) => GET(...a),
    POST: vi.fn(),
  },
}))

function makeRouter(): ReturnType<typeof createRouter> {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
    ],
  })
}

function mountSidebar(props: {
  selection: RunView
  iters?: Array<{ seq: number; phase: string }>
  children?: Array<{ id: string; status: string }>
  runId?: string
  project?: { id: number; name: string } | null
  status?: string
}): ReturnType<typeof mount> {
  return mount(RunSidebar, {
    props: {
      runId: props.runId ?? 'run-1',
      project: props.project ?? null,
      selection: props.selection,
      status: props.status,
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
    expect(row.attributes('role')).toBe('option')
  })

  it('shows the project name as a title row when project is provided', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      project: { id: 7, name: 'relay-v2' },
    })
    const title = w.get('[data-testid="sidebar-project-title"]')
    expect(title.text()).toContain('relay-v2')
    expect(title.attributes('href')).toBe('/projects/7')
  })

  it('omits the project title row when project is null (pre-hydration)', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="sidebar-project-title"]').exists()).toBe(
      false,
    )
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

  it('marks the selected iter with aria-selected=true and the others with false', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 2 },
      iters: [
        { seq: 1, phase: 'planning' },
        { seq: 2, phase: 'planning' },
      ],
    })
    const sel = w.get('[data-testid="sidebar-iter-2"]')
    expect(sel.attributes('aria-selected')).toBe('true')
    expect(sel.attributes('role')).toBe('option')
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

  it('renders one row per child when children is non-empty, with truncated id and href', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      children: [
        { id: 'child-a', status: 'running' },
        { id: 'child-b', status: 'done' },
      ],
    })
    const rows = w.findAll('[data-testid^="sidebar-child-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('child-a')
    expect(rows[0]!.attributes('href')).toContain('/runs/child-a')
  })

  it('clears aria-selected on Overview when an iter is selected', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 1 },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    expect(
      w.get('[data-testid="sidebar-overview"]').attributes('aria-selected'),
    ).toBe('false')
  })
})

// Phase 7 — proposal §"Accessibility". The Overview + Iters region is
// one listbox of run-views (selection model). FileTree and Children
// stay outside with their own semantics (FileTree owns role=tree;
// children rows are RouterLinks under the rail's outer <nav>).
describe('RunSidebar — listbox ARIA (Phase 7)', () => {
  it('wraps Overview + Iters in role=listbox with aria-orientation=vertical', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    const lb = w.get('[data-testid="sidebar-listbox"]')
    expect(lb.attributes('role')).toBe('listbox')
    expect(lb.attributes('aria-orientation')).toBe('vertical')
    expect(lb.attributes('aria-label')).toBe('Run views')
  })

  it('keeps the Iters group within the listbox (role=group + aria-labelledby)', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    const section = w.get('[data-testid="sidebar-iters-section"]')
    expect(section.attributes('role')).toBe('group')
    expect(section.attributes('aria-labelledby')).toBe('sidebar-iters-heading')
  })
})

// Phase 7 — proposal §"Empty states" table.
describe('RunSidebar — Iters empty state (Phase 7)', () => {
  it('shows "Waiting for first iter…" when a running run has no iters yet', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [],
      status: 'running',
    })
    const empty = w.get('[data-testid="sidebar-iters-waiting"]')
    expect(empty.text()).toContain('Waiting for first iter')
  })

  it('renders the Iters section as visible when waiting (not collapsed)', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [],
      status: 'running',
    })
    expect(w.find('[data-testid="sidebar-iters-section"]').exists()).toBe(true)
  })

  it('hides the Iters section entirely when a terminal run has no iters', () => {
    // A terminal run with zero iters is itself surprising (orphan
    // recovery should have left a run_ended); showing a "waiting"
    // copy on a finished run would be misleading.
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [],
      status: 'done',
    })
    expect(w.find('[data-testid="sidebar-iters-section"]').exists()).toBe(
      false,
    )
    expect(w.find('[data-testid="sidebar-iters-waiting"]').exists()).toBe(
      false,
    )
  })

  it('omits the waiting placeholder once an iter arrives', () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [{ seq: 1, phase: 'planning' }],
      status: 'running',
    })
    expect(w.find('[data-testid="sidebar-iters-waiting"]').exists()).toBe(
      false,
    )
  })
})

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return { data, error: undefined, response: new Response(null, { status: 200 }) }
}

function err(status: number, detail = 'not found'): { data: undefined; error: { detail: string }; response: Response } {
  return {
    data: undefined,
    error: { detail },
    response: new Response(null, { status }),
  }
}

function mountWithColada(props: {
  selection: RunView
  iters?: Array<{ seq: number; phase: string }>
  children?: Array<{ id: string; status: string }>
  runId?: string
  project?: { id: number; name: string } | null
  status?: string
}): ReturnType<typeof mount> {
  return mount(RunSidebar, {
    props: {
      runId: props.runId ?? 'run-1',
      project: props.project ?? null,
      selection: props.selection,
      status: props.status,
      iters: props.iters ?? [],
      children: props.children ?? [],
    },
    global: {
      plugins: [createPinia(), PiniaColada, makeRouter()],
    },
  })
}

describe('RunSidebar — Artifacts section', () => {
  beforeEach(() => { GET.mockReset() })

  it('shows "No artifacts yet" for a running run whose artifacts dir 404s', async () => {
    // Phase 7 — proposal §"Empty states". Previously the section was
    // hidden entirely on 404; the explicit copy is more informative
    // than silence and signals that artifacts will appear when written.
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) return Promise.resolve(err(404))
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({
      selection: { kind: 'overview' },
      status: 'running',
    })
    await flushPromises()
    const section = w.get('[data-testid="sidebar-artifacts-section"]')
    expect(section.find('[data-testid="sidebar-artifacts-empty"]').text()).toBe(
      'No artifacts yet',
    )
  })

  it('renders an em-dash for a terminal run with no artifacts dir', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) return Promise.resolve(err(404))
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({
      selection: { kind: 'overview' },
      status: 'done',
    })
    await flushPromises()
    expect(
      w.get('[data-testid="sidebar-artifacts-empty"]').text(),
    ).toBe('—')
  })

  it('keeps the section hidden on a non-404 error (no inline error surface)', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) return Promise.resolve(err(500))
      return Promise.resolve(ok([]))
    })
    const w = mountWithColada({
      selection: { kind: 'overview' },
      status: 'running',
    })
    await flushPromises()
    expect(
      w.find('[data-testid="sidebar-artifacts-section"]').exists(),
    ).toBe(false)
  })

  it('renders the Artifacts section when listing succeeds', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) {
        return Promise.resolve(
          ok({
            entries: [
              { name: 'evaluation-report.md', kind: 'file', size: 100 },
              { name: 'improvement-plan.md', kind: 'file', size: 200 },
            ],
          }),
        )
      }
      return Promise.resolve(ok({ entries: [] }))
    })
    const w = mountWithColada({ selection: { kind: 'overview' } })
    await flushPromises()
    expect(
      w.find('[data-testid="sidebar-artifacts-section"]').exists(),
    ).toBe(true)
  })

  it('emits update:view with { kind: artifact, path } on file select', async () => {
    GET.mockImplementation((path: string) => {
      if (path.includes('/artifacts')) {
        return Promise.resolve(
          ok({ entries: [{ name: 'plan.md', kind: 'file', size: 100 }] }),
        )
      }
      return Promise.resolve(ok({ entries: [] }))
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

// Phase 6 — proposal §"Layout" (responsive paragraph). Below 900px,
// the rail collapses to a top selector button (selected view's label
// + caret). Tapping reveals the body; selecting auto-collapses.
describe('RunSidebar — narrow-viewport collapse (Phase 6)', () => {
  const originalMM = window.matchMedia
  let mql: ReturnType<typeof makeMqlStub>

  beforeEach(() => {
    mql = makeMqlStub(true)
    window.matchMedia = vi.fn(() => mql) as unknown as typeof window.matchMedia
  })
  afterEach(() => {
    window.matchMedia = originalMM
  })

  it('renders the selector button and hides the body when narrow', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="sidebar-narrow-selector"]').exists()).toBe(
      true,
    )
    const body = w.get('[data-testid="sidebar-body"]')
    // The `hidden` HTML attribute presence is the contract.
    expect(body.attributes('hidden')).toBeDefined()
  })

  it('shows the selected view label on the selector', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 3 },
      iters: [{ seq: 3, phase: 'planning' }],
    })
    expect(
      w.get('[data-testid="sidebar-narrow-selector"]').text(),
    ).toContain('Iter #3')
  })

  it('formats an artifact selection as "Artifact · <path>"', () => {
    const w = mountSidebar({
      selection: { kind: 'artifact', path: 'improvement-plan.md' },
    })
    expect(
      w.get('[data-testid="sidebar-narrow-selector"]').text(),
    ).toContain('Artifact · improvement-plan.md')
  })

  it('reveals the body when the selector is tapped', async () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    const selector = w.get('[data-testid="sidebar-narrow-selector"]')
    expect(selector.attributes('aria-expanded')).toBe('false')
    await selector.trigger('click')
    expect(selector.attributes('aria-expanded')).toBe('true')
    expect(
      w.get('[data-testid="sidebar-body"]').attributes('hidden'),
    ).toBeUndefined()
  })

  it('auto-collapses after selecting an iter (right pane reclaims viewport)', async () => {
    const w = mountSidebar({
      selection: { kind: 'overview' },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    await w.get('[data-testid="sidebar-narrow-selector"]').trigger('click')
    await w.get('[data-testid="sidebar-iter-1"]').trigger('click')
    expect(w.emitted('update:view')).toEqual([[{ kind: 'iter', seq: 1 }]])
    expect(
      w.get('[data-testid="sidebar-narrow-selector"]').attributes(
        'aria-expanded',
      ),
    ).toBe('false')
  })

  it('exposes the standard aria-controls / aria-expanded pair on the selector', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    const selector = w.get('[data-testid="sidebar-narrow-selector"]')
    expect(selector.attributes('aria-controls')).toBe('run-sidebar-body')
    expect(selector.attributes('aria-expanded')).toBe('false')
    expect(w.get('[data-testid="sidebar-body"]').attributes('id')).toBe(
      'run-sidebar-body',
    )
  })

  it('resizing back to wide drops the expanded flag (re-narrowing starts collapsed)', async () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    await w.get('[data-testid="sidebar-narrow-selector"]').trigger('click')
    expect(
      w.get('[data-testid="sidebar-narrow-selector"]').attributes(
        'aria-expanded',
      ),
    ).toBe('true')
    // Cross back into desktop.
    mql.fire(false)
    await w.vm.$nextTick()
    // Selector vanishes in desktop mode.
    expect(w.find('[data-testid="sidebar-narrow-selector"]').exists()).toBe(
      false,
    )
    // Re-narrow → selector visible AND collapsed.
    mql.fire(true)
    await w.vm.$nextTick()
    expect(
      w.get('[data-testid="sidebar-narrow-selector"]').attributes(
        'aria-expanded',
      ),
    ).toBe('false')
  })
})

describe('RunSidebar — wide viewport (Phase 6 default)', () => {
  const originalMM = window.matchMedia

  beforeEach(() => {
    const mql = makeMqlStub(false)
    window.matchMedia = vi.fn(() => mql) as unknown as typeof window.matchMedia
  })
  afterEach(() => {
    window.matchMedia = originalMM
  })

  it('does not render the narrow selector', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="sidebar-narrow-selector"]').exists()).toBe(
      false,
    )
  })

  it('keeps the rail body visible (no hidden attr)', () => {
    const w = mountSidebar({ selection: { kind: 'overview' } })
    expect(
      w.get('[data-testid="sidebar-body"]').attributes('hidden'),
    ).toBeUndefined()
  })
})
