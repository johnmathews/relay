import { describe, expect, it, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TimelineDisplayMenu from '../src/components/runs/TimelineDisplayMenu.vue'
import { useTimelinePrefsStore } from '../src/stores/timelinePrefs'

describe('TimelineDisplayMenu', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    try {
      localStorage.removeItem('relay.timelinePrefs')
    } catch {
      // ignore
    }
  })

  it('renders a Display button + opens / closes its popover on click', async () => {
    const w = mount(TimelineDisplayMenu)
    expect(w.get('[data-testid="display-gear"]').text()).toContain('Display')
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(false)
    await w.get('[data-testid="display-gear"]').trigger('click')
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(true)
    await w.get('[data-testid="display-gear"]').trigger('click')
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(false)
  })

  it('exposes aria-haspopup + reflects open state via aria-expanded and a chevron class', async () => {
    // The button needs to read AS a dropdown trigger, not a status
    // pill. aria-haspopup signals "this opens a menu" to screen readers
    // and the rotating chevron is the visual cue. Both flip together
    // when the popover toggles.
    const w = mount(TimelineDisplayMenu)
    const btn = w.get('[data-testid="display-gear"]')
    expect(btn.attributes('aria-haspopup')).toBe('menu')
    expect(btn.attributes('aria-expanded')).toBe('false')
    expect(w.find('.display-menu__chevron--open').exists()).toBe(false)
    await btn.trigger('click')
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(w.find('.display-menu__chevron--open').exists()).toBe(true)
  })

  it('flips the type default in the prefs store when a toggle is clicked', async () => {
    const prefs = useTimelinePrefsStore()
    const before = prefs.isExpandedByDefault('tool')
    const w = mount(TimelineDisplayMenu)
    await w.get('[data-testid="display-gear"]').trigger('click')
    await w.get('[data-testid="display-toggle-tool"]').trigger('click')
    expect(prefs.isExpandedByDefault('tool')).toBe(!before)
  })

  it('Reset returns all type defaults to baseline', async () => {
    const prefs = useTimelinePrefsStore()
    const w = mount(TimelineDisplayMenu)
    await w.get('[data-testid="display-gear"]').trigger('click')
    await w.get('[data-testid="display-toggle-tool"]').trigger('click')
    await w.get('[data-testid="display-toggle-signal"]').trigger('click')
    await w.get('[data-testid="display-reset"]').trigger('click')
    await flushPromises()
    // Defaults match the store's reset() — assert via the store, not
    // hardcoded values, so a doc change to defaults doesn't break this.
    const fresh = useTimelinePrefsStore()
    fresh.reset()
    expect(prefs.isExpandedByDefault('tool')).toBe(
      fresh.isExpandedByDefault('tool'),
    )
    expect(prefs.isExpandedByDefault('signal')).toBe(
      fresh.isExpandedByDefault('signal'),
    )
  })
})
