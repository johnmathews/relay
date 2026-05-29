import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import RunHealthBadge from '../src/components/runs/RunHealthBadge.vue'
import type { HeartbeatSnapshot } from '../src/stores/events'

// ADR-45 Plan A. The badge translates the heartbeat snapshot fed by
// the live SSE stream into a "is this still alive?" indicator. The
// dashboard mounts it next to the run-status pill on the run-detail
// header — it MUST disappear entirely for a terminal run (replay
// mode), where no live stream exists and a stale clock would be
// misleading.

const FIXED_NOW = 1_716_000_000_000 // 2026-05-18T05:20:00Z, arbitrary

function snap(
  overrides: Partial<HeartbeatSnapshot> = {},
): HeartbeatSnapshot {
  return {
    serverTs: '2026-05-18T05:20:00+00:00',
    lastEventTs: null,
    receivedAt: FIXED_NOW,
    ...overrides,
  }
}

describe('RunHealthBadge', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(FIXED_NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders NOTHING for a terminal run (replay mode, no live stream)', () => {
    for (const status of ['done', 'failed', 'cancelled']) {
      const w = mount(RunHealthBadge, {
        props: { status, lastHeartbeat: null },
      })
      expect(w.find('[data-testid="run-health-badge"]').exists()).toBe(false)
      w.unmount()
    }
  })

  it('shows "connecting" while live but no heartbeat yet', () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'running', lastHeartbeat: null },
    })
    const el = w.get('[data-testid="run-health-badge"]')
    expect(el.text().toLowerCase()).toContain('connecting')
    expect(el.attributes('data-state')).toBe('connecting')
  })

  it('shows "live" when the last heartbeat is fresh', () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'running', lastHeartbeat: snap() },
    })
    const el = w.get('[data-testid="run-health-badge"]')
    expect(el.attributes('data-state')).toBe('live')
    expect(el.text().toLowerCase()).toContain('live')
  })

  it('transitions live → slow → stalled as the heartbeat ages', async () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'running', lastHeartbeat: snap() },
    })
    const stateNow = (): string | undefined =>
      w.get('[data-testid="run-health-badge"]').attributes('data-state')
    expect(stateNow()).toBe('live')

    // 20 seconds later: amber/slow (threshold ~15s).
    vi.setSystemTime(FIXED_NOW + 20_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(stateNow()).toBe('slow')

    // 90 seconds later: red/stalled (threshold ~60s).
    vi.setSystemTime(FIXED_NOW + 90_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(stateNow()).toBe('stalled')
  })

  it('renders an awaiting_children run with the live badge (not terminal)', () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'awaiting_children', lastHeartbeat: snap() },
    })
    expect(w.find('[data-testid="run-health-badge"]').exists()).toBe(true)
  })

  it('renders a paused run with the live badge (not terminal)', () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'paused', lastHeartbeat: snap() },
    })
    expect(w.find('[data-testid="run-health-badge"]').exists()).toBe(true)
  })

  // Phase 7 a11y — proposal §"Accessibility". The compact visible label
  // ("live · 2s ago") leans on the colour + pulsing dot for state and
  // uses a non-word duration ("2s"); neither reaches SRs. The aria-label
  // spells out state + duration so the SR reading is self-sufficient.
  describe('aria-label (verbose SR signal)', () => {
    it('says "Live stream connecting" while no heartbeat has arrived', () => {
      const w = mount(RunHealthBadge, {
        props: { status: 'running', lastHeartbeat: null },
      })
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live stream connecting')
    })

    it('says "Live, last activity 0 seconds ago" on a fresh heartbeat', () => {
      const w = mount(RunHealthBadge, {
        props: { status: 'running', lastHeartbeat: snap() },
      })
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live, last activity 0 seconds ago')
    })

    it('pluralises and switches state at the slow threshold', async () => {
      const w = mount(RunHealthBadge, {
        props: { status: 'running', lastHeartbeat: snap() },
      })
      // The component's ticking interval re-runs on every interval tick;
      // advancing timers by 1s also progresses wall-clock by 1s, so set
      // pre-advance baseline 1s before the target.
      vi.setSystemTime(FIXED_NOW + 19_000)
      await vi.advanceTimersByTimeAsync(1_000)
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live stream slow, last activity 20 seconds ago')
    })

    it('uses minute granularity once over a minute', async () => {
      const w = mount(RunHealthBadge, {
        props: { status: 'running', lastHeartbeat: snap() },
      })
      vi.setSystemTime(FIXED_NOW + 124_000)
      await vi.advanceTimersByTimeAsync(1_000)
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live stream stalled, last activity 2 minutes 5 seconds ago')
    })
  })
})
