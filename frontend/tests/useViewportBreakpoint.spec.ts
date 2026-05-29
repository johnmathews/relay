import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useViewportBreakpoint } from '../src/composables/useViewportBreakpoint'

// A controllable matchMedia stub: tests construct one, set its
// `matches`, and call `.fire(matches)` to trigger a `change` event
// (simulating a window resize crossing the breakpoint).
function makeStubMQL(initialMatches: boolean): {
  matches: boolean
  listeners: Array<(e: { matches: boolean }) => void>
  addEventListener: (type: 'change', cb: (e: { matches: boolean }) => void) => void
  removeEventListener: (type: 'change', cb: (e: { matches: boolean }) => void) => void
  fire: (matches: boolean) => void
} {
  const mql = {
    matches: initialMatches,
    listeners: [] as Array<(e: { matches: boolean }) => void>,
    addEventListener(_type: 'change', cb: (e: { matches: boolean }) => void): void {
      this.listeners.push(cb)
    },
    removeEventListener(_type: 'change', cb: (e: { matches: boolean }) => void): void {
      const i = this.listeners.indexOf(cb)
      if (i >= 0) this.listeners.splice(i, 1)
    },
    fire(matches: boolean): void {
      this.matches = matches
      for (const cb of [...this.listeners]) cb({ matches })
    },
  }
  return mql
}

describe('useViewportBreakpoint', () => {
  const originalMM = window.matchMedia
  let mql: ReturnType<typeof makeStubMQL>

  beforeEach(() => {
    mql = makeStubMQL(false)
    window.matchMedia = vi.fn(() => mql) as unknown as typeof window.matchMedia
  })
  afterEach(() => {
    window.matchMedia = originalMM
  })

  function harnessFor(query: string): {
    component: ReturnType<typeof defineComponent>
  } {
    return {
      component: defineComponent({
        setup() {
          const isMatch = useViewportBreakpoint(query)
          return () => h('div', { 'data-match': String(isMatch.value) })
        },
      }),
    }
  }

  it('reflects the initial matchMedia state on mount', () => {
    mql = makeStubMQL(true)
    window.matchMedia = vi.fn(() => mql) as unknown as typeof window.matchMedia
    const { component } = harnessFor('(max-width: 899px)')
    const w = mount(component)
    expect(w.attributes('data-match')).toBe('true')
  })

  it('updates reactively when matchMedia fires `change`', async () => {
    const { component } = harnessFor('(max-width: 899px)')
    const w = mount(component)
    expect(w.attributes('data-match')).toBe('false')
    mql.fire(true)
    await w.vm.$nextTick()
    expect(w.attributes('data-match')).toBe('true')
    mql.fire(false)
    await w.vm.$nextTick()
    expect(w.attributes('data-match')).toBe('false')
  })

  it('removes its listener on unmount (no leaks)', () => {
    const { component } = harnessFor('(max-width: 899px)')
    const w = mount(component)
    expect(mql.listeners).toHaveLength(1)
    w.unmount()
    expect(mql.listeners).toHaveLength(0)
  })

  it('passes the query through to matchMedia verbatim', () => {
    const { component } = harnessFor('(max-width: 600px)')
    mount(component)
    expect(window.matchMedia).toHaveBeenCalledWith('(max-width: 600px)')
  })
})
