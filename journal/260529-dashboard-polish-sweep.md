# 260529 — dashboard polish sweep

Six UI improvements landed in one branch (`feat/dashboard-polish`),
batched into four logical commits behind a single PR. Driven by
operator feedback on the running dashboard during
MVP-acceptance-testing — these surfaced from actually using the app,
not from a feature spec. Treated as acceptance-testing-driven fixes
rather than new feature work despite the codebase being in the
"feature work parked" phase: the testing phase exists exactly to
produce this kind of feedback.

## What changed

**Batch 1 — layout polish + intro panel.**

- `HubView.vue` and `ProjectView.vue` now centre with `margin: 0
  auto` on top of the existing `max-width` (1100 / 1200 px). The
  content was left-pinned before; on wide monitors that left a sea
  of dead space on the right.
- New `HomeIntroPanel.vue` rendered below the project grid on the
  hub page. Three short cards (What is Relay? / How it works / The
  engineering-team skill) — first-time-visitor framing that
  explicitly names the bundled engineering-team skill. The previous
  hub gave no hint that the skill was the canonical entry point.
- `ProjectView`'s "Unregister" button → **"Remove project"** with
  a `title` tooltip: "Remove project from relay. Files on disk
  will not be changed. Custom prompts will be lost." Confirm-dialog
  copy tightened to match. `data-testid`s left as-is so existing
  selectors keep working; the spec.ts assertion that checked
  "does NOT delete any files on disk" was updated to assert the
  new tightened copy ("Files on disk will not be changed").

**Batch 2 — "Other" event kinds become collapsible cards.**

The complaint: `iter_started` (and other structural events:
`iter_ended`, `run_started`, `run_ended`) rendered as inline blobs
of one-line JSON with no colour and no anchor. They were
visually the quietest row type despite being the most
load-bearing for understanding "where am I in the flow?".

- Promoted `boundary` from `inline` to `card` chrome — same
  expand/collapse pattern as tool/signal/etc., same `prefs`
  bucket, same chip-row participation.
- Added `boundary` to `TimelineRowType` + `DEFAULTS` in
  `timelinePrefs.ts` and to `COLLAPSIBLE_TYPES` in `TimelinePane`.
- Glyph: `◆`. Header name: kind with `_` → space (e.g.
  `iter_started` → `iter started`).
- Smart preview tailored to the kind: `seq=5 phase=wrap-up` for
  iter boundaries; `status=done reason=…` for run boundaries.
  Falls back to stringified payload when neither populated.
- Body: new `prettyJson()` helper renders indented JSON in a
  `.timeline__bmeta--pretty` `<pre><code>` block (whitespace
  preserved, monospace, soft surface). Single-line `generic()` is
  still used for previews; `prettyJson()` is the body-only path.
- Slate colour palette via the existing
  `--color-row-other-{bg,border}` tokens — shared with `generic`.
- The legacy `timeline__boundary` template branch is gone; only
  `pause` keeps the inline layout (amber chrome is a deliberate
  human-attention affordance that needs to stand out from the
  surrounding card grid).

**Batch 3 — group consecutive tool calls.**

The complaint: a Phase-3 development iter routinely has 50+
adjacent bash/read/edit calls and the operator just scrolls past
them.

- New `displayRows` computed walks `rows` accumulating streaks of
  adjacent `tool` rows. Streaks of size ≥ `GROUP_MIN (= 2)` either
  render as one anchor row (collapsed, the default) or all rows
  preceded by a `▾ Collapse group` chip (expanded).
- Streak-break rules: any non-tool row breaks the streak. The
  iter-boundary kinds (now also `tool`-incompatible since they're
  `boundary`-typed) implicitly break the streak too.
- Anchor row reads `#<first-seq> ⚒ <N> tool calls · bash, read,
  edit +M more` (first three distinct names + count overflow).
  Hand-formatted via `formatGroupNames()` with `GROUP_NAMES_LIMIT
  = 3`.
- Expand-state set is component-local (`Set<string>` keyed by the
  first row's `Row.key`). Resets on remount — same lifetime as the
  per-row expand override map. Not persisted: a group is a render
  concern, not a preference.
- Virtualisation switched from `rows` to `displayRows` for the
  windowing math + `visibleItems` slice. A 717-event run with one
  giant tool burst becomes a single tall display row and does NOT
  blow the height calculation.
- The template iteration is now a discriminated union
  (`group-anchor | row`). A `<template v-for="row in [item.row]">`
  inside the `<li v-else>` introduces a local `row` alias so the
  existing row-rendering markup reads unchanged. Pragmatic — avoids
  renaming every `row.kind` / `row.event` in the body.

**Batch 4 — timeline minimap (sibling column).**

The 717-event run was the trigger. Just scrolling through 717
steps is too much; the operator wanted a VS Code-style overview.

- New `TimelineMinimap.vue` rendered as a sibling column inside
  `TimelinePane` (`.timeline-pane__minimap`, 22 px fixed, height
  matches the timeline scroller's `max-height: 70vh`).
- One coloured tick per pre-grouping row, positioned linearly via
  `top: <i/N * 100>%`. Colours via `data-row-type` on each tick,
  reusing the existing `--color-row-*-border` palette so the
  minimap and the row cards share a colour language. Tool bursts
  are teal bands, thinking phases purple, signals green, boundary
  / generic slate, artifact-edits / pause amber.
- Viewport overlay: translucent rect, position + height computed
  from `scrollTop / scrollHeight` and `viewportH / scrollHeight`,
  with a 60ms linear transition for smooth scroll feedback. The
  mapping is linear — accurate under the >1000-row virtualisation
  path (uniform `ROW_HEIGHT = 88`), an approximation otherwise.
  Acceptable for a shape indicator.
- Click anywhere on the strip to scroll the timeline so that
  position is centred in the viewport; drag to scrub. Pointer
  capture is set on `pointerdown` and released on `pointerup`,
  so the drag tracks even after the cursor leaves the strip.
- Communication: the minimap emits `scroll-to(px)`, the parent
  applies it via `scrollToPixel(px)` (clamped to
  `[0, scrollHeight - clientHeight]`). The auto-follow pin is
  unaffected — programmatic scroll passes through the existing
  `onScroll` handler and unsticks the pin like any manual scroll.
- `scrollHeight` is a new ref refreshed in `onScroll` AND in the
  `watch(rows.length)` post-tick. The post-tick refresh is
  load-bearing for live streams — without it the viewport overlay
  would lag a frame behind appended rows.
- Hidden when `minimapTicks.length === 0` so empty runs don't have
  a dead column.

## Cross-cutting fix learned along the way

A first cut at the grouping tests asserted `[data-row-type="tool"]`
DOM presence — and failed once the minimap landed, because the
minimap's ticks also carry `data-row-type`. The selector was
narrowed to `.timeline__row[data-row-type="tool"]`. Lesson: row-
type is now a cross-component concern (rows AND ticks share the
attribute by design — that's how they share a colour palette), so
test selectors need to anchor on the row class too.

A second snag: the existing "renders a smart per-tool preview"
test exercised three adjacent tool rows, which Batch 3 now
collapses into a group. Updated the test to first click the
group anchor's header (not the `<li>` — the click handler is on
the inner `<header>`) to expand the group, then assert per-row
previews. That tests both behaviours in one path.

A third snag: PointerEvent isn't constructable in jsdom and Vue
Test Utils' `trigger('pointerdown', { clientY: … })` can't write
`clientY` (read-only on MouseEvent). The minimap's pointer test
synthesises a `MouseEvent('pointerdown', { clientY, bubbles })`,
adds `pointerId` via `Object.assign`, and dispatches it directly
on the element with stubbed `setPointerCapture` /
`releasePointerCapture`.

## What did NOT change

- `pause` rows are still the legacy inline layout. Amber chrome
  is intentional human-attention design (memory:
  `yellow-pause-borders-validated`).
- The `Signals` chip still groups all signal-like kinds for its
  count + expand-default behaviour, including the boundary
  kinds. That mapping pre-dates Batch 2 and the user did not flag
  it — leaving it as-is.
- No backend change. No spec / ADR change. Pure frontend
  feature work driven by acceptance-testing feedback.

## Post-review fixes (same session)

A code-reviewer pass on top of the four batches flagged two real
issues, both now fixed:

1. **Minimap tick positioning off-by-one.** The first cut used
   `top: i / N * 100%` which places the last tick at `(N - 1) / N
   * 100%` — for N = 5 that's 80%, leaving an empty 20% band at
   the bottom of the strip. Corrected to `top: i / (N - 1) *
   100%` with a single-tick guard returning 0% (avoids
   divide-by-zero). The original test `TimelineMinimap.spec.ts`
   was confirming the wrong formula; updated to expect 0/25/50/75/100%
   for a 5-tick strip and added a single-tick edge-case test.
2. **Minimap ARIA role was misleading.** The first cut had
   `role="slider"` with `aria-valuemin`/`aria-valuemax`/`aria-valuenow`
   but NO `@keydown` handlers — so screen readers would announce
   "slider, adjust to scroll" but arrow keys did nothing (WCAG 2.1
   SC 2.1.1 violation). Resolved to `aria-hidden="true"` with no
   role: the coloured-band pattern is meaningless without sight,
   keyboard users access the timeline via its own focusable
   scroll container, and exposing a competing non-operable
   control in the accessibility tree would actively harm AT users.
   `docs/dashboard.md`'s minimap section gained an explicit
   accessibility note + a tick-math note covering the `(N - 1)`
   denominator.

## Testing + gate

- 391 frontend tests pass (added: 1 intro-panel, 1 boundary card,
  5 tool-grouping, 3 minimap-in-timeline + 6 minimap-unit = 16
  new tests across `tests/HubView.spec.ts`,
  `tests/ProjectView.spec.ts` (one updated assertion),
  `tests/TimelinePane.spec.ts`, `tests/TimelineMinimap.spec.ts`).
- 371 backend tests still pass; ruff + mypy --strict clean.
- Frontend full gate (`npm run check`) green: eslint
  `--max-warnings 0`, vue-tsc, vitest.

## Files touched

| File | Change |
|---|---|
| `frontend/src/views/HubView.vue` | centre + render `HomeIntroPanel` |
| `frontend/src/components/projects/HomeIntroPanel.vue` | new — three-card intro |
| `frontend/src/views/ProjectView.vue` | centre, rename button, tooltip, confirm copy |
| `frontend/src/stores/timelinePrefs.ts` | add `boundary` to `TimelineRowType` + `DEFAULTS` |
| `frontend/src/components/runs/TimelinePane.vue` | boundary → card; tool grouping; minimap mount; sibling-column layout |
| `frontend/src/components/runs/TimelineMinimap.vue` | new — VS-Code-style overview |
| `docs/dashboard.md` | document Batch 1–4 |
| `frontend/tests/HubView.spec.ts` | add intro-panel smoke |
| `frontend/tests/ProjectView.spec.ts` | update confirm-copy assertion |
| `frontend/tests/TimelinePane.spec.ts` | boundary card test + grouping suite + minimap-in-timeline tests + smart-preview test fix |
| `frontend/tests/TimelineMinimap.spec.ts` | new — 5 unit tests for tick math, viewport overlay, pointer interaction |
