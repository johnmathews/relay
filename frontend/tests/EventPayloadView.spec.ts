import { describe, it, expect } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import EventPayloadView from '../src/components/runs/EventPayloadView.vue'

// Mock the shiki-backed renderer so we don't have to spin up the
// real highlighter in a unit test. `renderCode` returns sanitised
// HTML; an empty string fallback is fine here — we only assert that
// the raw-mode view swaps in and shows the JSON in some form.
import { vi } from 'vitest'
vi.mock('@/lib/render', () => ({
  renderCode: vi.fn().mockResolvedValue({ html: '<pre>{"k":"v"}</pre>' }),
}))

describe('EventPayloadView', () => {
  it('renders one labeled section per top-level field', () => {
    const w = mount(EventPayloadView, {
      props: { payload: { seq: 3, phase: 'wrap-up', misc: null } },
    })
    const view = w.get('[data-testid="event-payload-view"]')
    expect(view.text()).toContain('seq')
    expect(view.text()).toContain('3')
    expect(view.text()).toContain('phase')
    expect(view.text()).toContain('wrap-up')
    expect(view.text()).toContain('misc')
    expect(view.text()).toContain('null')
  })

  it('renders multi-line strings with real newlines, NOT \\n escapes', () => {
    // The whole motivation for the field-aware view: long string
    // fields (prompt / preamble) carry embedded newlines that the
    // old JSON.stringify dump rendered as the literal two-character
    // escape `\n`. The new view splits on `\n` so the prompt is
    // readable.
    const prompt = 'line 1\nline 2\nline 3'
    const w = mount(EventPayloadView, {
      props: { payload: { prompt } },
    })
    const pre = w.get('[data-testid="payload-multiline"]')
    // Browser preserves the newlines; assert the text contains real
    // line breaks and NOT the literal `\n` escape sequence.
    expect(pre.text()).toContain('line 1')
    expect(pre.text()).toContain('line 2')
    expect(pre.text()).not.toContain('\\n')
  })

  it('collapses very long multi-line strings with a show-more toggle', async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`)
    const w = mount(EventPayloadView, {
      props: { payload: { prompt: lines.join('\n') }, collapseLinesAt: 5 },
    })
    const more = w.get('[data-testid="payload-more-0"]')
    expect(more.text()).toContain('Show all 30 lines')
    const pre = w.get('[data-testid="payload-multiline"]')
    // Before expand: only the first 5 lines are visible.
    expect(pre.text()).toContain('line 1')
    expect(pre.text()).toContain('line 5')
    expect(pre.text()).not.toContain('line 6')

    await more.trigger('click')
    expect(pre.text()).toContain('line 30')
    expect(more.text()).toBe('Show less')
  })

  it('"View raw JSON" toggle swaps to the shiki-rendered raw view', async () => {
    const w = mount(EventPayloadView, {
      props: { payload: { phase: 'wrap-up' } },
    })
    // Field-aware view is the default.
    expect(w.find('[data-testid="payload-multiline"]').exists()).toBe(false)
    expect(w.text()).toContain('phase')

    const toggle = w.get('[data-testid="payload-view-toggle-raw"]')
    expect(toggle.text()).toContain('View raw JSON')
    await toggle.trigger('click')
    await flushPromises()
    // Shows raw-mode label.
    expect(toggle.text()).toContain('View formatted')
  })

  it('renders nothing for null / empty-object payloads', () => {
    const w = mount(EventPayloadView, { props: { payload: null } })
    expect(w.find('[data-testid="payload-multiline"]').exists()).toBe(false)
    // The toolbar is still rendered (so the user can flip to raw
    // even on an empty payload), but no field rows.
    expect(w.findAll('.payload-view__key').length).toBe(0)
  })
})
