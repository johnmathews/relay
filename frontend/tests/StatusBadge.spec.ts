import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '../src/components/shared/StatusBadge.vue'

describe('StatusBadge', () => {
  it('renders the status text (never color-only)', () => {
    const w = mount(StatusBadge, { props: { status: 'running' } })
    expect(w.text()).toBe('running')
  })

  it('applies a distinct class per known status', () => {
    for (const s of ['running', 'done', 'failed', 'paused', 'cancelled']) {
      const w = mount(StatusBadge, { props: { status: s } })
      expect(w.classes()).toContain(`status-badge--${s}`)
    }
  })

  it('handles an unknown status without crashing (neutral fallback)', () => {
    const w = mount(StatusBadge, { props: { status: 'queued' } })
    expect(w.text()).toBe('queued')
    expect(w.classes()).toContain('status-badge--unknown')
    expect(w.attributes('data-status')).toBe('queued')
  })
})
