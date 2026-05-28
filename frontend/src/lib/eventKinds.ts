/**
 * Event-kind categories surfaced by `EventKindFilter`. Each relay
 * event kind maps to one of five categories so the chip row is a
 * small, scannable control rather than 11+ raw-kind toggles.
 *
 * The mapping is intentionally lossy: `tool_use_start` and
 * `tool_use_end` both fold to `tool` (they render as one card),
 * `iter_started`/`iter_ended`/`run_started`/`run_ended` /
 * `signal_emit` / subagent + child-resolve / harness-session-end all
 * fold to `signal` (they are structural / terminal events the operator
 * reads together), `assistant_text` splits by `payload.kind` into
 * `assistant` (`text`) vs `thinking` (`thinking`), and everything else
 * (`artifact_edited`, future kinds) lands in `other`.
 *
 * The chip row controls **expand-by-default** per category — there is
 * NO visibility filter; every step is always rendered. The category
 * names align 1:1 with `TimelineRowType` except `other` ↔ `generic`;
 * {@link categoryToRowType} bridges them.
 */

import type { PendingTurn, StreamEvent } from '@/stores/events'
import type { TimelineRowType } from '@/stores/timelinePrefs'

export type KindCategory =
  | 'assistant'
  | 'thinking'
  | 'tool'
  | 'signal'
  | 'other'

/** Display order shared by the chip row, the timeline label, and CSS. */
export const KIND_CATEGORIES: readonly KindCategory[] = [
  'assistant',
  'thinking',
  'tool',
  'signal',
  'other',
] as const

export const KIND_LABEL: Record<KindCategory, string> = {
  assistant: 'Assistant',
  thinking: 'Thinking',
  tool: 'Tool calls',
  signal: 'Signals',
  other: 'Other',
}

const SIGNAL_KINDS = new Set([
  'signal_emit',
  'iter_started',
  'iter_ended',
  'run_started',
  'run_ended',
  'subagent_dispatch',
  'subagent_return',
  'child_runs_resolved',
  'harness_session_ended',
  'pause_requested',
  'pause_resolved',
])

const TOOL_KINDS = new Set(['tool_use_start', 'tool_use_end'])

/** Classify a persisted event into its chip category. */
export function classifyEvent(ev: StreamEvent): KindCategory {
  if (ev.kind === 'assistant_text') {
    return ev.payload.kind === 'thinking' ? 'thinking' : 'assistant'
  }
  if (TOOL_KINDS.has(ev.kind)) return 'tool'
  if (SIGNAL_KINDS.has(ev.kind)) return 'signal'
  return 'other'
}

/** Classify a streaming `assistant_delta` pending turn. */
export function classifyPending(pt: PendingTurn): KindCategory {
  return pt.kind === 'thinking' ? 'thinking' : 'assistant'
}

/**
 * Bridge `KindCategory` → `TimelineRowType` so the chip row can drive
 * the per-type expand-by-default in the timelinePrefs store. The names
 * align 1:1 except for `other` ↔ `generic` (legacy of two parallel
 * vocabularies — the prefs store predates the chip categories).
 */
export function categoryToRowType(c: KindCategory): TimelineRowType {
  return c === 'other' ? 'generic' : c
}
