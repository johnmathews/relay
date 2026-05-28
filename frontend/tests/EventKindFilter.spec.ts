import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EventKindFilter from '../src/components/runs/EventKindFilter.vue'
import {
  KIND_CATEGORIES,
  type KindCategory,
} from '../src/lib/eventKinds'
import { useTimelinePrefsStore } from '../src/stores/timelinePrefs'

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

describe('EventKindFilter — chip row drives expand-by-default', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    try {
      localStorage.removeItem('relay.timeline.expanded')
    } catch {
      // ignore
    }
  })

  it('renders one chip per category in canonical order', () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
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
        counts: fullCounts({ tool: 3, assistant: 7 }),
      },
    })
    expect(w.get('[data-testid="kind-count-tool"]').text()).toBe('3')
    expect(w.get('[data-testid="kind-count-assistant"]').text()).toBe('7')
    expect(w.get('[data-testid="kind-count-signal"]').text()).toBe('0')
  })

  it('starts with every chip off (everything collapsed by default)', () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    for (const k of KIND_CATEGORIES) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('false')
      expect(chip.classes()).not.toContain('is-on')
    }
  })

  it('clicking a chip flips that category in the timelinePrefs store', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(prefs.isExpandedByDefault('tool')).toBe(false)
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')
    expect(prefs.isExpandedByDefault('tool')).toBe(true)
    const chip = w.get('[data-testid="kind-chip-tool"]')
    expect(chip.attributes('aria-pressed')).toBe('true')
    expect(chip.classes()).toContain('is-on')
  })

  it('the Other chip bridges to the `generic` row type', async () => {
    // `other` (KindCategory) ↔ `generic` (TimelineRowType) — the prefs
    // store predates the chip vocabulary; this bridge is asserted so a
    // future rename can't break it silently.
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(prefs.isExpandedByDefault('generic')).toBe(false)
    await w.get('[data-testid="kind-chip-other"]').trigger('click')
    expect(prefs.isExpandedByDefault('generic')).toBe(true)
  })

  it('shows a Reset button only when at least one chip is on', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(false)

    prefs.toggle('tool')
    await w.vm.$nextTick()
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(true)
    await w.get('[data-testid="kind-filter-reset"]').trigger('click')
    expect(prefs.isExpandedByDefault('tool')).toBe(false)
  })

  it('exposes role="toolbar" for assistive tech', () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(w.get('[data-testid="event-kind-filter"]').attributes('role')).toBe(
      'toolbar',
    )
  })
})
