import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, computed } from 'vue'
import type { Prompt } from '../src/lib/queries'

const versionsData = ref<Prompt[]>([])

vi.mock('@/lib/queries', () => ({
  usePromptVersionsQuery: () => ({ data: versionsData }),
  asAsyncState: () => ({
    isLoading: computed(() => false),
    error: computed(() => null),
  }),
}))

import PromptVersions from '../src/components/prompts/PromptVersions.vue'

const MarkdownRenderStub = {
  name: 'MarkdownRender',
  props: ['source'],
  template: '<div class="md-stub">{{ source }}</div>',
}

function v(over: Partial<Prompt>): Prompt {
  return {
    id: 1,
    project_id: 7,
    name: 'P',
    version: 1,
    body: 'body',
    created_at: '2026-05-19T10:00:00Z',
    user_id: 1,
    ...over,
  } as Prompt
}

function mountVersions(): ReturnType<typeof mount> {
  return mount(PromptVersions, {
    props: { promptId: 1 },
    global: { stubs: { MarkdownRender: MarkdownRenderStub } },
  })
}

describe('PromptVersions', () => {
  beforeEach(() => {
    versionsData.value = []
  })

  it('lists versions ascending', async () => {
    versionsData.value = [
      v({ id: 1, version: 1, body: 'first' }),
      v({ id: 2, version: 2, body: 'second' }),
      v({ id: 3, version: 3, body: 'third' }),
    ]
    const w = mountVersions()
    await flushPromises()
    const rows = w.findAll('[data-testid^="version-row-"]')
    expect(rows.map((r) => r.text())).toEqual([
      expect.stringContaining('v1'),
      expect.stringContaining('v2'),
      expect.stringContaining('v3'),
    ])
  })

  it('renders the selected version body read-only and exposes NO edit/delete affordance', async () => {
    versionsData.value = [
      v({ id: 1, version: 1, body: 'old version' }),
      v({ id: 2, version: 2, body: 'new version' }),
    ]
    const w = mountVersions()
    await flushPromises()

    // Default-selects the latest; pick an OLD version explicitly.
    await w.get('[data-testid="version-row-1"]').trigger('click')
    expect(w.find('[data-testid="version-body"] .md-stub').text()).toContain(
      'old version',
    )

    // Read-only: explicit notice + ZERO mutation controls anywhere.
    expect(w.find('[data-testid="versions-readonly"]').exists()).toBe(true)
    expect(w.text().toLowerCase()).toContain('read-only')
    expect(w.find('[data-testid="prompt-edit"]').exists()).toBe(false)
    expect(w.find('[data-testid="prompt-delete"]').exists()).toBe(false)
    expect(w.find('textarea').exists()).toBe(false)
    expect(w.find('input').exists()).toBe(false)
  })

  it('emits close when the close affordance is clicked', async () => {
    versionsData.value = [v({ id: 1, version: 1 })]
    const w = mountVersions()
    await flushPromises()
    await w.get('[data-testid="versions-close"]').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })

  it('shows an empty state with no history', async () => {
    const w = mountVersions()
    await flushPromises()
    expect(w.text()).toContain('No version history.')
  })
})
