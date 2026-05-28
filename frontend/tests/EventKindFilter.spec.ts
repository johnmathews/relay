import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import EventKindFilter from '../src/components/runs/EventKindFilter.vue'
import {
  KIND_CATEGORIES,
  type KindCategory,
} from '../src/lib/eventKinds'

function fullCounts(
  over: Partial<Record<KindCategory, number>> = {},
): Record<KindCategory, number> {
  return {
    assistant: 0,
    thinking: 0,
    tool: 0,
    signal: 0,
    other: 0,
    ...over,
  }
}

describe('EventKindFilter — chip row', () => {
  it('renders one chip per category in canonical order', () => {
    const w = mount(EventKindFilter, {
      props: { modelValue: null, counts: fullCounts() },
    })
    const chips = w.findAll('[data-testid^="kind-chip-"]')
    expect(chips).toHaveLength(KIND_CATEGORIES.length)
    expect(chips.map((c) => c.attributes('data-kind'))).toEqual(
      [...KIND_CATEGORIES],
    )
  })

  it('shows per-category counts from props', () => {
    const w = mount(EventKindFilter, {
      props: {
        modelValue: null,
        counts: fullCounts({ tool: 3, assistant: 7 }),
      },
    })
    expect(w.get('[data-testid="kind-count-tool"]').text()).toBe('3')
    expect(w.get('[data-testid="kind-count-assistant"]').text()).toBe('7')
    expect(w.get('[data-testid="kind-count-signal"]').text()).toBe('0')
  })

  it('renders all chips as on when modelValue is null', () => {
    const w = mount(EventKindFilter, {
      props: { modelValue: null, counts: fullCounts() },
    })
    for (const k of KIND_CATEGORIES) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('true')
      expect(chip.classes()).not.toContain('is-off')
    }
  })

  it('first chip click emits a 4-element subset (the unclicked categories)', async () => {
    const w = mount(EventKindFilter, {
      props: { modelValue: null, counts: fullCounts() },
    })
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const payload = emitted![0]![0] as ReadonlySet<KindCategory> | null
    expect(payload).not.toBeNull()
    expect(payload!.has('tool')).toBe(false)
    expect(payload!.size).toBe(KIND_CATEGORIES.length - 1)
  })

  it('clicking the last hidden chip on emits null (URL drops the param)', async () => {
    const onlyTool = new Set<KindCategory>(['tool'])
    const w = mount(EventKindFilter, {
      props: {
        modelValue: new Set<KindCategory>(
          KIND_CATEGORIES.filter((k) => !onlyTool.has(k)),
        ),
        counts: fullCounts(),
      },
    })
    // Click `tool` (currently off) → set becomes complete → null emit.
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted![0]![0]).toBeNull()
  })

  it('renders off-state visually + via aria-pressed when a chip is hidden', () => {
    const w = mount(EventKindFilter, {
      props: {
        modelValue: new Set<KindCategory>(['assistant', 'thinking', 'signal', 'other']),
        counts: fullCounts(),
      },
    })
    const tool = w.get('[data-testid="kind-chip-tool"]')
    expect(tool.attributes('aria-pressed')).toBe('false')
    expect(tool.classes()).toContain('is-off')
  })

  it('renders a Clear button only when a filter is active', async () => {
    const w = mount(EventKindFilter, {
      props: { modelValue: null, counts: fullCounts() },
    })
    expect(w.find('[data-testid="kind-filter-clear"]').exists()).toBe(false)

    await w.setProps({
      modelValue: new Set<KindCategory>(['tool']),
      counts: fullCounts(),
    })
    expect(w.find('[data-testid="kind-filter-clear"]').exists()).toBe(true)
    await w.get('[data-testid="kind-filter-clear"]').trigger('click')
    const emitted = w.emitted('update:modelValue')
    expect(emitted![emitted!.length - 1]![0]).toBeNull()
  })

  it('exposes role="toolbar" for assistive tech', () => {
    const w = mount(EventKindFilter, {
      props: { modelValue: null, counts: fullCounts() },
    })
    expect(w.get('[data-testid="event-kind-filter"]').attributes('role')).toBe(
      'toolbar',
    )
  })
})
