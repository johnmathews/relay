import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import ParentRunChip from '../src/components/shared/ParentRunChip.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
  ],
})

describe('ParentRunChip', () => {
  it('renders nothing when parentRunId is null', () => {
    const wrapper = mount(ParentRunChip, {
      props: { parentRunId: null },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="parent-run-chip"]').exists()).toBe(false)
  })

  it('renders a router-link to /runs/<parentRunId> when set', async () => {
    const wrapper = mount(ParentRunChip, {
      props: { parentRunId: 'parent-abc' },
      global: { plugins: [router] },
    })
    const link = wrapper.find('[data-testid="parent-run-chip"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/runs/parent-abc')
    expect(link.text()).toContain('parent-abc'.slice(0, 8))
  })
})
