import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import {
  useTimelinePrefsStore,
  type TimelineRowType,
} from '../src/stores/timelinePrefs'

// Per-type expand/collapse defaults for the dashboard timeline. The
// motivating UX: `ASSISTANT` rows are what the user is meant to
// read (expanded by default) while tool/signal/thinking/generic
// rows are scannable headers (collapsed by default). The user can
// flip a type's default via a gear-popover, and that preference
// MUST survive a refresh (per-session ergonomics are a downgrade
// users already pushed back on).

const LS_KEY = 'relay.timeline.expanded'

describe('useTimelinePrefsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.removeItem(LS_KEY)
  })

  it('defaults: assistant expanded; tool/signal/thinking/generic collapsed', () => {
    const s = useTimelinePrefsStore()
    expect(s.isExpandedByDefault('assistant')).toBe(true)
    expect(s.isExpandedByDefault('tool')).toBe(false)
    expect(s.isExpandedByDefault('signal')).toBe(false)
    expect(s.isExpandedByDefault('thinking')).toBe(false)
    expect(s.isExpandedByDefault('generic')).toBe(false)
  })

  it('toggle(type) flips the type default', () => {
    const s = useTimelinePrefsStore()
    expect(s.isExpandedByDefault('tool')).toBe(false)
    s.toggle('tool')
    expect(s.isExpandedByDefault('tool')).toBe(true)
    s.toggle('tool')
    expect(s.isExpandedByDefault('tool')).toBe(false)
  })

  it('persists changes to localStorage and reloads them on a fresh store', () => {
    const s = useTimelinePrefsStore()
    s.toggle('thinking')
    s.toggle('assistant') // flip the default-expanded one off

    // Simulate a fresh page load — drop the active pinia, build a
    // new one. The store should see the persisted state.
    setActivePinia(createPinia())
    const s2 = useTimelinePrefsStore()
    expect(s2.isExpandedByDefault('thinking')).toBe(true)
    expect(s2.isExpandedByDefault('assistant')).toBe(false)
    expect(s2.isExpandedByDefault('tool')).toBe(false) // unchanged
  })

  it('reset() restores the original defaults', () => {
    const s = useTimelinePrefsStore()
    s.toggle('tool')
    s.toggle('assistant')
    s.reset()
    expect(s.isExpandedByDefault('tool')).toBe(false)
    expect(s.isExpandedByDefault('assistant')).toBe(true)
  })

  it('ignores malformed localStorage payloads (no throw)', () => {
    localStorage.setItem(LS_KEY, '{not json')
    setActivePinia(createPinia())
    const s = useTimelinePrefsStore()
    // Falls back to defaults — assistant expanded.
    expect(s.isExpandedByDefault('assistant')).toBe(true)
  })

  it('unknown row types fall back to collapsed', () => {
    const s = useTimelinePrefsStore()
    // The compile-time TimelineRowType union limits valid calls,
    // but a future row type added before the store is updated
    // should not crash. Cast to satisfy TS for the test.
    expect(s.isExpandedByDefault('mystery' as TimelineRowType)).toBe(false)
  })
})
