# 260528 — step-card polish sweep (4th round)

Fourth polish round on the run-detail timeline today, finishing the
step-card unification that the last three rounds opened. The user
opened the session as `/engineering-team` in Discussion mode — surface
candidates first, pick scope, then build — and after a Playwright
walk of the security-audit run (`20260525-160758-11ce`, the
paused-then-recovered evaluation run) chose **option A: step-card
polish sweep, all three items**:

1. Drop the inner `ToolCallCard` / `SignalCard` chrome
2. Fold `usage` + `artifact_edited` into the step-card structure
3. Strengthen the collapsed-row hover affordance

The proposal doc (`docs/proposals/run-detail-layout.md`) was
explicitly left as a parallel candidate (Phase 3: Follow-live pin +
smart-default + keyboard nav) but parked — the polish gaps were
visible eyesores in the live run and shipping them keeps the
surface during the MVP-acceptance-testing freeze contained.

## What landed (3 commits on `main`)

### `e546ce8` — drop inner ToolCallCard / SignalCard chrome

When a tool / signal row was expanded inside the timeline step-card
body, the inner `ToolCallCard` and `SignalCard` rendered their own
outer border + background + head row — producing visible card-in-card
nesting because the step-card already supplied the container chrome
plus the kind / name in its header.

Added an `embedded` boolean prop on both components. When set:

- The outer border + background + padding disappear.
- The head row (`tool-card__head` with name + duration / `signal-card__head`
  with tag + kind) is hidden — the step-card header already shows
  these. For `SignalCard`, the head collapses to a single
  right-aligned anchor link so `#signal-<seq>` deep links still
  resolve.
- For `ToolCallCard`, the inner `<pre>` blocks also drop their
  border + background. The step-card's body is the visual container;
  the blocks keep monospace font, scroll behaviour, and a small
  inset between `args` and `result` sections so they read as code
  rather than running into the body padding.

`TimelinePane.vue` passes `embedded` when rendering inside the
step-card body. Standalone callers (none today outside the timeline)
are unaffected. `data-testid` and existing tests preserved — the
embedded variant is a CSS-only narrowing of the same rendered
surface.

### `f8ab95c` — fold usage + artifact_edited into step-card chrome

`usage` (harness_session_ended totals, ADR-39) and `artifact_edited`
(14a/14e in-pause edits) were the last one-liners still rendering in
the legacy inline layout:

- `usage` via `UsageRow`'s own `border-left: 2px solid var(--color-border)`
  + padding, no surrounding step-card.
- `artifact_edited` via a `.timeline__edit` `<button>` with a left
  border accent.

Both now render as single-line step-cards with per-type pastel
border + surface, matching the chrome the collapsed / expanded rows
adopted three rounds back. Mechanism:

- New `.timeline__card-header--inline` modifier on the existing
  card-header layout, applied via `<component :is>` so the wrapping
  element switches between `<button>` and `<div>` based on whether
  the row is interactive.
- Per-type CSS applied to the outer `.timeline__row` for
  `data-row-type='usage'` (zinc — matches the "Other" chip; reuses
  `--color-row-other-{bg,border}`) and `data-row-type='artifact_edited'`
  (amber — matches the warning palette so the human-attention
  affordance still reads as such; reuses `--color-warning{,-bg}`).
- Single line: `#seq · glyph · content · spacer · [⧉ Copy]`.

`artifact_edited` keeps its full 14e click-to-navigate behaviour
(opens the file in the right pane + scrolls the sidebar's Artifacts
section into view). The wrapping element switches from `<button>`
to `<div>` when `runId` is unset so the row is non-interactive in
older test mounts; the `data-testid="artifact-edited-row"` is
preserved on both variants. `UsageRow`'s own `.usage-row` styling
is overridden via `:deep()` inside the inline card-header so the
stop-reason badge + token totals sit flush next to the `Σ` glyph
instead of carrying their own border-left + padding.

The legacy `.timeline__edit` button class + its hover / focus
styles were removed (no element uses them anymore); the
`__edit-path` / `__edit-sha` / `__edit-editor` text utilities are
kept and re-used inside the new inline card-header. `boundary` and
`pause` rows keep their existing inline layout — their fenced
metadata-block treatment isn't worth restructuring into a step-card
shape.

### `5abd8c3` — strengthen collapsed-row hover affordance

The collapsed-row hover only tinted the header background to
`--color-surface-hover` — nearly invisible against the per-type
pastel surface. Users couldn't see whether the row was a click
target before reaching the inner Copy / Expand buttons.

Hover now lifts the whole collapsed card:

- Border shifts to `--color-border-strong` (a neutral strong tone
  that contrasts against every per-type tint — keeps the chrome
  consistent across assistant / thinking / tool / signal / generic).
- Soft `0 2px 6px var(--color-shadow)` sits below the row.
- `translateY(-1px)` pops the row up.
- 120ms ease-out transition on all three so the affordance reads
  as a deliberate lift, not a flash.

Applied to `.timeline__row--card.timeline__row--collapsed:hover`
and to the clickable inline-card variant (`artifact_edited` with a
runId) via `:has(button.timeline__card-header--inline)`.

Expanded cards keep the existing behaviour — the open body and the
Collapse button already advertise interactivity. The old
`.timeline__card-header:hover { background: var(--color-surface-hover) }`
rule was dropped because it double-applied with the row-level hover
and produced a competing inner highlight on the header strip; the
selector is reduced to a `focus-visible` no-outline rule.

Verified via `getComputedStyle` on a Playwright-hovered thinking
row: `transform = matrix(1,0,0,1,0,-1)`, `box-shadow = rgba(0,0,0,.35) 0 2px 6px`,
border = strong neutral. A static screenshot didn't visibly capture
the lift (the 120ms transition + 1px translate is subtle in a still
frame); the live behaviour matches the spec.

## Traps + things I'd re-step on

- **`<component :is>` for click-target conditionality.** The
  artifact_edited row needs to be a real `<button>` when interactive
  (keyboard activation + a11y) but a `<div>` when not (the older
  test mounts don't set `runId`). Wrote it as
  `<component :is="row.type === 'artifact_edited' && runId ? 'button' : 'div'">`
  rather than two `v-if` branches because the inner content + the
  styling rules are identical — duplicating the markup would have
  drifted within a round. The `:type` attribute is conditional too
  (`<div type="button">` would render the attribute as a noop;
  `:type="... ? 'button' : undefined"` keeps it off the div).
- **`UsageRow`'s `:deep()` override.** The component already lives
  in the timeline ecosystem so wiring an `embedded` prop in
  symmetry with ToolCallCard / SignalCard would have been the
  symmetrical move — but it's only used in one place and the
  `:deep()` override is two CSS rules, which is honestly less code
  than threading a prop through. Symmetry isn't free if the cost
  is more code; took the smaller patch.
- **Hover `:has()` selector for the clickable inline variant.**
  Used `:has(button.timeline__card-header--inline)` to apply the
  same lift to artifact_edited rows that have a runId (and thus
  render as a `<button>`). `:has()` has full support in the
  target browsers (latest Chromium/Firefox/Safari) so the
  conditional doesn't need a class-based fallback. If we ever
  need to keep the lift in older browsers, swap to a
  `.timeline__row--clickable` class set by the template.
- **Playwright static screenshots miss transitions.** The
  third commit's verification needed `getComputedStyle` via
  `browser_evaluate` because the hover screenshot showed a row
  visually identical to its neighbours — the 120ms ease-out
  hadn't completed in time. Worth remembering: trust the
  computed-style probe over the visual diff when a transition
  is in play.

## Verification

- `npm run check` clean after each commit: 36 test files, 303
  tests passing. ruff + mypy untouched (frontend-only sweep).
- Playwright walk of the security-audit run (`20260525-160758-11ce`,
  paused-then-recovered through `pause-for-input` at iter #2). Verified:
  - Expanded tool cards now render args / result flush against the
    step-card body — no nested darker "RESULT" box.
  - `#308 usage` (CANCELLED ∑ in 0 · out 0 · cache r 0 / w 0)
    renders as a step-card matching #306 `pause` / #307 `phase_start`
    chrome, with the `Σ` glyph + Copy button on the right.
  - `boundary` rows (`#309 ITER_ENDED`) intentionally retain the
    legacy inline layout — out of scope this round.
  - Hover on a collapsed thinking row applies `translateY(-1px)`,
    soft shadow, and strong-neutral border — confirmed via
    `getComputedStyle`.
- Both light + dark themes render correctly; the per-type pastel +
  the new hover-strong border read clearly in both.

## Files touched

- `frontend/src/components/runs/ToolCallCard.vue` — `embedded` prop,
  conditional `head` row, embedded variant CSS that strips outer
  chrome + flattens inner `<pre>` blocks.
- `frontend/src/components/runs/SignalCard.vue` — `embedded` prop,
  conditional tag / kind in head, embedded variant CSS that strips
  outer chrome + collapses head to a right-aligned anchor.
- `frontend/src/components/runs/TimelinePane.vue`:
  - Pass `embedded` when rendering inside the step-card body.
  - New `<template v-else-if="row.type === 'usage' || row.type === 'artifact_edited'">`
    branch with the inline card-header layout.
  - New `.timeline__card-header--inline` modifier (button + div
    variants).
  - Per-type palette extended to `data-row-type='usage'` and
    `data-row-type='artifact_edited'`.
  - `UsageRow` `:deep()` override.
  - Removed legacy `.timeline__edit` button class + hover / focus.
  - Hover-lift rule + 120ms transition on the collapsed-card
    variant and the clickable inline-card variant.
- `docs/dashboard.md` — Inline-rows section rewritten; new
  Embedded card bodies + Hover affordance subsections; per-type
  palette table extended with usage + artifact_edited.

No new tests added — the changes are CSS + structural narrowing
inside an existing rendered surface; the existing 303-test gate
covers the contract (data-testids, interactivity, rendered text)
that any consumer can reach.
