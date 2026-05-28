/**
 * Event-kind categories surfaced by `EventKindFilter` (Phase 2 of the
 * run-detail layout proposal). Each relay event kind maps to one of
 * five categories so the timeline filter is a small, scannable chip
 * row rather than 11+ raw-kind toggles.
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
 * URL contract: `?kinds=tool,signal` (comma-separated subset of
 * {@link KIND_CATEGORIES}). Absent param = all visible. Unknown tokens
 * are dropped silently.
 */

import type { PendingTurn, StreamEvent } from '@/stores/events'

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
 * Parse `?kinds=…` into a set of allowed categories. Absent / empty /
 * malformed → `null` (callers treat null as "all visible"). Unknown
 * tokens are dropped; if the result is a non-empty proper subset of
 * {@link KIND_CATEGORIES} we return it, otherwise null (all-on and
 * all-off both fall back to the absent-param semantics — only a proper
 * subset is a meaningful filter).
 */
export function parseKinds(
  query: Record<string, string | string[] | null | undefined | (string | null)[]>,
): ReadonlySet<KindCategory> | null {
  const raw = query.kinds
  const first = Array.isArray(raw) ? raw[0] : raw
  if (first == null || first === '') return null
  const allowed = new Set<KindCategory>()
  for (const tok of first.split(',')) {
    const t = tok.trim() as KindCategory
    if (KIND_CATEGORIES.includes(t)) allowed.add(t)
  }
  if (allowed.size === 0) return null
  if (allowed.size === KIND_CATEGORIES.length) return null
  return allowed
}

/**
 * Serialise a kinds set to the `?kinds=` form. `null` (or the full
 * set) → `undefined` so the caller can spread it into a `router.push`
 * query and the param drops out of the URL entirely. Preserves the
 * canonical {@link KIND_CATEGORIES} order so URLs are stable across
 * chip-click orderings.
 */
export function serializeKinds(
  kinds: ReadonlySet<KindCategory> | null,
): string | undefined {
  if (kinds == null) return undefined
  if (kinds.size === 0) return undefined
  if (kinds.size === KIND_CATEGORIES.length) return undefined
  return KIND_CATEGORIES.filter((k) => kinds.has(k)).join(',')
}
