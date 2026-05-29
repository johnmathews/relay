# 2026-05-29 — Run-detail layout Phase 7: accessibility + empty-state polish

Phase 7 of the run-detail layout proposal
(`docs/proposals/run-detail-layout.md` §"Build sequence" item 7 /
§"Accessibility" / §"Empty states") shipped as a single commit on
`main`. 347 → 362 frontend tests (+15 new). Backend untouched. No
SSE / sentinel / schema / dual-list change.

Build-order note: the proposal lists Phase 6 (responsive collapse)
before Phase 7. Per the user call this session, Phase 7 ships first
because it's the higher-value polish on a desktop dev tool that
explicitly disclaims mobile-first behaviour (proposal §Non-goals).
Phase 6 is queued as a follow-up commit.

## Freeze stance

Same call as Phases 1–5 — dashboard-only, no backend / SSE / sentinel
surface, no new event-kind subscription. The MVP-acceptance-testing
freeze is intended for polish that lands without contract change;
ARIA wiring + empty-state copy are exactly that.

## What shipped

- **`frontend/src/components/runs/layout/RunSidebar.vue`** —
  - The Overview button + Iters section are wrapped in a new
    `<div role="listbox" aria-orientation="vertical" aria-label="Run
    views">` (the `sidebar-listbox` testid). Each option-row
    (Overview + each iter row) drops `aria-current="page"` in favour
    of `role="option" aria-selected="true|false"`. The Iters section
    keeps its existing `role="group" aria-labelledby` wiring; that
    grouping inside a listbox is permitted by WAI-ARIA 1.2 (`listbox >
    group > option`).
  - **FileTree stays outside the listbox** as `role="tree"` (its own
    component-owned semantics — load-bearing per CLAUDE.md "wrap, do
    not rewrite" on shared render components). The Children section
    also stays outside the listbox: those rows are RouterLinks, and
    the rail's outer `<nav>` already provides the navigation landmark
    — nesting another nav would be redundant.
  - New `status?: string` prop. When the run isn't terminal and
    `iters.length === 0`, an empty Iters section renders with the
    "Waiting for first iter…" copy (proposal §"Empty states" row 1).
    Terminal-status runs with zero iters collapse the section
    entirely — a "Waiting…" copy on a finished run would be
    misleading.
  - Artifacts section: stops hiding on 404. A 404 (dir not yet
    created) or a 200-with-empty-entries render the section with
    inline copy: **"No artifacts yet"** for non-terminal status, **"—"**
    for terminal (proposal §"Empty states" row 2). Non-404 errors
    (500, network) still collapse the section — the rail has no
    inline error surface; the right-pane FileViewer renders any
    artifact errors on selection.

- **`frontend/src/components/shared/StatusBadge.vue`** — gains
  `aria-label="Run status: ${status}"`. The badge already renders the
  status word as visible text (the visual encoding was never
  colour-only), but a screen reader reading the bare word "running"
  loses the role context. The label clarifies it.

- **`frontend/src/components/runs/RunHealthBadge.vue`** — gains
  `aria-label` driven by a new `ariaLabel` computed. The visible
  compact label ("live · 2s ago") relies on the dot pulse + colour
  for state and uses a non-word duration ("2s"); neither reaches a
  screen reader. The verbose label spells out state + duration:
  `"Live, last activity 0 seconds ago"`, `"Live stream slow, last
  activity 20 seconds ago"`, `"Live stream stalled, last activity
  2 minutes 5 seconds ago"`, `"Live stream connecting"`. The dot
  itself remains `aria-hidden` so the SR isn't double-cued.

- **`frontend/src/components/runs/TimelinePane.vue`** — gains an
  optional `emptyMessage?: string` prop. Default value preserves the
  legacy "No events yet." copy; the empty-state node carries a new
  `data-testid="timeline-empty"` for regression coverage.

- **`frontend/src/components/runs/layout/OverviewPanel.vue` +
  `IterTimelinePanel.vue`** — pass contextualised empty-state copy
  per the proposal §"Empty states" table:
  - Overview body: **"Run hasn't emitted any events yet."**
  - Iter body: **"Iter started — no events yet."**

- **`frontend/src/views/RunDetailView.vue`** — threads
  `detail.status` into RunSidebar so the new empty-state logic has
  the run-status signal it needs.

## Verify-only — already in place from earlier phases

The proposal's Phase 7 checklist named four items that prior phases
had already satisfied; this commit verified each with a regression
test that locks the contract:

- **EventKindFilter** is already `role="toolbar"` with each chip
  `aria-pressed` (EventKindFilter.vue:62–88, Phase 2). The existing
  spec at `EventKindFilter.spec.ts:107–114` already asserts the
  toolbar role; no change needed.
- **Tool-call drawer** already carries `role="dialog"` +
  `aria-modal="true"` + a `useFocusTrap` cycle (Phase 5). The Phase 5
  spec at `ToolCallDetailDrawer.spec.ts:91–92` + the focus-trap suite
  at L263–360 lock both. No regression.
- **Colour is never the sole channel.** StatusBadge renders the
  status word as text, EventKindFilter chips carry text labels,
  timeline rows carry text kind labels. All confirmed by reading
  source; no change needed.
- **Children section hides when empty** (already implemented Phase
  1; covered by `RunSidebar.spec.ts` "hides the CHILDREN section
  when children is empty").

## Empty-state context not implemented — "All events hidden by filter"

The proposal §"Empty states" row 7 names a "Filter excludes all
events" empty state with copy "All events hidden by filter" + a
"Clear filter" button. **This state is unreachable in the shipped
design.** Per Phase 2 (commit `82c8c98`, EventKindFilter.vue:1–14):
*"There is no visibility filter — every step is always rendered. The
chips control per-category expand-by-default state, NOT row
visibility."* The chip row was deliberately merged with the
"Display" popover into a single expand/collapse control. No
combination of chip states hides any row, so no copy is needed.

The proposal table predates the Phase-2 design choice. Recording the
discrepancy here so a future contributor doesn't re-add the empty
state from the proposal alone.

## Deferred — kind-colour contrast

**Resolved 2026-05-29** in
[`260529-kind-colour-contrast-fix.md`](260529-kind-colour-contrast-fix.md).
Option (b) from the menu below — solid bolder hues across both
themes, with tool swapped off amber to teal to preserve the
amber-for-pause-banner reservation. Lowest post-fix ratio is
4.57:1 (light-theme assistant border vs row-bg); the audit
numbers below remain useful as the "before" baseline.

The proposal §Accessibility nominates a WCAG contrast spot-check on
each kind colour against the row background. I computed real ratios
for both themes against both surfaces; numbers below. Every kind
border fails the WCAG 1.4.11 non-text contrast bar (3:1) and would
also fail the 4.5:1 text bar the proposal cites:

```
DARK  THEME (--color-surface = #181b21)
  assistant  border vs surface = 2.40:1   border vs row-bg = 2.31:1
  thinking   border vs surface = 2.30:1   border vs row-bg = 2.21:1
  tool       border vs surface = 2.72:1   border vs row-bg = 2.62:1
  signal     border vs surface = 3.02:1   border vs row-bg = 2.87:1
  other      border vs surface = 1.95:1   border vs row-bg = 1.92:1

LIGHT THEME (--color-surface = #ffffff)
  assistant  border vs surface = 2.54:1   border vs row-bg = 2.34:1
  thinking   border vs surface = 2.72:1   border vs row-bg = 2.48:1
  tool       border vs surface = 1.92:1   border vs row-bg = 1.79:1
  signal     border vs surface = 1.74:1   border vs row-bg = 1.59:1
  other      border vs surface = 2.56:1   border vs row-bg = 2.34:1
```

Fixing this is a multi-token visual rebalance across 5 kinds × 2
themes that affects 3+ components (chip dots, row left borders,
card header strips) and needs design review — it's independent of
the structural ARIA work and bundling them lockstep risks a
silent visual regression that the structural tests can't catch.

Documented in `frontend/src/styles/base.css` next to the row
tokens. Picking it up as a separate piece would mean either (a)
bumping opacity on the dark-theme tokens to ~0.80, (b) switching to
solid bolder hues across both themes, or (c) accepting non-text
contrast at this level given the localhost dev-tool context (ADR-12,
proposal §Non-goals "Mobile-first" and "narrow viewports degrade
gracefully"). Decision belongs with the user, not this commit.

## Why the listbox doesn't span the full rail

The proposal text reads "Left rail is role=listbox with
aria-orientation=vertical. Each row is role=option …". Taken
literally that scopes the listbox to the entire rail. In practice
the rail mixes three widget kinds:

1. Selection rows (Overview + iters) — single-select within the
   master pane, classic listbox semantics.
2. **FileTree** under Artifacts — its own hierarchical `role="tree"`
   semantics, written for the Phase 1 file-browser refactor (W7).
   Re-skinning it as a flat listbox would lose folder-expand
   semantics and the `treeitem` cues.
3. **RouterLink children** — navigate to a different `/runs/<id>`
   route. They aren't selecting a view within the current right
   pane; they're navigating away. The link idiom is correct;
   listbox-option idiom is not.

The implementation scopes the listbox tightly to widget #1 — the
Overview + Iters region inside a `<div role="listbox">` — and
leaves widgets #2 and #3 outside under the rail's outer `<nav>`
landmark. This was confirmed with the user before implementation.

## What did NOT change

- Backend, REST surface, SSE wire shape, OTel pipeline, sentinel
  grammar.
- Event store invariants (single source of truth, append-only,
  envelope shape, `Last-Event-ID` replay).
- Dual-list contract: `KNOWN_EVENT_TYPES` × `INVALIDATING_KINDS` —
  no new event-kind subscription this phase.
- `PauseAnswerForm.vue` (14c/14e/14f review-paths tabs + diff
  toggle).
- `ToolCallDetailDrawer.vue` (Phase 5) — verify-only.
- `EventKindFilter.vue` (Phase 2) — verify-only.
- Children section's existing hide-when-empty behaviour.
- Token values for `--color-row-*` in `base.css` — only a comment
  was added documenting the contrast audit.

## Gate

`npm run check` green (eslint + vue-tsc + vitest), 362 tests
passing (was 347; +15 new across StatusBadge, RunHealthBadge,
RunSidebar, TimelinePane). Backend untouched (371 tests, 95%
coverage, ruff/mypy --strict).

The amber-fix from the earlier sweep (`#e0b341` reserved for
human-attention affordances) is preserved — no new use of amber in
this commit. The pause banner remains the sole carrier.

## Manual-test punch list (for the next acceptance pass)

- VoiceOver / NVDA: open a paused run, tab through the rail; confirm
  "Run views, listbox" → "Overview, selected, 1 of N" cadence.
- Confirm the run-health badge announcement reads as "Live, last
  activity 12 seconds ago" without doubled "live live" cue from the
  dot.
- A run mid-launch (between `run_started` and `iter_started`)
  shows "Waiting for first iter…" in the rail.
- A finished run with zero artifacts shows "—" under the Artifacts
  heading (rather than hiding the section).
