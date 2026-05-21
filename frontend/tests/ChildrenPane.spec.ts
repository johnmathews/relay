import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { ref } from 'vue'
import { useEventsStore } from '../src/stores/events'
import type { Run } from '../src/lib/queries'

vi.mock('../src/lib/queries', async () => {
  const actual = await vi.importActual<object>('../src/lib/queries')
  return {
    ...actual,
    useRunChildrenQuery: vi.fn(),
  }
})

import { useRunChildrenQuery } from '../src/lib/queries'
import ChildrenPane from '../src/components/runs/ChildrenPane.vue'

const mockUseRunChildrenQuery = vi.mocked(useRunChildrenQuery)

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
  ],
})

function makeChildRow(
  overrides: Partial<{
    id: string
    status: string
    branch: string
    parent_run_id: string
  }> = {},
): Run {
  return {
    id: 'child-a',
    project_id: 1,
    prompt_id: null,
    prompt_body: 'x',
    user_id: 0,
    status: 'running',
    started_at: '2026-05-21T00:00:00Z',
    ended_at: null,
    max_iters: 1,
    iter_timeout: 60,
    worktree_path: '/wt/child-a',
    branch: 'relay/child-a',
    parent_run_id: 'parent-1',
    ...overrides,
  } as unknown as Run
}

describe('ChildrenPane', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders nothing when there are no children', () => {
    mockUseRunChildrenQuery.mockReturnValue({ data: ref([]) } as unknown as ReturnType<typeof useRunChildrenQuery>)
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="children-pane"]').exists()).toBe(false)
  })

  it('renders one row per direct child with status badge + link + branch', () => {
    mockUseRunChildrenQuery.mockReturnValue({
      data: ref([
        makeChildRow({ id: 'child-a', status: 'running' }),
        makeChildRow({ id: 'child-b', status: 'done', branch: 'relay/child-b' }),
      ]),
    } as unknown as ReturnType<typeof useRunChildrenQuery>)
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const rows = wrapper.findAll('[data-testid^="children-row-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('child-a'.slice(0, 8))
    expect(rows[0]!.text()).toContain('running')
    expect(rows[1]!.text()).toContain('relay/child-b')
  })

  it("populates role from the events store's subagent_dispatch payload", async () => {
    mockUseRunChildrenQuery.mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a' })]),
    } as unknown as ReturnType<typeof useRunChildrenQuery>)
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const store = useEventsStore()
    store._ingest([
      {
        seq: 1,
        kind: 'subagent_dispatch',
        payload: { child_run_id: 'child-a', role: 'explorer-frontend', prompt: 'x' },
      },
    ])
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('explorer-frontend')
  })

  it("populates summary from the events store's subagent_return payload", async () => {
    mockUseRunChildrenQuery.mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a', status: 'done' })]),
    } as unknown as ReturnType<typeof useRunChildrenQuery>)
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    const store = useEventsStore()
    store._ingest([
      {
        seq: 1,
        kind: 'subagent_return',
        payload: {
          child_run_id: 'child-a',
          status: 'done',
          summary: 'audit complete: 3 findings',
        },
      },
    ])
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('audit complete: 3 findings')
  })

  it('renders an empty summary for a child whose subagent_return is missing (e.g., cascade-cancelled)', () => {
    mockUseRunChildrenQuery.mockReturnValue({
      data: ref([makeChildRow({ id: 'child-a', status: 'cancelled' })]),
    } as unknown as ReturnType<typeof useRunChildrenQuery>)
    const wrapper = mount(ChildrenPane, {
      props: { runId: 'parent-1' },
      global: { plugins: [router] },
    })
    expect(wrapper.text()).toContain('cancelled')
    expect(wrapper.text()).not.toContain('(no summary)')
  })
})
