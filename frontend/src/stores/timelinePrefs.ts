// Per-category visibility for the run-detail timeline.
//
// The EventKindFilter chip row above the timeline drives a
// **focus-style filter** (Material filter chips / Gmail-label
// behaviour): the operator clicks one kind to focus on it, additional
// chips add to the active set, re-clicking removes. Empty active set
// reverts to the all-visible default so the user can't strand
// themselves on an empty timeline by chip clicks alone — explicit
// "Show none" is the only way to land on the empty state.
//
// Three modes:
// - `all`    — default; every category visible; chips render lit.
// - `subset` — only categories in `selected` visible; non-selected
//              chips render dim.
// - `none`   — nothing visible; every chip dim. Reached only via the
//              explicit "Show none" button.
//
// Read-side API: `isHidden(category)` — TimelinePane filters rows on
// this. `isActive(category)` is the lit/dim signal for the chip UI
// (the negation, named so the chip template reads cleanly).
//
// Choice persists across reloads via localStorage. Per-row
// expand/collapse is unrelated and lives in TimelinePane's component
// state.

import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { KIND_CATEGORIES, type KindCategory } from '@/lib/eventKinds'

const LS_KEY = 'relay.timeline.kindFilter'

type Mode = 'all' | 'subset' | 'none'

interface Persisted {
  mode: Mode
  selected: KindCategory[]
}

function loadFromLocalStorage(): Persisted | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw == null || raw === '') return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const obj = parsed as Record<string, unknown>
    const rawMode = obj.mode
    const mode: Mode =
      rawMode === 'all' || rawMode === 'subset' || rawMode === 'none'
        ? rawMode
        : 'all'
    const rawSel = obj.selected
    const valid = new Set<KindCategory>(KIND_CATEGORIES)
    const selected: KindCategory[] = []
    if (Array.isArray(rawSel)) {
      for (const k of rawSel) {
        if (typeof k === 'string' && valid.has(k as KindCategory)) {
          selected.push(k as KindCategory)
        }
      }
    }
    return { mode, selected }
  } catch {
    return null
  }
}

export const useTimelinePrefsStore = defineStore('timeline-prefs', () => {
  const initial = loadFromLocalStorage()
  const mode = ref<Mode>(initial?.mode ?? 'all')
  const selected = ref<Set<KindCategory>>(
    new Set(initial?.selected ?? []),
  )

  // Persist on any change. `flush:'sync'` so a freshly-mounted store
  // in the same tick (tab-refresh test pattern) reads the just-written
  // value. `deep:true` because mutations swap the Set in place.
  watch(
    [mode, selected],
    () => {
      try {
        const payload: Persisted = {
          mode: mode.value,
          selected: [...selected.value],
        }
        localStorage.setItem(LS_KEY, JSON.stringify(payload))
      } catch {
        // Storage quota / disabled — preferences just don't persist
        // this session.
      }
    },
    { deep: true, flush: 'sync' },
  )

  function isHidden(category: KindCategory): boolean {
    if (mode.value === 'all') return false
    if (mode.value === 'none') return true
    return !selected.value.has(category)
  }

  function isActive(category: KindCategory): boolean {
    return !isHidden(category)
  }

  // Chip-click behaviour: focus-mode toggle. From `all` or `none`,
  // the first click enters `subset` with just that chip. Subsequent
  // clicks add/remove. Removing the last selected chip snaps back to
  // `all` (so the user can't strand themselves on an empty timeline
  // via chip clicks — that requires the explicit "Show none" button).
  function toggle(category: KindCategory): void {
    if (mode.value === 'all' || mode.value === 'none') {
      mode.value = 'subset'
      selected.value = new Set([category])
      return
    }
    const next = new Set(selected.value)
    if (next.has(category)) {
      next.delete(category)
      if (next.size === 0) {
        mode.value = 'all'
        selected.value = new Set()
        return
      }
    } else {
      next.add(category)
    }
    selected.value = next
  }

  function showAll(): void {
    mode.value = 'all'
    selected.value = new Set()
  }

  function showNone(): void {
    mode.value = 'none'
    selected.value = new Set()
  }

  const hasSelection = computed(() => mode.value !== 'all')

  return {
    mode,
    selected,
    hasSelection,
    isHidden,
    isActive,
    toggle,
    showAll,
    showNone,
  }
})
