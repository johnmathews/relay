// Per-type expand/collapse defaults for the run-detail timeline.
//
// The dashboard's verbose `/engineering-team` runs need a way to be
// both **scannable** (collapsed by default) and **deep-divable**
// (expand on click). Different row types have different default
// affordances: `ASSISTANT` text is what the user is meant to read,
// so it stays expanded; tool calls / signals / thinking deltas /
// unknown future kinds are scannable headers — collapsed by
// default. The user can flip a type's default via the timeline
// gear-popover and the choice persists across reloads (per-session
// ergonomics felt wrong in testing).
//
// Per-row override (a single row the user has expanded against its
// type default) lives in TimelinePane's component state, not here —
// it shouldn't outlive a tab refresh and shouldn't compete with
// the persisted type default for storage space.

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

/**
 * The row "types" this store knows about. A subset of TimelinePane's
 * full row-type vocabulary — boundary / pause / usage / artifact_edited
 * are intrinsically small (a line or two) and have no useful
 * collapsed state, so the popover doesn't list them.
 */
export type TimelineRowType =
  | 'tool'
  | 'signal'
  | 'assistant'
  | 'thinking'
  | 'generic'

const LS_KEY = 'relay.timeline.expanded'

/**
 * Source of truth for the out-of-the-box behaviour. `assistant`
 * (the `text` kind of `assistant_text` — the agent's actual reply)
 * is expanded; everything else is collapsed. Mirrored in the
 * `isExpandedByDefault` fallback so an unknown row type added in
 * the future does not crash.
 */
const DEFAULTS: Record<TimelineRowType, boolean> = {
  tool: false,
  signal: false,
  assistant: true,
  thinking: false,
  generic: false,
}

function loadFromLocalStorage(): Record<TimelineRowType, boolean> | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw == null || raw === '') return null
    const parsed = JSON.parse(raw)
    if (parsed == null || typeof parsed !== 'object') return null
    // Spread over DEFAULTS so a stored payload that's missing a key
    // (e.g. saved before a new row type was added) is filled in.
    return { ...DEFAULTS, ...(parsed as Partial<Record<TimelineRowType, boolean>>) }
  } catch {
    // Malformed payload — fall back to defaults. Never throw on a
    // user-corrupted localStorage entry.
    return null
  }
}

export const useTimelinePrefsStore = defineStore('timeline-prefs', () => {
  const expanded = ref<Record<TimelineRowType, boolean>>(
    loadFromLocalStorage() ?? { ...DEFAULTS },
  )

  // Persist on any change. Three details are load-bearing:
  //   1. deep:true — toggle() mutates a key in place; without
  //      deep:true the watch fires only on whole-object identity
  //      changes (e.g. reset()).
  //   2. flush:'sync' — the persisted state must be visible to a
  //      subsequent freshly-mounted store *in the same tick* (a
  //      tab-refresh test does setActivePinia + useStore() back to
  //      back). The default 'pre' flush would queue the write past
  //      the test's re-instantiation point.
  //   3. immediate not set — the initial load came from
  //      `loadFromLocalStorage()` already; a watch-on-mount would
  //      pointlessly re-serialise the freshly-loaded value back.
  watch(
    expanded,
    (v) => {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(v))
      } catch {
        // Storage quota exceeded / disabled — preferences just
        // don't persist this session. Not worth surfacing.
      }
    },
    { deep: true, flush: 'sync' },
  )

  function toggle(type: TimelineRowType): void {
    expanded.value[type] = !isExpandedByDefault(type)
  }

  function isExpandedByDefault(type: TimelineRowType): boolean {
    const v = expanded.value[type]
    if (typeof v === 'boolean') return v
    return DEFAULTS[type] ?? false
  }

  function reset(): void {
    expanded.value = { ...DEFAULTS }
  }

  return { expanded, toggle, isExpandedByDefault, reset }
})
