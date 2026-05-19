import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Prompt } from '../src/lib/queries'
import PromptList from '../src/components/prompts/PromptList.vue'

function prompt(over: Partial<Prompt>): Prompt {
  return {
    id: 1,
    project_id: 7,
    name: 'Build it',
    version: 1,
    body: '# x',
    created_at: '2026-05-19T10:00:00Z',
    user_id: 1,
    ...over,
  } as Prompt
}

describe('PromptList', () => {
  it('lists prompts with name + latest version', () => {
    const w = mount(PromptList, {
      props: {
        prompts: [
          prompt({ id: 1, name: 'Build it', version: 3 }),
          prompt({ id: 2, name: 'Refactor', version: 1 }),
        ],
        selectedId: null,
      },
    })
    expect(w.text()).toContain('Build it')
    expect(w.text()).toContain('v3')
    expect(w.text()).toContain('Refactor')
    expect(w.text()).toContain('v1')
  })

  it('emits select with the clicked prompt', async () => {
    const w = mount(PromptList, {
      props: {
        prompts: [prompt({ id: 9, name: 'Build it' })],
        selectedId: null,
      },
    })
    await w.get('[data-testid="prompt-row-9"]').trigger('click')
    const ev = w.emitted('select')
    expect(ev).toBeTruthy()
    expect((ev![0]![0] as Prompt).id).toBe(9)
  })

  it('marks the selected row as pressed', () => {
    const w = mount(PromptList, {
      props: {
        prompts: [prompt({ id: 9 })],
        selectedId: 9,
      },
    })
    expect(
      w.get('[data-testid="prompt-row-9"]').attributes('aria-pressed'),
    ).toBe('true')
  })

  it('has a "New prompt" affordance that emits new', async () => {
    const w = mount(PromptList, {
      props: { prompts: [], selectedId: null },
    })
    const btn = w.find('[data-testid="new-prompt-button"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(w.emitted('new')).toBeTruthy()
  })

  it('shows an empty state with no prompts', () => {
    const w = mount(PromptList, {
      props: { prompts: [], selectedId: null },
    })
    expect(w.text()).toContain('No saved prompts for this project.')
  })
})
