// Hand-rolled focus trap for the Phase 5 tool-call drawer (and any
// future ARIA dialog). When `active` flips true:
//   1. Captures the currently-focused element so it can be restored on
//      deactivate.
//   2. Moves focus to the first focusable descendant of `rootEl`.
//   3. Intercepts Tab / Shift+Tab on `rootEl` to wrap focus inside it.
//   4. Intercepts Escape and calls the optional `onEscape` callback.
//
// When `active` flips false (or the component unmounts), removes the
// listener and restores focus to the previously-active element.
//
// Intentionally narrow: no roving-tabindex, no inert outside the root,
// no listener on `document` (Escape is captured on the trap root only —
// the drawer's root receives focus on open, so keydown bubbles to it).
// The drawer's backdrop swallows pointer events outside the dialog box.

import { onBeforeUnmount, watch, type Ref } from 'vue'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableWithin(root: HTMLElement): HTMLElement[] {
  // No layout-visibility filter: jsdom does not compute `offsetParent`,
  // so a `el.offsetParent !== null` guard would empty the list under
  // test. For the drawer's known set of controls (mode select + close
  // button + interactive renderer output) this is fine; the drawer
  // never hides individual focusables behind `display:none` while it
  // is itself open.
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

interface UseFocusTrapOptions {
  active: Ref<boolean>
  onEscape?: () => void
}

export function useFocusTrap(
  rootEl: Ref<HTMLElement | null>,
  options: UseFocusTrapOptions,
): void {
  let previouslyFocused: HTMLElement | null = null

  function onKeydown(ev: KeyboardEvent): void {
    if (ev.key === 'Escape') {
      ev.stopPropagation()
      options.onEscape?.()
      return
    }
    if (ev.key !== 'Tab') return
    const root = rootEl.value
    if (root == null) return
    const focusables = focusableWithin(root)
    if (focusables.length === 0) {
      ev.preventDefault()
      return
    }
    const first = focusables[0]!
    const last = focusables[focusables.length - 1]!
    const activeEl = document.activeElement as HTMLElement | null
    if (ev.shiftKey) {
      if (activeEl === first || !root.contains(activeEl)) {
        ev.preventDefault()
        last.focus()
      }
    } else {
      if (activeEl === last || !root.contains(activeEl)) {
        ev.preventDefault()
        first.focus()
      }
    }
  }

  function activate(): void {
    previouslyFocused = (document.activeElement as HTMLElement | null) ?? null
    const root = rootEl.value
    if (root == null) return
    root.addEventListener('keydown', onKeydown)
    // Focus the first focusable inside the trap. Fall back to the root
    // itself (it should carry `tabindex="-1"` so the focus call works).
    const focusables = focusableWithin(root)
    const target = focusables[0] ?? root
    target.focus()
  }

  function deactivate(): void {
    const root = rootEl.value
    root?.removeEventListener('keydown', onKeydown)
    if (previouslyFocused != null && document.contains(previouslyFocused)) {
      previouslyFocused.focus()
    }
    previouslyFocused = null
  }

  watch(
    () => options.active.value,
    (now, prev) => {
      if (now === prev) return
      if (now) {
        // Wait one tick: the drawer's `v-if` mounts the root in the
        // same tick `active` flips true; the element ref is only
        // populated after the DOM is patched.
        queueMicrotask(activate)
      } else {
        deactivate()
      }
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    if (options.active.value) deactivate()
  })
}
