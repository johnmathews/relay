import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import RunSidebar from '../src/components/runs/layout/RunSidebar.vue'
import type { RunView } from '../src/lib/runView'

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
}): ReturnType<typeof mount> {
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
    expect(row.attributes('aria-current')).toBe('page')
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

  it('marks the selected iter with aria-current=page', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 2 },
      iters: [
        { seq: 1, phase: 'planning' },
        { seq: 2, phase: 'planning' },
      ],
    })
    const sel = w.get('[data-testid="sidebar-iter-2"]')
    expect(sel.attributes('aria-current')).toBe('page')
    const other = w.get('[data-testid="sidebar-iter-1"]')
    expect(other.attributes('aria-current')).toBeUndefined()
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

  it('clears aria-current on Overview when an iter is selected', () => {
    const w = mountSidebar({
      selection: { kind: 'iter', seq: 1 },
      iters: [{ seq: 1, phase: 'planning' }],
    })
    expect(w.get('[data-testid="sidebar-overview"]').attributes('aria-current')).toBeUndefined()
  })
})
