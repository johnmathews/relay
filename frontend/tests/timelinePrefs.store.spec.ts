import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTimelinePrefsStore } from '../src/stores/timelinePrefs'
import type { KindCategory } from '../src/lib/eventKinds'

// Per-category visibility for the dashboard timeline. The chip row
// above the timeline toggles category visibility; default is every
// category visible. Preference persists across reloads via
// localStorage.

const LS_KEY = 'relay.timeline.hiddenKinds'

describe('useTimelinePrefsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.removeItem(LS_KEY)
  })

  it('defaults: every category visible (no kinds hidden)', () => {
    const s = useTimelinePrefsStore()
    expect(s.isHidden('assistant')).toBe(false)
    expect(s.isHidden('tool')).toBe(false)
    expect(s.isHidden('signal')).toBe(false)
    expect(s.isHidden('boundary')).toBe(false)
    expect(s.isHidden('pause')).toBe(false)
    expect(s.isHidden('artifact')).toBe(false)
    expect(s.isHidden('other')).toBe(false)
  })

  it('toggleHidden(category) flips its visibility', () => {
    const s = useTimelinePrefsStore()
    expect(s.isHidden('tool')).toBe(false)
    s.toggleHidden('tool')
    expect(s.isHidden('tool')).toBe(true)
    s.toggleHidden('tool')
    expect(s.isHidden('tool')).toBe(false)
  })

  it('persists changes to localStorage and reloads them on a fresh store', () => {
    const s = useTimelinePrefsStore()
    s.toggleHidden('thinking')
    s.toggleHidden('boundary')

    // Simulate a fresh page load — drop the active pinia, build a
    // new one. The store should see the persisted state.
    setActivePinia(createPinia())
    const s2 = useTimelinePrefsStore()
    expect(s2.isHidden('thinking')).toBe(true)
    expect(s2.isHidden('boundary')).toBe(true)
    expect(s2.isHidden('tool')).toBe(false) // unchanged
  })

  it('showAll() restores every category to visible', () => {
    const s = useTimelinePrefsStore()
    s.toggleHidden('tool')
    s.toggleHidden('assistant')
    s.showAll()
    expect(s.isHidden('tool')).toBe(false)
    expect(s.isHidden('assistant')).toBe(false)
  })

  it('ignores malformed localStorage payloads (no throw)', () => {
    localStorage.setItem(LS_KEY, '{not json')
    setActivePinia(createPinia())
    const s = useTimelinePrefsStore()
    // Falls back to defaults — every kind visible.
    expect(s.isHidden('assistant')).toBe(false)
    expect(s.isHidden('tool')).toBe(false)
  })

  it('drops unknown category strings on load (forward compat)', () => {
    // A future build could persist a category that an older bundle
    // doesn't recognise. The loader must drop it rather than crash.
    localStorage.setItem(LS_KEY, JSON.stringify(['tool', 'mystery']))
    setActivePinia(createPinia())
    const s = useTimelinePrefsStore()
    expect(s.isHidden('tool')).toBe(true)
    expect(s.isHidden('mystery' as KindCategory)).toBe(false)
  })
})
