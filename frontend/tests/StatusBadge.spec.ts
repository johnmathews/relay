import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '../src/components/shared/StatusBadge.vue'

describe('StatusBadge', () => {
  it('renders the status text (never color-only)', () => {
    const w = mount(StatusBadge, { props: { status: 'running' } })
    expect(w.text()).toBe('running')
  })

  it('applies a distinct class per known status', () => {
    for (const s of [
      'running',
      'done',
      'failed',
      'paused',
      'cancelled',
      'awaiting_children',
    ]) {
      const w = mount(StatusBadge, { props: { status: s } })
      expect(w.classes()).toContain(`status-badge--${s}`)
    }
  })

  it('renders awaiting_children with its dedicated style (not the "unknown" fallback)', () => {
    // Regression: a future contributor must not accidentally drop
    // `awaiting_children` from the KNOWN set — the fallback styling
    // would visually erase a non-terminal lifecycle state from the
    // dashboard (ADR-34 / spec.md §3.1).
    const w = mount(StatusBadge, { props: { status: 'awaiting_children' } })
    expect(w.text()).toBe('awaiting_children')
    expect(w.classes()).toContain('status-badge--awaiting_children')
    expect(w.classes()).not.toContain('status-badge--unknown')
  })

  it('handles an unknown status without crashing (neutral fallback)', () => {
    const w = mount(StatusBadge, { props: { status: 'queued' } })
    expect(w.text()).toBe('queued')
    expect(w.classes()).toContain('status-badge--unknown')
    expect(w.attributes('data-status')).toBe('queued')
  })
})
