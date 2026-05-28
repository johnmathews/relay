/**
 * URL-reflected selection state for `RunDetailView`. The right pane
 * routes its body on this discriminated union; the left rail
 * highlights the matching row.
 *
 * Serialised to/from `?view=` in the URL — see {@link parseView} and
 * {@link serializeView}. Absent `?view=` resolves to {@link smartDefault}.
 */
export type RunView =
  | { kind: 'overview' }
  | { kind: 'iter'; seq: number }
  | { kind: 'artifact'; path: string }

type QueryShape = Record<string, string | string[] | null | undefined | (string | null)[]>

function firstOf(v: string | string[] | null | undefined | (string | null)[]): string | null {
  if (v == null) return null
  if (Array.isArray(v)) {
    const first = v[0]
    return first ?? null
  }
  return v
}

/**
 * Parse `?view=…` into a {@link RunView}. Returns `null` when absent or
 * malformed — callers fall back to {@link smartDefault}. NEVER throws.
 */
export function parseView(query: QueryShape): RunView | null {
  const raw = firstOf(query.view)
  if (raw == null || raw === '') return null

  if (raw === 'overview') return { kind: 'overview' }

  if (raw.startsWith('iter:')) {
    const tail = raw.slice('iter:'.length)
    if (tail === '') return null
    if (!/^\d+$/.test(tail)) return null
    const seq = Number(tail)
    if (!Number.isInteger(seq) || seq < 1) return null
    return { kind: 'iter', seq }
  }

  if (raw.startsWith('artifact:')) {
    const tail = raw.slice('artifact:'.length)
    if (tail === '') return null
    let path: string
    try {
      path = decodeURIComponent(tail)
    } catch {
      return null
    }
    if (path === '') return null
    return { kind: 'artifact', path }
  }

  return null
}

/**
 * Serialise a {@link RunView} into the `?view=` form. Inverse of
 * {@link parseView}.
 */
export function serializeView(view: RunView): string {
  switch (view.kind) {
    case 'overview':
      return 'overview'
    case 'iter':
      return `iter:${view.seq}`
    case 'artifact':
      return `artifact:${encodeURIComponent(view.path)}`
  }
}

/**
 * Compute the default {@link RunView} when `?view=` is absent. Driven
 * by run status:
 *
 *   - `paused`              → `artifact:<first reviewPath>` if any, else `overview`
 *   - `running` / `awaiting_children` → latest iter, else `overview`
 *   - terminal (`done` / `failed` / `cancelled`) → `overview`
 *
 * The paused branch opens the file the operator is being asked to review
 * next to the PauseAnswerForm (ADR-40/41 reviewable pauses); when no
 * `review_paths` were declared on the paused iter (every pre-14b run,
 * and any 14b+ pause without the attribute), `overview` is the fallback.
 * Callers pass `reviewPaths` from `RunDetailView`'s `pauseReviewPaths`
 * computed — it already handles the 14a–14d scalar `review_path`
 * migration fallback.
 */
export function smartDefault(detail: {
  status: string
  iters: ReadonlyArray<{ seq: number }>
  reviewPaths?: ReadonlyArray<string>
}): RunView {
  if (detail.status === 'paused') {
    const first = detail.reviewPaths?.[0]
    if (first != null && first !== '') return { kind: 'artifact', path: first }
    return { kind: 'overview' }
  }
  if (detail.status === 'running' || detail.status === 'awaiting_children') {
    const latest = detail.iters[detail.iters.length - 1]
    if (latest != null) return { kind: 'iter', seq: latest.seq }
    return { kind: 'overview' }
  }
  return { kind: 'overview' }
}
