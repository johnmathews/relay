import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ItersPane from '../src/components/runs/ItersPane.vue'
import { useCurrentRunStore } from '../src/stores/currentRun'
import type { Iter } from '../src/lib/queries'

function mkIter(over: Partial<Iter> = {}): Iter {
  return {
    id: 100,
    run_id: 'run-1',
    seq: 1,
    phase: 'evaluate',
    pi_session_id: null,
    prompt: 'p',
    preamble: '',
    signal_kind: null,
    signal_args: null,
    started_at: '2026-05-19T10:00:00Z',
    ended_at: null,
    exit_reason: null,
    ...over,
  }
}

const ITERS: Iter[] = [
  mkIter({ id: 10, seq: 1, phase: 'evaluate', signal_kind: 'phase_start' }),
  mkIter({
    id: 11,
    seq: 2,
    phase: 'plan',
    signal_kind: 'pause',
    ended_at: '2026-05-19T10:05:00Z',
    exit_reason: 'signal',
  }),
]

describe('ItersPane', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders iters with seq / phase / signal_kind', () => {
    const w = mount(ItersPane, { props: { iters: ITERS } })
    const t = w.text()
    expect(t).toContain('#1')
    expect(t).toContain('evaluate')
    expect(t).toContain('phase_start')
    expect(t).toContain('#2')
    expect(t).toContain('plan')
    expect(t).toContain('pause')
    expect(w.findAll('[data-testid^="iter-row-"]')).toHaveLength(2)
  })

  it('shows the empty state with no iters', () => {
    const w = mount(ItersPane, { props: { iters: [] } })
    expect(w.text()).toContain('No iters yet.')
  })

  it('clicking an iter sets selectedIterId (the iter SEQ) and toggles off', async () => {
    const w = mount(ItersPane, { props: { iters: ITERS } })
    const store = useCurrentRunStore()
    expect(store.selectedIterId).toBeNull()

    await w.get('[data-testid="iter-row-2"]').trigger('click')
    expect(store.selectedIterId).toBe(2)

    // Clicking the same iter again clears the filter.
    await w.get('[data-testid="iter-row-2"]').trigger('click')
    expect(store.selectedIterId).toBeNull()
  })

  it('"Clear filter" appears only when selected and resets it', async () => {
    const w = mount(ItersPane, { props: { iters: ITERS } })
    const store = useCurrentRunStore()
    expect(w.find('[data-testid="iters-clear-filter"]').exists()).toBe(false)

    await w.get('[data-testid="iter-row-1"]').trigger('click')
    expect(store.selectedIterId).toBe(1)
    const clear = w.find('[data-testid="iters-clear-filter"]')
    expect(clear.exists()).toBe(true)

    await clear.trigger('click')
    expect(store.selectedIterId).toBeNull()
  })
})
