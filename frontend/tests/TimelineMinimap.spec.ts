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
  it('tiles ticks edge-to-edge — each at i/n with height 100/n %', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        viewportStart: 0,
        viewportEnd: 0,
      },
    })
    const ticks = w.findAll('.minimap__tick')
    expect(ticks).toHaveLength(5)
    // N=5 slots → top: 0%, 20%, 40%, 60%, 80% — each spans 20%.
    expect(ticks[0]!.attributes('style')).toContain('top: 0%')
    expect(ticks[0]!.attributes('style')).toContain('height: 20%')
    expect(ticks[1]!.attributes('style')).toContain('top: 20%')
    expect(ticks[2]!.attributes('style')).toContain('top: 40%')
    expect(ticks[4]!.attributes('style')).toContain('top: 80%')
    expect(ticks[4]!.attributes('style')).toContain('height: 20%')
  })

  it('single-tick minimap fills the whole strip', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: [{ type: 'assistant', index: 0 }],
        viewportStart: 0,
        viewportEnd: 0,
      },
    })
    const ticks = w.findAll('.minimap__tick')
    expect(ticks).toHaveLength(1)
    expect(ticks[0]!.attributes('style')).toContain('top: 0%')
    expect(ticks[0]!.attributes('style')).toContain('height: 100%')
  })

  it('viewport overlay frames the visible slot range exactly', () => {
    // 5 slots, visible rows [1, 3] → top 20%, height 60% (3 slots).
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        viewportStart: 1,
        viewportEnd: 3,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    expect(overlay.attributes('style')).toContain('top: 20%')
    expect(overlay.attributes('style')).toContain('height: 60%')
  })

  it('single-row viewport spans exactly one slot', () => {
    // visible row [2, 2] → top 40%, height 20%.
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        viewportStart: 2,
        viewportEnd: 2,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    expect(overlay.attributes('style')).toContain('top: 40%')
    expect(overlay.attributes('style')).toContain('height: 20%')
  })

  it('clamps overlay to the strip when viewport indices are out of range', () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        viewportStart: 9999,
        viewportEnd: 9999,
      },
    })
    const overlay = w.get('[data-testid="minimap-viewport"]')
    // clamped to the last slot — top 80%, height 20%.
    expect(overlay.attributes('style')).toContain('top: 80%')
    expect(overlay.attributes('style')).toContain('height: 20%')
  })

  it('emits scroll-to-index with the slot the operator clicked', async () => {
    const w = mount(TimelineMinimap, {
      props: {
        ticks: TICKS,
        viewportStart: 0,
        viewportEnd: 0,
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
    const emitted = w.emitted('scroll-to-index')
    expect(emitted).toBeTruthy()
    // Centre of strip (y=200 of 400) → ratio 0.5 × N=5 = 2.5 → floor 2.
    expect(emitted![0]).toEqual([2])
    w.unmount()
  })
})
