import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TimelineMinimap from '../src/components/runs/TimelineMinimap.vue'

const TICKS = [
  { type: 'assistant', index: 0 },
  { type: 'thinking', index: 1 },
  { type: 'tool', index: 2 },
  { type: 'signal', index: 3 },
  { type: 'boundary', index: 4 },
]

describe('TimelineMinimap', () => {
  it('positions ticks linearly along the strip — first at 0%, last at 100%', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        scrollTop: 0,
        viewportH: 200,
        scrollHeight: 1000,
      },
    })
    const ticks = w.findAll('.minimap__tick')
    expect(ticks).toHaveLength(5)
    // i / (N-1) for N=5: 0%, 25%, 50%, 75%, 100% — last tick
    // anchors at the bottom of the strip so there is no dead band.
    expect(ticks[0]!.attributes('style')).toContain('top: 0%')
    expect(ticks[1]!.attributes('style')).toContain('top: 25%')
    expect(ticks[2]!.attributes('style')).toContain('top: 50%')
    expect(ticks[4]!.attributes('style')).toContain('top: 100%')
  })

  it('single-tick minimap places the lone tick at 0% (avoids divide-by-zero)', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: [{ type: 'assistant', index: 0 }],
        scrollTop: 0,
        viewportH: 200,
        scrollHeight: 1000,
      },
    })
    const ticks = w.findAll('.minimap__tick')
    expect(ticks).toHaveLength(1)
    expect(ticks[0]!.attributes('style')).toContain('top: 0%')
  })

  it('viewport overlay scales with scrollTop / scrollHeight', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        scrollTop: 250,
        viewportH: 200,
        scrollHeight: 1000,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    expect(overlay.attributes('style')).toContain('top: 25%')
    expect(overlay.attributes('style')).toContain('height: 20%')
  })

  it('overlay covers the full strip when content fits the viewport', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        scrollTop: 0,
        viewportH: 600,
        scrollHeight: 0,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    expect(overlay.attributes('style')).toContain('top: 0%')
    expect(overlay.attributes('style')).toContain('height: 100%')
  })

  it('clamps overlay top to [0, 100]% even with out-of-range scroll values', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        scrollTop: 9999,
        viewportH: 200,
        scrollHeight: 1000,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    expect(overlay.attributes('style')).toContain('top: 100%')
  })

  it('emits scroll-to with a clamped target on pointerdown', async () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        scrollTop: 0,
        viewportH: 200,
        scrollHeight: 1000,
      },
      attachTo: document.body,
    })
    const strip = w.get('[data-testid="timeline-minimap"]')
    const el = strip.element as HTMLElement
    // Stub the bounding rect so we know what coords mean.
    el.getBoundingClientRect = () =>
      ({ top: 0, left: 0, width: 22, height: 400 }) as DOMRect
    // Mock pointer capture API (not implemented in jsdom).
    el.setPointerCapture = () => {}
    el.releasePointerCapture = () => {}
    // PointerEvent isn't constructable in jsdom; build a MouseEvent
    // with clientY set, then add a pointerId via Object.assign.
    const ev = new MouseEvent('pointerdown', {
      clientY: 200,
      bubbles: true,
    })
    Object.assign(ev, { pointerId: 1 })
    el.dispatchEvent(ev)
    const emitted = w.emitted('scroll-to')
    expect(emitted).toBeTruthy()
    // Centre of strip (y=200 of 400) → 50% of scrollHeight (500)
    // minus half the viewport (100) → target 400.
    expect(emitted![0]).toEqual([400])
    w.unmount()
  })
})
