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

describe('EventKindFilter — focus-style chip filter', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    try {
      localStorage.removeItem('relay.timeline.kindFilter')
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

  it('every chip starts lit — default is mode=all', () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    for (const k of KIND_CATEGORIES) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('true')
      expect(chip.classes()).toContain('is-on')
    }
  })

  it('clicking one chip enters focus mode: only that chip lit', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })

    await w.get('[data-testid="kind-chip-thinking"]').trigger('click')

    expect(prefs.mode).toBe('subset')
    expect(prefs.isActive('thinking')).toBe(true)
    const thinking = w.get('[data-testid="kind-chip-thinking"]')
    expect(thinking.attributes('aria-pressed')).toBe('true')
    expect(thinking.classes()).toContain('is-on')

    // Every other chip dim.
    for (const k of KIND_CATEGORIES.filter((c) => c !== 'thinking')) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('false')
      expect(chip.classes()).not.toContain('is-on')
    }
  })

  it('clicking a second chip in focus mode adds it (additive)', async () => {
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    await w.get('[data-testid="kind-chip-thinking"]').trigger('click')
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')

    expect(
      w.get('[data-testid="kind-chip-thinking"]').attributes('aria-pressed'),
    ).toBe('true')
    expect(
      w.get('[data-testid="kind-chip-tool"]').attributes('aria-pressed'),
    ).toBe('true')
    expect(
      w.get('[data-testid="kind-chip-signal"]').attributes('aria-pressed'),
    ).toBe('false')
  })

  it('re-clicking an active chip removes it (and snaps back to all on empty)', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    await w.get('[data-testid="kind-chip-signal"]').trigger('click')
    await w.get('[data-testid="kind-chip-other"]').trigger('click')
    expect(prefs.isActive('signal')).toBe(true)
    expect(prefs.isActive('other')).toBe(true)
    // Re-click signal → only `other` stays active.
    await w.get('[data-testid="kind-chip-signal"]').trigger('click')
    expect(prefs.isActive('signal')).toBe(false)
    expect(prefs.isActive('other')).toBe(true)
    // Re-click other → empty set, snaps to all-visible.
    await w.get('[data-testid="kind-chip-other"]').trigger('click')
    expect(prefs.mode).toBe('all')
    for (const k of KIND_CATEGORIES) {
      expect(prefs.isActive(k)).toBe(true)
    }
  })

  it('shows Show-all button only when mode != all', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(false)

    prefs.toggle('tool')
    await w.vm.$nextTick()
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(true)
    await w.get('[data-testid="kind-filter-reset"]').trigger('click')
    expect(prefs.mode).toBe('all')
    expect(w.find('[data-testid="kind-filter-reset"]').exists()).toBe(false)
  })

  it('shows Show-none button only when mode != none', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(EventKindFilter, {
      props: { counts: fullCounts() },
    })
    expect(w.find('[data-testid="kind-filter-none"]').exists()).toBe(true)
    await w.get('[data-testid="kind-filter-none"]').trigger('click')
    expect(prefs.mode).toBe('none')
    expect(w.find('[data-testid="kind-filter-none"]').exists()).toBe(false)
    for (const k of KIND_CATEGORIES) {
      const chip = w.get(`[data-testid="kind-chip-${k}"]`)
      expect(chip.attributes('aria-pressed')).toBe('false')
    }
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
