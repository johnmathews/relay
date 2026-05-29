# 2026-05-29 — Run-detail layout Phase 4: Pause banner extraction + sticky

Phase 4 of the run-detail layout proposal
(`docs/proposals/run-detail-layout.md` §"Build sequence" item 4 /
§"Pause banner") shipped as a single commit on `main`. 327 → 328
frontend tests (+1 structural ordering assertion). Backend untouched.
No SSE / sentinel / schema / dual-list change.

## Freeze stance

Same call as Phases 1–3 — dashboard-only, no backend / SSE / sentinel
surface, no new event-kind subscription. The pause flow itself is
unchanged (wrap, not rewrite). A sticky Resume button is a clear
ergonomic win during a paused-then-resume cycle: the operator can
scroll the artifact pane (the file they're being asked to review)
without losing access to the answer textarea + Resume button.

## What shipped

- **New `frontend/src/components/runs/layout/PauseBanner.vue`** —
  thin amber-bordered wrapper around `PauseAnswerForm`. Props
  `{ runId, question, reviewPaths }` and emit `@resumed` thread the
  14c / 14e / 14f contract through verbatim. `reviewPaths` is typed
  `ReadonlyArray<string>` at the banner's surface and re-cast at the
  form's prop site (the form still wants a mutable `string[]` —
  matching the existing pattern in `RunRightPane`).
- **`RunRightPane.vue`** — swap `<PauseAnswerForm>` for
  `<PauseBanner>` at the same slot (between header and body). The
  `v-if="isPaused"` stays on the wrapper, not inside it — single
  source of truth for "show pause UI" remains in the parent.

The banner's CSS:

```css
position: sticky;
top: 0;
z-index: 2;
border: 1px solid #e0b341;
border-left-width: 4px;
border-radius: 6px;
background: var(--color-surface);
padding: 0.75rem 1rem;
box-shadow: 0 2px 8px var(--color-shadow);
```

Hex `#e0b341` is inlined with a comment pointing back at the
`yellow-pause-borders-validated` memory note (no global token exists
yet). Solid `var(--color-surface)` background is load-bearing — when
the banner is stuck and the body scrolls behind it, a transparent
background would let the scrolling content bleed through.

## Sticky scope — sticky-to-viewport, not sticky-to-pane

The proposal said "sticky in the right-pane scroll container", but
`.right-pane` is **not** a scroll container today: no `overflow-y`,
no fixed `height`. The whole page scrolls. Two options were on the
table:

- **A — sticky-to-viewport** (shipped): `top: 0` on the banner sticks
  to the viewport. No layout reshape.
- **B — true per-pane sticky** (deferred): give `.right-pane` its own
  scroll container (`overflow-y: auto` + a defined height). Cleaner
  conceptually but reshapes the right pane and affects every body
  component's scroll behaviour — bleeds into Phase 6's responsive
  layout work.

A is visually identical for the operator's use case (banner stays
glued under the page top while the body scrolls past) and matches
how the existing `.timeline__jump` sticky pill already works in
`TimelinePane.vue`. B is left for Phase 6 if the responsive collapse
ends up requiring a per-pane scroll story anyway.

No fixed nav bar exists above the right pane (`grep` for
`position:.*fixed` confirms only `DirectoryPicker.vue` uses fixed
positioning, and `position:.*sticky` only the timeline's jump pill).
`top: 0` is the right value; no offset adjustment needed.

## Drift — paused-default artifact selection already shipped in 3a

The proposal's §"Pause banner" closes with "On paused entry, the rail
auto-selects the first `review_path` artifact …". That bullet
**already shipped in commit `ae782ea`** as part of Phase 3a's
`smartDefault({ reviewPaths })` paused-branch. Re-implementing here
would have double-applied. Phase 4 in practice was just the banner
extraction + sticky — the artifact-selection behaviour was already in
place and tested.

## Test changes

`RunRightPane.spec.ts` paused-state test split into two:

1. **Wrap-preserves-form**: assert both `data-testid="pause-banner"`
   exists AND `findComponent({ name: 'PauseAnswerForm' })` exists
   AND the form is found *inside* the banner.
2. **Structural ordering**: assert the banner sits between
   `.right-pane__header` and `.right-pane__body` in the children of
   `[data-testid="run-right-pane"]`. This is the load-bearing
   visual-order assertion that catches a future refactor that
   accidentally lifts the banner above the header or buries it inside
   the body.

The negative test (`does NOT render PauseBanner when status != paused`)
asserts both `[data-testid="pause-banner"]` absence and form-component
absence — paranoid double-check that the wrapper doesn't leak when
the run isn't paused.

`PauseAnswerForm.spec.ts` was untouched. Its mounts call
`PauseAnswerForm` directly, not through the RunRightPane render path,
so the wrap is invisible to it.

## What did NOT happen

- **No `PauseAnswerForm` change.** Props, emit, internal review-paths
  tab logic, diff toggle, ApiError mapping — all untouched. The 14c /
  14e / 14f contract is preserved at the form's prop surface
  byte-for-byte.
- **No new event-kind subscription.** Dual-list contract
  (`KNOWN_EVENT_TYPES` × `INVALIDATING_KINDS`) untouched.
- **No layout reshape.** `.right-pane` does not become a scroll
  container (Option B deferred to Phase 6).
- **No live-browser smoke this round.** The sticky behaviour is
  trivial CSS that vitest can't render meaningfully (jsdom doesn't
  layout); a real smoke would need a long enough paused run to
  produce a scrollable body below the banner. The user can spot-check
  during the next acceptance pass.

## What's left

Per the proposal:

- **Phase 5** — Tool-call detail drawer (`ToolCallDetailDrawer`,
  `ToolCallCard` trigger, focus trap, drawer animation).
- **Phase 6** — Responsive collapse below 900px. This is the natural
  home for the per-pane scroll story if it ever becomes load-bearing
  for the sticky banner (it didn't here).
- **Phase 7** — Accessibility + empty-state polish.

Phase 2's chip-row drift (localStorage expand-by-default vs the
proposal's URL-serialised `?kinds=` visibility filter) is still
standing — same call as in `260529-run-detail-phase-3.md`, a
doc-only follow-up not in scope for any of these build phases.
