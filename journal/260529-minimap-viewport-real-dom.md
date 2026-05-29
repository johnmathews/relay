# 2026-05-29 — Minimap viewport overlay: real DOM measurement

Bug-fix session, shipped on top of an in-progress minimap polish that
was already in the working tree (slot-index protocol + per-tick row-bg
parity + brighter light/dark pastels). The bug surfaced once the
polish landed and the viewport overlay started reading "wrong" ticks
for the operator. Fixed by replacing the uniform-row-height math with
real DOM measurement. 393 → 394 frontend tests (+1 regression).
Backend untouched. No SSE / sentinel / schema / dual-list change.

## The bug

User report (screenshot, light theme): a 337-event run scrolled
mid-list. The topmost row visible in the timeline was a purple
`thinking` step (#376); the bottom was a green `signal` row (#379).
But the minimap's viewport-overlay rectangle had a *yellow* tick at
its top edge (warning-bg = `pause`/`artifact_edited`) and a different
*green* tick at its bottom edge — neither of which corresponded to
the actually-visible rows.

## Root cause

`TimelinePane.vue` translated `scrollTop → display-row index range`
via a hard-coded `ROW_HEIGHT = 88` constant:

```ts
const firstIdx = Math.floor(scrollTop / ROW_HEIGHT)
const lastIdx  = Math.floor((scrollTop + clientHeight - 1) / ROW_HEIGHT)
```

This held while rows were uniform-sized. Two unrelated features in
the same component broke that assumption catastrophically:

1. **Tool-call grouping** — a 131-tool burst collapses to one short
   ~90px group-anchor row, but the math still counts 131 rows worth
   of `ROW_HEIGHT * scrollTop` index space.
2. **Per-row expand** — a thinking row's body can be 500-1000+ px
   when expanded; an assistant row likewise. Scrolling past one tall
   row consumes hundreds of px without advancing the row index.

With both in play (a typical engteam run), the index drift was
arbitrary — the overlay drifted up to ~20 slots away from the truly
visible rows.

The same `* ROW_HEIGHT` math was also load-bearing in
`scrollToDisplayIndex(idx)`, the minimap click handler — clicking a
specific tick on the strip would scroll to the wrong row by the same
margin.

## What shipped

- **`frontend/src/components/runs/TimelinePane.vue`** —
  - `viewportDisplayRange` switched from a `computed` to a `ref`
    populated by a new `measureViewportRange()` function. The
    function walks the rendered `<li>` children of `.timeline__list`,
    reads each one's real `offsetTop` and `offsetHeight`, and
    selects the first/last whose extent overlaps
    `[scrollTop, scrollTop + clientHeight]`. For the virtualized
    path the rendered-slice index is mapped back to the full
    displayRows index via `window.value.start`.
  - `scrollToDisplayIndex(idx)` looks up the target row's real
    `offsetTop`/`offsetHeight` when it sits inside the currently-
    rendered slice (always true for non-virtualized lists, which
    cover anything under 1000 events). Falls back to the
    `ROW_HEIGHT` estimate only when the target is outside the
    windowed slice — a follow-up `onScroll` then re-measures with
    real geometry once the slice rolls forward.
  - Triggers for `measureViewportRange()`:
    - `onScroll` — already runs on user scroll + on the auto-scroll
      programmatic assignment.
    - Existing `rows.length` watcher — catches event-stream
      growth.
    - New `[rowOverrides, groupExpanded, () => prefs.expanded]`
      watcher with `{deep: true}` — catches every per-row toggle,
      group expand/collapse, and type-default toggle that mutates
      layout heights.
    - `onMounted` — schedules an initial measure after the first
      DOM flush; without this the overlay shows `{0, 0}` until the
      user first scrolls.

- **`frontend/tests/TimelinePane.spec.ts`** — added one regression
  test (`viewport overlay tracks real per-row heights, not a uniform
  estimate`) that plants `Object.defineProperty(li, 'offsetTop' …)`
  geometry on four `<li>` elements with a tall row 1 (800px among
  50/60/90px peers), scrolls to y=[100,500] (fully inside row 1), and
  asserts the overlay style reports `top: 25%, height: 25%` (one slot
  of four, the correct row). Under the old math this would have
  computed `floor(100/88)=1` and `floor(499/88)=5 → clamp 3`,
  framing rows [1, 3].

## Why the polish in the working tree set this up

The slot-index minimap protocol (which I did not change) was already
correct: ticks are positioned by slot `i/n`, and the overlay frames
`[viewportStart, viewportEnd]` in the same slot space. The
*translation* from `scrollTop` → slot indices was the bit that lied.
The polish bumped tick contrast (light theme 100-band → 200-band
pastels, dark theme alpha 0.10 → 0.22) so the overlay's misalignment
became suddenly visible. The fix lands in the same logical change
set and they ship together.

## Verification

In a live `meeting-assistant` run via Playwright at light theme,
1400×1000:

- Collapsed view, mid-scroll: visible rows 7–16 of 27. Overlay
  reports `top: 25.9259%; height: 37.037%` = exactly 7/27 → 17/27.
  Top tick under overlay edge: gray (usage row 7). Bottom: green
  (signal row 16). Match.
- All `thinking` rows expanded, scrolled to a tall row: visible row
  is index 13 alone. Overlay reports
  `top: 48.1481%; height: 3.7037%` = exactly 13/27 → 14/27. Under
  the old math this would have computed scroll-position 3331px /
  88 = 37, clamped to slot 26 — the bottom of the strip.

## Out of scope

- **Virtualized + out-of-window click** — clicking a minimap tick
  for a row not currently in the rendered slice still uses the
  `ROW_HEIGHT` estimate for the initial scroll. The follow-up
  `onScroll` re-measures with real geometry once the slice rolls
  forward, so the operator lands close to (not exactly on) the
  target — acceptable for >1000-event runs that are already an
  approximation.
- **ResizeObserver on individual rows** — not added. Row heights
  change in response to user toggles (which we watch) and content
  arriving (which we already catch via the `rows.length` watch).
  Adding a per-row observer would handle async content like
  shiki-highlighted code blocks finishing their layout after the
  initial mount, but in practice that single-frame drift is
  invisible. Keep it simple.
