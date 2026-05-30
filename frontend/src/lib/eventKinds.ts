/**
 * Event-kind categories surfaced by `EventKindFilter`. Each relay
 * event kind maps to one of eight chip categories so the chip row is
 * a small, scannable visibility control rather than a 13+ raw-kind
 * toggle bank.
 *
 * The previous five-chip split lumped "structural / boundary /
 * lifecycle / pause / artifact / future" all into `signal` + `other`,
 * which obscured what a chip was actually filtering. The current
 * split is:
 *
 *   - `assistant`  — agent reply text (`assistant_text` kind=text)
 *   - `thinking`   — model reasoning stream (`assistant_text` kind=thinking)
 *   - `tool`       — tool_use_start/end (paired into one row)
 *   - `signal`     — sentinel signals only (`signal_emit`)
 *   - `boundary`   — iter/run lifecycle (iter_started/ended, run_started/ended, harness_session_ended)
 *   - `pause`      — pause + child/fanout coordination (pause_requested/resolved, subagent_*, child_runs_resolved)
 *   - `artifact`   — operator file edits during a paused review (`artifact_edited`)
 *   - `other`      — true unknown / future kinds only
 *
 * The chip row controls **visibility** per category: clicking a chip
 * hides every row of that category from the timeline; clicking again
 * shows them. Default = all visible. Per-row expand/collapse is a
 * separate concern (TimelinePane's `rowOverrides`).
 */

import type { PendingTurn, StreamEvent } from '@/stores/events'

export type KindCategory =
  | 'assistant'
  | 'thinking'
  | 'tool'
  | 'signal'
  | 'boundary'
  | 'pause'
  | 'artifact'
  | 'other'

/** Display order shared by the chip row, the timeline label, and CSS. */
export const KIND_CATEGORIES: readonly KindCategory[] = [
  'assistant',
  'thinking',
  'tool',
  'signal',
  'boundary',
  'pause',
  'artifact',
  'other',
] as const

export const KIND_LABEL: Record<KindCategory, string> = {
  assistant: 'Assistant',
  thinking: 'Thinking',
  tool: 'Tool calls',
  signal: 'Signals',
  boundary: 'Boundaries',
  pause: 'Pauses',
  artifact: 'Artifacts',
  other: 'Other',
}

const TOOL_KINDS = new Set(['tool_use_start', 'tool_use_end'])
const SIGNAL_KINDS = new Set(['signal_emit'])
const BOUNDARY_KINDS = new Set([
  'iter_started',
  'iter_ended',
  'run_started',
  'run_ended',
  'harness_session_ended',
])
const PAUSE_KINDS = new Set([
  'pause_requested',
  'pause_resolved',
  'subagent_dispatch',
  'subagent_return',
  'child_runs_resolved',
])
const ARTIFACT_KINDS = new Set(['artifact_edited'])

/**
 * Underlying event kinds per category, exposed so the chip tooltip
 * can list "what's in this category" without each component
 * re-deriving it. The strings match the on-wire `kind` values from
 * `api/events.py` (the `_event_payload` builder).
 */
export const KIND_MEMBERS: Record<KindCategory, readonly string[]> = {
  assistant: ['assistant_text (text)'],
  thinking: ['assistant_text (thinking)'],
  tool: ['tool_use_start', 'tool_use_end'],
  signal: ['signal_emit'],
  boundary: [
    'iter_started',
    'iter_ended',
    'run_started',
    'run_ended',
    'harness_session_ended',
  ],
  pause: [
    'pause_requested',
    'pause_resolved',
    'subagent_dispatch',
    'subagent_return',
    'child_runs_resolved',
  ],
  artifact: ['artifact_edited'],
  other: ['unknown / future event kinds'],
}

/** Classify a persisted event into its chip category. */
export function classifyEvent(ev: StreamEvent): KindCategory {
  if (ev.kind === 'assistant_text') {
    return ev.payload.kind === 'thinking' ? 'thinking' : 'assistant'
  }
  if (TOOL_KINDS.has(ev.kind)) return 'tool'
  if (SIGNAL_KINDS.has(ev.kind)) return 'signal'
  if (BOUNDARY_KINDS.has(ev.kind)) return 'boundary'
  if (PAUSE_KINDS.has(ev.kind)) return 'pause'
  if (ARTIFACT_KINDS.has(ev.kind)) return 'artifact'
  return 'other'
}

/** Classify a streaming `assistant_delta` pending turn. */
export function classifyPending(pt: PendingTurn): KindCategory {
  return pt.kind === 'thinking' ? 'thinking' : 'assistant'
}
