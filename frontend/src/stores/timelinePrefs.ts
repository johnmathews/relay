// Per-category visibility for the run-detail timeline.
//
// The EventKindFilter chip row above the timeline toggles visibility
// per chip category (see `lib/eventKinds`): clicking a chip hides
// every row of that category from the timeline; clicking again shows
// them. Default = every category visible. Choice persists across
// reloads via localStorage.
//
// Per-row expand/collapse (the click target on a row's header) is a
// separate concern — it lives in TimelinePane's component state, not
// here. Rows default to collapsed regardless of visibility.

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { KIND_CATEGORIES, type KindCategory } from '@/lib/eventKinds'

const LS_KEY = 'relay.timeline.hiddenKinds'

function loadFromLocalStorage(): Set<KindCategory> | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw == null || raw === '') return null
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    const valid = new Set(KIND_CATEGORIES)
    const out = new Set<KindCategory>()
    for (const k of parsed) {
      if (typeof k === 'string' && valid.has(k as KindCategory)) {
        out.add(k as KindCategory)
      }
    }
    return out
  } catch {
    return null
  }
}

export const useTimelinePrefsStore = defineStore('timeline-prefs', () => {
  const hidden = ref<Set<KindCategory>>(loadFromLocalStorage() ?? new Set())

  // Persist on any change. `flush:'sync'` so a freshly-mounted store
  // in the same tick (tab-refresh test pattern: setActivePinia + use
  // back-to-back) reads the just-written value. `deep:true` because
  // toggle() mutates the Set in place.
  watch(
    hidden,
    (v) => {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify([...v]))
      } catch {
        // Storage quota / disabled — preferences just don't persist
        // this session. Not worth surfacing.
      }
    },
    { deep: true, flush: 'sync' },
  )

  function isHidden(category: KindCategory): boolean {
    return hidden.value.has(category)
  }

  function toggleHidden(category: KindCategory): void {
    const next = new Set(hidden.value)
    if (next.has(category)) next.delete(category)
    else next.add(category)
    hidden.value = next
  }

  function showAll(): void {
    hidden.value = new Set()
  }

  return { hidden, isHidden, toggleHidden, showAll }
})
