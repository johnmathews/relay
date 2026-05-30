import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EventKindFilter from '../src/components/runs/EventKindFilter.vue'
import {
  KIND_CATEGORIES,
  KIND_MEMBERS,
  type KindCategory,
} from '../src/lib/eventKinds'
import { useTimelinePrefsStore } from '../src/stores/timelinePrefs'

function fullCounts(
  over: Partial<Record<KindCategory, number>> = {},
): Record<KindCategory, number> {
  return Object.fromEntries(
    KIND_CATEGORIES.map((c) => [c, over[c] ?? 0]),
  ) as Record<KindCategory, number>
}

describe('EventKindFilter — chip row drives category visibility', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    try {
      localStorage.removeItem('relay.timeline.hiddenKinds')
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
        counts: fullCounts({ tool: 3, assistant: 7, boundary: 2, artifact: 1 }),
      },
    })
    expect(w.get('[data-testid="kind-count-tool"]').text()).toBe('3')
    expect(w.get('[data-testid="kind-count-assistant"]').text()).toBe('7')
    expect(w.get('[data-testid="kind-count-boundary"]').text()).toBe('2')
    expect(w.get('[data-testid="kind-count-artifact"]').text()).toBe('1')
    expect(w.get('[data-testid="kind-count-signal"]').text()).toBe('0')
  })

  it('every chip starts visible (lit) — default is all-visible', () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    for (const k of KIND_CATEGORIES) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('true')
      expect(chip.classes()).toContain('is-on')
    }
  })

  it('clicking a chip hides that category in the timelinePrefs store', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(prefs.isHidden('tool')).toBe(false)
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')
    expect(prefs.isHidden('tool')).toBe(true)
    const chip = w.get('[data-testid="kind-chip-tool"]')
    expect(chip.attributes('aria-pressed')).toBe('false')
    expect(chip.classes()).not.toContain('is-on')
  })

  it('clicking a hidden chip again shows it (independent per-chip toggle)', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    await w.get('[data-testid="kind-chip-signal"]').trigger('click')
    await w.get('[data-testid="kind-chip-other"]').trigger('click')
    expect(prefs.isHidden('signal')).toBe(true)
    expect(prefs.isHidden('other')).toBe(true)
    // Re-click signal → only `other` stays hidden.
    await w.get('[data-testid="kind-chip-signal"]').trigger('click')
    expect(prefs.isHidden('signal')).toBe(false)
    expect(prefs.isHidden('other')).toBe(true)
  })

  it('shows a Show-all button only when at least one chip is hidden', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(false)

    prefs.toggleHidden('tool')
    await w.vm.$nextTick()
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(true)
    await w.get('[data-testid="kind-filter-reset"]').trigger('click')
    expect(prefs.isHidden('tool')).toBe(false)
  })

  it('chip tooltip lists the underlying event kinds in that category', () => {
    // Hovering "Other" should no longer be a mystery — it lists what
    // falls into the bucket (here: just "unknown / future" since
    // artifact_edited is now its own chip).
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    const other = w.get('[data-testid="kind-chip-other"]')
    const title = other.attributes('title') ?? ''
    for (const member of KIND_MEMBERS.other) {
      expect(title).toContain(member)
    }
    // Spot-check one structural chip too.
    const bound = w.get('[data-testid="kind-chip-boundary"]')
    const boundTitle = bound.attributes('title') ?? ''
    expect(boundTitle).toContain('iter_started')
    expect(boundTitle).toContain('run_ended')
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
