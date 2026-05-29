// Phase 5 surface — the new "View full" trigger on ToolCallCard. The
// inline "Show full" 5-line collapse / expand is covered by
// TimelinePane.spec.ts (line 321 et seq) and is intentionally NOT
// re-asserted here.

import { describe, it, expect, vi } from 'vitest'
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
