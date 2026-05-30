import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTimelinePrefsStore } from '../src/stores/timelinePrefs'
import type { KindCategory } from '../src/lib/eventKinds'

// Focus-style category filter for the dashboard timeline. The chip
// row above the timeline drives a tri-state filter: `all` (default),
// `subset` (only chips in `selected`), `none` (nothing visible).
// Preference persists across reloads via localStorage.

const LS_KEY = 'relay.timeline.kindFilter'

describe('useTimelinePrefsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.removeItem(LS_KEY)
  })

  it('defaults to mode=all with every category visible', () => {
    const s = useTimelinePrefsStore()
    expect(s.mode).toBe('all')
    expect(s.isHidden('assistant')).toBe(false)
    expect(s.isHidden('tool')).toBe(false)
    expect(s.isHidden('signal')).toBe(false)
    expect(s.isHidden('boundary')).toBe(false)
    expect(s.isHidden('pause')).toBe(false)
    expect(s.isHidden('artifact')).toBe(false)
    expect(s.isHidden('other')).toBe(false)
  })

  it('first toggle from `all` enters focus mode with just that chip', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    expect(s.mode).toBe('subset')
    expect(s.isActive('thinking')).toBe(true)
    expect(s.isActive('tool')).toBe(false)
    expect(s.isHidden('tool')).toBe(true)
  })

  it('toggle on a non-active chip in subset mode adds it (additive)', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    s.toggle('tool')
    expect(s.mode).toBe('subset')
    expect(s.isActive('thinking')).toBe(true)
    expect(s.isActive('tool')).toBe(true)
    expect(s.isActive('signal')).toBe(false)
  })

  it('toggle on an active chip removes it', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    s.toggle('tool')
    s.toggle('thinking')
    expect(s.mode).toBe('subset')
    expect(s.isActive('thinking')).toBe(false)
    expect(s.isActive('tool')).toBe(true)
  })

  it('removing the last active chip snaps back to mode=all (no dead state)', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    s.toggle('thinking')
    expect(s.mode).toBe('all')
    // Every category visible again.
    expect(s.isHidden('thinking')).toBe(false)
    expect(s.isHidden('tool')).toBe(false)
  })

  it('toggle from mode=none enters focus mode with just that chip', () => {
    const s = useTimelinePrefsStore()
    s.showNone()
    expect(s.mode).toBe('none')
    s.toggle('signal')
    expect(s.mode).toBe('subset')
    expect(s.isActive('signal')).toBe(true)
    expect(s.isActive('thinking')).toBe(false)
  })

  it('showAll() restores every category to visible', () => {
    const s = useTimelinePrefsStore()
    s.toggle('tool')
    s.toggle('assistant')
    s.showAll()
    expect(s.mode).toBe('all')
    expect(s.isHidden('tool')).toBe(false)
    expect(s.isHidden('assistant')).toBe(false)
  })

  it('showNone() hides every category', () => {
    const s = useTimelinePrefsStore()
    s.showNone()
    expect(s.mode).toBe('none')
    expect(s.isHidden('tool')).toBe(true)
    expect(s.isHidden('assistant')).toBe(true)
    expect(s.isHidden('other')).toBe(true)
  })

  it('persists changes to localStorage and reloads them on a fresh store', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    s.toggle('boundary')

    // Simulate a fresh page load — drop the active pinia, build a
    // new one. The store should see the persisted state.
    setActivePinia(createPinia())
    const s2 = useTimelinePrefsStore()
    expect(s2.mode).toBe('subset')
    expect(s2.isActive('thinking')).toBe(true)
    expect(s2.isActive('boundary')).toBe(true)
    expect(s2.isActive('tool')).toBe(false)
  })

  it('persists mode=none across reloads', () => {
    const s = useTimelinePrefsStore()
    s.showNone()

    setActivePinia(createPinia())
    const s2 = useTimelinePrefsStore()
    expect(s2.mode).toBe('none')
    expect(s2.isHidden('tool')).toBe(true)
  })

  it('ignores malformed localStorage payloads (no throw)', () => {
    localStorage.setItem(LS_KEY, '{not json')
    setActivePinia(createPinia())
    const s = useTimelinePrefsStore()
    expect(s.mode).toBe('all')
    expect(s.isHidden('assistant')).toBe(false)
  })

  it('drops unknown category strings on load (forward compat)', () => {
    // A future build could persist a category that an older bundle
    // doesn't recognise. The loader must drop it rather than crash.
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ mode: 'subset', selected: ['tool', 'mystery'] }),
    )
    setActivePinia(createPinia())
    const s = useTimelinePrefsStore()
    expect(s.mode).toBe('subset')
    expect(s.isActive('tool')).toBe(true)
    expect(s.isActive('mystery' as KindCategory)).toBe(false)
  })

  it('hasSelection reflects mode != all', () => {
    const s = useTimelinePrefsStore()
    expect(s.hasSelection).toBe(false)
    s.toggle('tool')
    expect(s.hasSelection).toBe(true)
    s.showAll()
    expect(s.hasSelection).toBe(false)
    s.showNone()
    expect(s.hasSelection).toBe(true)
  })
})
