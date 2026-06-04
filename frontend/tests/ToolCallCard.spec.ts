// Phase 5 surface — the new "View full" trigger on ToolCallCard. The
// inline "Show full" 5-line collapse / expand is covered by
// TimelinePane.spec.ts (line 321 et seq) and is intentionally NOT
// re-asserted here.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCallCard from '../src/components/runs/ToolCallCard.vue'

describe('ToolCallCard — View full trigger', () => {
  it('does NOT render the View-full button when no openToolDetail is provided', () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        result: 'hi',
        isError: false,
        durationMs: 12,
      },
    })
    expect(w.find('[data-testid="tool-card-view-full"]').exists()).toBe(
      false,
    )
  })

  it('renders the View-full button when openToolDetail is provided and calls it on click', async () => {
    const spy = vi.fn()
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        result: 'hi',
        isError: false,
        durationMs: 12,
      },
      global: {
        provide: { openToolDetail: spy },
      },
    })
    const btn = w.find('[data-testid="tool-card-view-full"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0]![0]).toEqual({
      name: 'Bash',
      args: { command: 'echo hi' },
      result: 'hi',
      isError: false,
      durationMs: 12,
    })
  })

  it('preserves the existing inline "Show full" toggle alongside View full', async () => {
    // Args as a plain string — `pretty()` returns it verbatim so the
    // newline count is honoured (an object would JSON.stringify with
    // escaped `\n` into a single line, defeating the >5-line trigger).
    const spy = vi.fn()
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: 'a\nb\nc\nd\ne\nf\ng',
        result: 'x',
        isError: false,
        durationMs: 1,
      },
      global: {
        provide: { openToolDetail: spy },
      },
    })
    // Both affordances coexist.
    expect(w.find('[data-testid="tool-card-toggle"]').exists()).toBe(true)
    expect(w.find('[data-testid="tool-card-view-full"]').exists()).toBe(true)
  })
})

describe('ToolCallCard — running chip', () => {
  const FIXED_NOW = 1_716_000_000_000

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(FIXED_NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders "running Ns" while the tool is pending and ticks every second', async () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'sleep 100' },
        startedAt: FIXED_NOW - 3_000,
        // result deliberately omitted → pending
      },
    })
    const chip = w.get('[data-testid="tool-card-running"]')
    expect(chip.text()).toMatch(/running 3s/)

    vi.setSystemTime(FIXED_NOW + 4_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(w.get('[data-testid="tool-card-running"]').text())
      .toMatch(/running 8s/)
  })

  it('accepts an ISO string for startedAt and parses it', () => {
    const startedAtIso = new Date(FIXED_NOW - 7_000).toISOString()
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'sleep 100' },
        startedAt: startedAtIso,
      },
    })
    expect(w.get('[data-testid="tool-card-running"]').text())
      .toMatch(/running 7s/)
  })

  it('renders no running chip once a result is present', () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        result: 'hi',
        startedAt: FIXED_NOW - 3_000,
        durationMs: 3000,
      },
    })
    expect(w.find('[data-testid="tool-card-running"]').exists()).toBe(false)
  })

  it('renders no running chip when startedAt is null', () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        startedAt: null,
      },
    })
    expect(w.find('[data-testid="tool-card-running"]').exists()).toBe(false)
  })
})
