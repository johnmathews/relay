import { describe, it, expect, vi } from 'vitest'
import { mount, RouterLinkStub, flushPromises } from '@vue/test-utils'
import { ref, computed } from 'vue'
import type { Project, Run } from '../src/lib/queries'

// Control the projects list + per-project latest-run query via mocks so
// no backend is needed. `useRunsQuery` is keyed by projectId so we
// return the latest run for the requested project.
const projectsData = ref<Project[]>([])
const runsByProject = new Map<number, Run[]>()

function mkRun(status: string): Run {
  return { status } as unknown as Run
}

vi.mock('@/lib/queries', () => ({
  useProjectsQuery: () => ({ data: projectsData }),
  asAsyncState: () => ({
    isLoading: computed(() => false),
    error: computed(() => null),
  }),
  useRunsQuery: (filters: () => { projectId: number }) => ({
    data: computed(() => runsByProject.get(filters().projectId) ?? []),
  }),
}))

import ProjectList from '../src/components/projects/ProjectList.vue'

function mountList(): ReturnType<typeof mount> {
  return mount(ProjectList, {
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

describe('ProjectList', () => {
  it('shows the empty state when there are no projects', async () => {
    projectsData.value = []
    const w = mountList()
    await flushPromises()
    expect(w.text()).toContain('No projects registered yet')
  })

  it('renders one card per project with the latest-run StatusBadge', async () => {
    projectsData.value = [
      { id: 1, name: 'Alpha', root_path: '/a' } as unknown as Project,
      { id: 2, name: 'Beta', root_path: '/b' } as unknown as Project,
    ]
    runsByProject.set(1, [mkRun('running')])
    runsByProject.set(2, [mkRun('failed')])

    const w = mountList()
    await flushPromises()

    const badges = w.findAll('.status-badge')
    expect(badges).toHaveLength(2)
    expect(badges[0].text()).toBe('running')
    expect(badges[0].classes()).toContain('status-badge--running')
    expect(badges[1].text()).toBe('failed')
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('/a')
  })

  it('renders the "no runs yet" case when a project has no runs', async () => {
    projectsData.value = [
      { id: 3, name: 'Gamma', root_path: '/g' } as unknown as Project,
    ]
    runsByProject.clear()

    const w = mountList()
    await flushPromises()

    expect(w.find('.status-badge').exists()).toBe(false)
    expect(w.text()).toContain('no runs yet')
  })

  it('the whole card is a link to the project view (no per-card New run)', async () => {
    projectsData.value = [
      { id: 9, name: 'Delta', root_path: '/d' } as unknown as Project,
    ]
    runsByProject.clear()

    const w = mountList()
    await flushPromises()

    const links = w.findAllComponents(RouterLinkStub)
    const targets = links.map((l) => l.props('to'))
    expect(targets).toContainEqual({ name: 'project', params: { id: 9 } })
    // The wizard is only reachable from the project view — the hub does
    // not offer a per-card "New run" shortcut.
    expect(targets).not.toContainEqual({ name: 'new-run', params: { id: 9 } })
    expect(targets).toHaveLength(1)
  })
})
