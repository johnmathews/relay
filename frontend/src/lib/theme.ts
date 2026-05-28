/*
 * Theme controller.
 *
 * Three logical states:
 *   - 'auto'  → follow `prefers-color-scheme` (default)
 *   - 'light' → force light
 *   - 'dark'  → force dark
 *
 * The choice is persisted in localStorage under `relay.theme` and
 * mirrored to `<html data-theme="…">` so `styles/base.css` can resolve
 * the right palette. We apply the attribute as early as possible (call
 * `applyInitialTheme` from main.ts before `app.mount`) to avoid a
 * dark→light flash on first paint when the user previously picked
 * light.
 *
 * The OS-preference channel is observed too: with `theme === 'auto'`
 * a `prefers-color-scheme` flip is reflected immediately (no reload).
 */

import { computed, ref, type ComputedRef, type Ref } from 'vue'

export type ThemeChoice = 'auto' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'relay.theme'

function isChoice(v: unknown): v is ThemeChoice {
  return v === 'auto' || v === 'light' || v === 'dark'
}

function readStored(): ThemeChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return isChoice(v) ? v : 'auto'
  } catch {
    return 'auto'
  }
}

function osPrefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return true
  }
}

function resolve(choice: ThemeChoice): ResolvedTheme {
  if (choice === 'light' || choice === 'dark') return choice
  return osPrefersDark() ? 'dark' : 'light'
}

function applyAttribute(choice: ThemeChoice): void {
  const root = document.documentElement
  // We set `data-theme` to the choice (not the resolved value) so the
  // CSS @media auto branch keeps doing its job. For 'light'/'dark' the
  // explicit selectors win; for 'auto' the OS media query controls.
  root.setAttribute('data-theme', choice)
}

const choiceRef = ref<ThemeChoice>('auto')

let osListenerInstalled = false
function installOsListener(): void {
  if (osListenerInstalled) return
  try {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    // The OS-preference flip is owned by the CSS `@media
    // (prefers-color-scheme: light)` block in styles/base.css when
    // data-theme is 'auto' — no JS action is required for the visual
    // flip. We don't subscribe to a `change` event here because Vue's
    // reactivity short-circuits ref-equal assignments (so the
    // "trigger by re-assigning the same value" trick from an earlier
    // draft was a no-op). Components that need the resolved
    // light/dark for non-CSS decisions should read it on demand via
    // `useTheme().resolved` at the point of use.
    void mql
    osListenerInstalled = true
  } catch {
    // matchMedia missing (SSR / very old env) — auto mode degrades to
    // whatever resolve() returned on init.
  }
}

/** Apply the persisted choice to `<html>` before app mount. Idempotent. */
export function applyInitialTheme(): void {
  const choice = readStored()
  choiceRef.value = choice
  applyAttribute(choice)
  installOsListener()
}

/** Reactive composable. */
export function useTheme(): {
  choice: Ref<ThemeChoice>
  resolved: ComputedRef<ResolvedTheme>
  set: (next: ThemeChoice) => void
  cycle: () => void
} {
  const resolved = computed<ResolvedTheme>(() => resolve(choiceRef.value))

  function set(next: ThemeChoice): void {
    choiceRef.value = next
    applyAttribute(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Private mode / disabled storage — choice still applies for the session.
    }
  }

  /** auto → light → dark → auto */
  function cycle(): void {
    const order: ThemeChoice[] = ['auto', 'light', 'dark']
    const i = order.indexOf(choiceRef.value)
    const next = order[(i + 1) % order.length] ?? 'auto'
    set(next)
  }

  return { choice: choiceRef, resolved, set, cycle }
}
