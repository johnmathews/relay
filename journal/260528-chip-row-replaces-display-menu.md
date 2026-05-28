# 260528 — chip row replaces Display menu (merge of two timeline controls)

Fourth polish pass on the run-detail timeline, kicked off by a
single-shot user direction:

> The kind-filter looks great. Let's keep it but use it to select
> if a kind is expanded or collapsed — it can replace the
> Display-menu button. I never want to be able to hide a step,
> just choose if it's collapsed or expanded. Also, let's make all
> steps collapsed by default.

That's a clean instruction: merge two adjacent controls into one,
delete the visibility-filter affordance, and reset the defaults.

## What landed

### EventKindFilter is now an expand-by-default toggle

`components/runs/EventKindFilter.vue`:

- Dropped the `modelValue` / `update:modelValue` API entirely.
- Component now consumes `useTimelinePrefsStore` directly. Clicking
  a chip calls `prefs.toggle(categoryToRowType(k))`; chip `is-on` /
  `aria-pressed="true"` reflects `prefs.isExpandedByDefault(...)`
  for that category.
- "Clear filter" affordance retired; replaced with "Reset to
  collapsed" that runs `prefs.reset()` and shows only when at
  least one chip is on.
- Lit-state visual: same pastel background + saturated border as
  the matching card type (`--color-row-<kind>-{bg,border}`), so
  the chip row and the card palette read as the same control.
- Title attributes flipped from "Hide … / Show …" to
  "Collapse … steps by default / Expand … steps by default".

### TimelineDisplayMenu retired

`components/runs/TimelineDisplayMenu.vue` + its `.spec.ts` are
**deleted** — the chip row covers the entire surface the popover
did (per-type expand-by-default + Reset), and the user named the
button as something they didn't want. Removed the mount from
`RunRightPane.vue` action row; the row now only renders when the
run is cancellable.

### timelinePrefs defaults all flipped to collapsed

`stores/timelinePrefs.ts::DEFAULTS`:

```ts
{ tool: false, signal: false, assistant: false, thinking: false, generic: false }
```

was previously `assistant: true` (the agent's reply was the one
auto-expanded type). Operators with no `localStorage` entry now see
every step collapsed; the new chip row is the discoverable opt-in.
Existing operators with a saved preference keep it (the persisted
record overrides DEFAULTS at load time).

### Visibility filter excised

The Phase-2 `kindsFilter` plumbing landed earlier today is gone:

- `TimelinePane.vue` lost the `kindsFilter` prop + the
  `clearKindsFilter` emit, the `filteredEvents` chain (now just
  `filteredEvents = iter scope`), the `categoryFor` helper, the
  in-card `kind-label` badge, and the "All events hidden by
  filter" affordance with its Clear button. The `KIND_LABEL`,
  `classifyEvent`, `classifyPending`, and `KindCategory` imports
  all dropped from this component.
- `OverviewPanel.vue` + `IterTimelinePanel.vue` lost the
  `kindsFilter` prop / `update:kindsFilter` emit; both still
  compute per-category `counts` (the chip row's count badges).
- `RunRightPane.vue` lost the `kindsFilter` prop / emit and the
  `TimelineDisplayMenu` mount.
- `views/RunDetailView.vue` lost the `?kinds=` URL plumbing —
  `parseKinds` / `serializeKinds` / `currentKinds` / `onUpdateKinds`
  all gone. The router never sees a `kinds` param now; chip state
  lives in `localStorage` (via the timelinePrefs store), which
  matches the original user expectation that "this is a personal
  display preference, not part of a shareable URL".

### eventKinds.ts loses URL serialization

`src/lib/eventKinds.ts`:

- Dropped `parseKinds` + `serializeKinds`. They were only used by
  the deleted Phase-2 URL plumbing.
- Added `categoryToRowType(c: KindCategory): TimelineRowType` to
  bridge the `other ↔ generic` naming mismatch between the chip
  vocabulary and the prefs store. Asserted in
  `tests/eventKinds.spec.ts` so a future rename trips a test.
- `classifyEvent`, `classifyPending`, `KIND_CATEGORIES`,
  `KIND_LABEL` all preserved — they still drive the chip row
  counts + labels + colour dots.

## Traps + things I'd re-step on

- **Default flip is a one-way trip for existing operators with
  empty localStorage.** Users who hadn't touched the Display
  menu were getting `assistant: true` (default-expanded). After
  the flip they'll see every assistant row collapsed by default
  until they click the Assistant chip once. That's the intended
  behaviour (the chip is the discoverable opt-in) but it IS a
  visible behavioural change. No migration applied — the saved
  localStorage record is authoritative when present, and absent
  records pick up the new defaults.
- **`other` ↔ `generic` is the only naming mismatch in the new
  contract.** Spent a few minutes auditing whether other type
  vocabularies were drifting between the chip row, prefs store,
  and the TimelinePane Row interface — they're aligned 1:1 now
  except for this one legacy. Codified in `categoryToRowType`
  + its test.
- **Test cleanup was significant.** 4 TimelinePane tests directly
  asserted on the kindsFilter behaviour (all-hidden affordance,
  scope-before-kinds ordering, pending-turn filtering, kind labels)
  — all deleted. 2 RunDetailView tests asserted URL plumbing for
  `?kinds=` — replaced with one assertion that clicking a chip
  flips the prefs store and does NOT touch the URL. 1 store test
  asserted `assistant: true` default — flipped to `false`.
  Net: -1 test file (TimelineDisplayMenu.spec.ts deleted),
  +1 test file's worth of contract assertions (EventKindFilter
  rewrite + categoryToRowType coverage + new chip-toggles-store
  RunDetailView assertion).

## Verification

- `npm run check` clean: 36 test files, 303 passing. ruff + mypy +
  eslint --max-warnings 0 + vue-tsc all green.
- Playwright walk on the meeting-assistant run with 134 cards.
  Verified:
  - Display button gone from the right-pane action row.
  - Chip row renders Assistant 1 / Thinking 1 / Tool calls 131 /
    Signals 4 / Other 0 with the correct per-type colour dots.
  - URL has no `?kinds=` param.
  - All 134 cards start collapsed (every chip in `aria-pressed="false"`).
  - Clicking the Tool calls chip lights it amber + flips
    `localStorage['relay.timeline.expanded']` to
    `{"tool":true,…}` + expands all 131 tool cards in place.
  - "Reset to collapsed" link appears on the right while any chip
    is on; clicking it returns the row to its initial state and
    collapses every type.
  - Both light + dark themes render correctly; the per-card
    palette and the chip palette stay visually aligned.

## Files touched

- `frontend/src/components/runs/EventKindFilter.vue` — rewritten
  (chip-as-expand-toggle, lit-state pastel fill, Reset affordance)
- `frontend/src/components/runs/TimelinePane.vue` — kindsFilter
  prop / emit / filtered-events chain / categoryFor helper /
  in-card kind labels / all-hidden affordance all removed
- `frontend/src/components/runs/layout/OverviewPanel.vue`,
  `IterTimelinePanel.vue` — drop kindsFilter prop + emit, keep
  counts computation
- `frontend/src/components/runs/layout/RunRightPane.vue` — drop
  TimelineDisplayMenu import + mount, drop kindsFilter prop + emit
- `frontend/src/views/RunDetailView.vue` — drop parseKinds /
  serializeKinds / currentKinds / onUpdateKinds + the
  `update:kindsFilter` binding
- `frontend/src/lib/eventKinds.ts` — drop parseKinds /
  serializeKinds, add categoryToRowType
- `frontend/src/stores/timelinePrefs.ts` — DEFAULTS all flipped
  to `false`; comment header updated
- `frontend/src/components/runs/TimelineDisplayMenu.vue` —
  **deleted**
- `frontend/tests/TimelineDisplayMenu.spec.ts` — **deleted**
- `frontend/tests/EventKindFilter.spec.ts` — rewritten for the
  new contract
- `frontend/tests/eventKinds.spec.ts` — drop parseKinds /
  serializeKinds tests, add categoryToRowType coverage
- `frontend/tests/timelinePrefs.store.spec.ts` — flip default
  expectations
- `frontend/tests/TimelinePane.spec.ts` — drop the Phase-2 kinds
  filter `describe` block + the kind-label assertion + the
  assistant-expanded-by-default expectation
- `frontend/tests/RunDetailView.spec.ts` — replace the `?kinds=`
  URL describe block with a single chip-flips-store assertion
- `frontend/tests/RunRightPane.spec.ts` — drop the stale
  `kindsFilter: null` from the default props builder
- `docs/dashboard.md` — replaced the "Display menu" subsection +
  the "Run-detail Phase 2 — chip-row event-kind filter" section
  with a single "Chip row — per-type expand-by-default" section
