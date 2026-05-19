import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, computed, defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Project } from '../src/lib/queries'

const projectsData = ref<Project[]>([])

vi.mock('@/lib/queries', () => ({
  useProjectsQuery: () => ({ data: projectsData }),
  asAsyncState: () => ({
    isLoading: computed(() => false),
    error: computed(() => null),
  }),
  useRunsQuery: () => ({ data: computed(() => []) }),
}))

// Stub the register form to a trivial marker so HubView's toggle is
// what's under test, not the form internals (covered by their own spec).
vi.mock('@/components/projects/RegisterProjectForm.vue', () => ({
  default: defineComponent({
    name: 'RegisterProjectFormStub',
    setup: () => () => h('div', { class: 'register-form-stub' }, 'form'),
  }),
}))

import HubView from '../src/views/HubView.vue'

function mountHub(): ReturnType<typeof mount> {
  setActivePinia(createPinia())
  return mount(HubView, {
    global: {
      plugins: [createPinia()],
      stubs: { RouterLink: true },
    },
  })
}

describe('HubView', () => {
  it('shows the empty-projects message when there are none', async () => {
    projectsData.value = []
    const w = mountHub()
    await flushPromises()
    expect(w.text()).toContain('No projects registered yet')
  })

  it('renders ProjectList cards when projects exist', async () => {
    projectsData.value = [
      { id: 1, name: 'Alpha', root_path: '/a' } as unknown as Project,
    ]
    const w = mountHub()
    await flushPromises()
    expect(w.text()).toContain('Alpha')
    expect(w.text()).not.toContain('No projects registered yet')
  })

  it('toggles the register form on the affordance click', async () => {
    projectsData.value = []
    const w = mountHub()
    await flushPromises()

    expect(w.find('.register-form-stub').exists()).toBe(false)
    await w.get('.hub__register-toggle').trigger('click')
    expect(w.find('.register-form-stub').exists()).toBe(true)
    await w.get('.hub__register-toggle').trigger('click')
    expect(w.find('.register-form-stub').exists()).toBe(false)
  })
})
