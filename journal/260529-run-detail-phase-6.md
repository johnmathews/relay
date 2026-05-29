# 2026-05-29 — Run-detail layout Phase 6: responsive rail-dropdown collapse

Phase 6 of the run-detail layout proposal
(`docs/proposals/run-detail-layout.md` §"Layout" responsive paragraph)
shipped as a single commit on `main`. 362 → 375 frontend tests (+13
new across two new specs). Backend untouched. No SSE / sentinel /
schema / dual-list change.

Build-order note: shipped immediately after Phase 7 in the same
session, per the user's two-commit plan (Phase 7 then Phase 6). The
proposal lists Phase 6 first in §"Build sequence", but the structural
a11y bundle was higher value to ship first.

## Freeze stance

Same call as Phases 1–5/7 — dashboard-only, no backend / SSE /
sentinel surface, no new event-kind subscription. Responsive collapse
is a smaller polish than Phase 7 was (the proposal §Non-goals
explicitly disclaims mobile-first), but it closes a documented item
in the build sequence and brings the rail in line with the
master-detail conventions the layout cites (Mail.app on iPad in
portrait).

## What shipped

- **New `frontend/src/composables/useViewportBreakpoint.ts`** — small
  hand-rolled composable (~40 LOC) wrapping `window.matchMedia(query)`
  in a reactive `Ref<boolean>`. Adds the `change` listener on the
  modern `MediaQueryList.addEventListener` API; removes it in
  `onBeforeUnmount`. Mirrors the Phase 5 `useFocusTrap` choice not to
  pull in @vueuse for a targeted 40-LOC need. SSR-guarded (returns a
  static `false` if `window.matchMedia` is unavailable) even though
  every entry mounts in the browser today — keeps the import safe to
  reuse.

- **`frontend/src/components/runs/layout/RunSidebar.vue`** —
  - `useViewportBreakpoint('(max-width: 899px)')` drives a new
    `isNarrow` reactive flag. The `899px` matches the existing CSS
    `@media (max-width: 899px)` in `RunDetailView.vue` (which flips
    the grid to 1-col so the rail stacks above the right pane);
    keeping the JS + CSS breakpoints aligned avoids the 1px gap where
    only one side has flipped.
  - When `isNarrow`, the rail renders a top selector button
    (`sidebar-narrow-selector`) showing the currently-selected view
    label + a caret. The rest of the rail body (listbox, Artifacts,
    Children) lives under a wrapper `<div id="run-sidebar-body">`
    that toggles the `[hidden]` attribute. `aria-expanded` +
    `aria-controls` wire the selector to the body so screen readers
    can navigate the disclosure widget canonically.
  - Tapping the selector toggles `isExpanded`; **selecting any row
    auto-collapses** (`collapseAfterSelect()` is called inside
    `selectOverview` / `selectIter` / `onArtifactSelect`). The right
    pane reclaims the viewport immediately on selection — that's the
    Mail-on-iPad-portrait pattern. Children RouterLinks don't need
    auto-collapse because they navigate away to a different
    `/runs/<id>`, unmounting the entire RunDetailView tree.
  - A `watch(isNarrow, …)` clears `isExpanded` when the viewport
    crosses back into desktop. Re-narrowing later therefore starts
    in the collapsed state — consistent with first-mount behaviour.
  - Selected-view label format:
    - `overview` → `"Overview"`
    - `iter` → `"Iter #N"` (no phase suffix; the selector strip is
      tight)
    - `artifact` → `"Artifact · <path>"` (a `·` separator rather than
      `:` because the path can contain `:` characters in theory and
      the visual hierarchy reads better)
    The label uses `text-overflow: ellipsis` so long artifact paths
    truncate gracefully.

- **`frontend/src/views/RunDetailView.vue`** — comment updated to
  reflect Phase 6's ship status. The grid CSS itself didn't need to
  change: the existing `@media (max-width: 899px) { grid-template-
  columns: 1fr; }` already stacks the rail above the pane; Phase 6
  just makes the stacked rail much shorter (a one-row selector strip)
  rather than a full-height inert section above the body.

## Drawer at narrow widths — verify-only

The proposal's drawer callout flagged that the existing
`ToolCallDetailDrawer.vue` has `width: 50vw` / `min-width: 320px` /
`max-width: 100vw`. Audited:
- At 900px viewport: 50vw = 450px (within bounds).
- At 320px viewport: 50vw = 160px → clamped to `min-width: 320px` =
  full viewport. The drawer covers the entire narrow viewport at
  the smallest sizes, which is the expected mobile drawer pattern.

No drawer change needed. The proposal call (drawer "probably needs no
Phase 6 change but verify") was correct.

## Per-pane scroll story — NOT implemented

The Phase 4 journal flagged: *".right-pane is NOT a scroll container
today — Phase 6 is the natural home for the per-pane scroll story IF
the responsive layout genuinely needs it."*

It doesn't. Below 900px the layout is fully stacked (one column, page
scroll); a per-pane scroll container would create a nested scroll
inside a scroll, which is precisely the UX the per-pane scroll story
would have aimed to avoid. Above 900px the original two-column
layout sets its own scroll behaviour per-pane via `min-height: 100%`
+ overflow on the children. No `.right-pane` reshape this phase.

If a future visual smoke shows the sticky pause banner detaches
during a long page scroll on a tall right pane, that's the trigger to
revisit — but with the current event volumes the natural page scroll
is fine.

## jsdom contract — matchMedia stubbing

Phase 6 is the first part of the run-detail layout that depends on
`window.matchMedia`. jsdom 22+ provides a stub that always returns
`matches: false` — i.e. the desktop path. Tests that exercise the
narrow path must stub `window.matchMedia` before mounting; the new
`useViewportBreakpoint.spec.ts` includes a `makeMqlStub` helper that
returns a controllable `MediaQueryList`-shaped object, and the
RunSidebar narrow-mode tests reuse the same shape.

The stub supports `.fire(matches)` to simulate a window resize
crossing the breakpoint mid-test — that's the mechanism behind the
"resizing back to wide drops the expanded flag" regression test.

## What did NOT change

- Backend, REST surface, SSE wire shape, OTel pipeline, sentinel
  grammar.
- Event store invariants.
- Dual-list contract.
- `PauseAnswerForm.vue`, `ToolCallDetailDrawer.vue`,
  `EventKindFilter.vue`, `RunHealthBadge.vue`, `StatusBadge.vue`.
- The Phase 7 listbox semantics — `role="listbox"` on the
  Overview+Iters region still applies in narrow mode; it's just
  hidden under the disclosure widget until the user expands.
- The CSS breakpoint at 899px in `RunDetailView.vue` — Phase 6
  keeps the JS and CSS in lockstep at the same value.

## Gate

`npm run check` green (eslint + vue-tsc + vitest), 375 tests passing
(was 362; +13 new across `useViewportBreakpoint.spec.ts` and
`RunSidebar.spec.ts` narrow + wide describes). Backend untouched
(371 tests, 95% coverage, ruff/mypy --strict).

The amber `#e0b341` reservation is preserved — no new use of amber.

## Manual-test punch list (for the next acceptance pass)

- Resize a browser window from 1200px → 600px on the run-detail
  view; confirm the rail collapses into the selector strip and the
  right pane reclaims the freed space.
- Tap the selector at 600px; confirm the listbox + Artifacts +
  Children sections slide down.
- Tap any iter row at 600px; confirm the body re-collapses and the
  right pane scrolls to show the new iter's timeline.
- VoiceOver / NVDA at 600px: confirm the selector reads as a
  disclosure button ("Overview, collapsed, button") and the
  expanded state announces correctly.
- Resize from 600px → 1200px while expanded; confirm the wide rail
  returns and the expanded flag drops.
