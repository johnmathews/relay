// Reactive viewport breakpoint via `window.matchMedia`. Used by
// Phase 6 of the run-detail layout to collapse the rail to a top
// selector below 900px (proposal §"Layout"). Hand-rolled — keeps the
// composable footprint to one targeted utility (mirrors the
// `useFocusTrap` choice not to pull in @vueuse for a 20-LOC need).
//
// Returns a reactive `isNarrow` `Ref<boolean>` that flips whenever the
// media-query state changes. Cleans up its listener on unmount.
//
// jsdom note: jsdom doesn't lay out, but `window.matchMedia` IS a
// mockable surface. The default jsdom implementation returns a stub
// where `matches: false` always — tests that exercise the narrow path
// must stub matchMedia before mounting the component.

import { onBeforeUnmount, ref, type Ref } from 'vue'

export function useViewportBreakpoint(query: string): Ref<boolean> {
  const isMatch = ref(false)

  // SSR / pre-mount safety: window may not exist if this is ever
  // imported from a non-browser entry. The component shells we use
  // always mount in the browser, but the guard keeps the import safe.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return isMatch
  }

  const mql = window.matchMedia(query)
  isMatch.value = mql.matches

  const onChange = (e: MediaQueryListEvent): void => {
    isMatch.value = e.matches
  }

  // `addEventListener('change', …)` is the modern API. The deprecated
  // `addListener` fallback is intentionally NOT included — every
  // browser we target (and jsdom 22+) supports the event-target API.
  mql.addEventListener('change', onChange)
  onBeforeUnmount(() => {
    mql.removeEventListener('change', onChange)
  })

  return isMatch
}
