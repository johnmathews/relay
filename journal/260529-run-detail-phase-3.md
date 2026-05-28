# 2026-05-29 — Run-detail layout Phase 3: follow-live + smart default + keyboard nav

Phase 3 of the run-detail layout proposal
(`docs/proposals/run-detail-layout.md` §"Build sequence" item 3) shipped
as three independently-green commits on `main`:

- `ae782ea` — **3a**: smart default routes paused runs to the first
  `review_path` artifact.
- `674eaa9` — **3b**: Follow-live pin in the right-pane header.
- `05eb648` — **3c**: keyboard navigation (j/k/g chords/f/Esc/c).

303 → 327 frontend tests (24 new). Backend untouched. No SSE / sentinel
/ schema / dual-list change.

## Freeze stance

Treated dashboard work the same way as Phases 1 + 2 and the four
polish rounds that landed under the acceptance-testing freeze yesterday
— dashboard-only, no backend/SSE/sentinel surface touched, no new
event-kind subscription, and follow-live is high-leverage enough to
justify expanding the acceptance surface (it turns the dashboard from a
static snapshot into a live-tail surface for actually-running pi runs).
The proposal's "build sequence runs only when gates close" note is the
strict reading; the soft reading we've been following is "polish under
the freeze is fine, watch the gate".

## 3a — Smart default

`runView.ts:smartDefault` gains a `reviewPaths?: ReadonlyArray<string>`
input. New branch: `paused` with a non-empty `reviewPaths` resolves to
`{ kind: 'artifact', path: reviewPaths[0] }`. Empty/absent →
`overview`. Other branches unchanged. The point is that when an
operator clicks into a paused run, the file they're being asked to
review is already open next to `PauseAnswerForm` without a manual
click.

`RunDetailView.vue` had to be re-ordered: `pauseReviewPaths` moved
above the `currentView` computed and the bootstrap watcher — both
now read it. The pre-3a position (line ~171, well below both consumers)
was load-bearing for "works today" only because `currentView` was lazy
and the bootstrap watcher returned early on `d == null`; with a
hydrated Pinia Colada cache the watcher would have fired into TDZ. The
docstring on `pauseReviewPaths` now calls out the reason it sits where
it sits.

## 3b — Follow-live pin

State lives in `RunDetailView`:

- `followLive: Ref<boolean>` — the pin.
- `isLive` computed — `status ∈ {running, awaiting_children}` (the
  pin is meaningful only here; hidden otherwise).
- One-shot bootstrap watcher: auto-engages the pin on first detail
  load when `isLive && urlView == null`. If the URL has an explicit
  `?view=`, the user already picked something; the pin starts off.
- Watcher on the latest iter seq: when the pin is on and a new iter
  appears, push `router.replace({ view: iter:<latest> })`. Replace,
  not push, so back-button doesn't have to traverse every
  auto-promoted iter.
- `onSelectView` un-pins on any manual click. The click is the
  signal of "lock onto this", regardless of what was clicked — the
  pin button is the way to re-engage.
- `toggleFollowLive` flips the pin; re-engaging jumps to the latest
  iter immediately.

Pin button lives in `RunRightPane.vue`'s `right-pane__actions` next to
Cancel. New props `followLive: boolean`, `followLiveVisible: boolean`;
new emit `toggle-follow-live`. Button is a `<button>` (not the
`ActionButton` Cancel uses) — it's a tighter pill-shaped affordance
that wears on/off state via `aria-pressed` + label
("Follow live" / "Following live"), styled with the existing
`--color-accent` / `--color-accent-soft` tokens.

Five new `RunDetailView` integration tests + four new `RunRightPane`
unit tests cover: pin renders only when visible, aria-pressed reflects
state, click emits, auto-engage on entry without `?view=`,
no-auto-engage when URL is explicit, auto-promote on new iter (via the
existing cancel-as-refetch-trigger pattern that other tests already
use), manual click un-pins, click pin → jump to latest, hidden on
terminal.

## 3c — Keyboard navigation

`@vueuse/core`'s `onKeyStroke` was the proposal's suggested mechanism;
the proposal also said "verify in implementation phase or pull it in
directly". Both turn out to be needed: `@vueuse/core` is not a direct
dep AND not a transitive (no `node_modules/@vueuse/` at all — Pinia
Colada doesn't pull it in). Decision: don't add the dep. We need a
chord state machine + focus guard anyway, and `onKeyStroke` only saves
the `addEventListener`/`removeEventListener` boilerplate. Native
listener in `onMounted` / `onBeforeUnmount` is fine.

Shipped subset of the proposal's table:

- `j` / `↓`     — next rail row
- `k` / `↑`     — previous rail row
- `g o`         — jump to Overview
- `g i`         — jump to first iter
- `f`           — toggle Follow-live pin (no-op on terminal)
- `Esc`         — blur active element
- `c`           — focus the Cancel button (does NOT trigger)

Deferred — meaningful extra surface for a follow-up:

- `g a`         — jump to first artifact (needs an artifact-tree walk).
- `h` / `l` / `←` / `→` — rail / pane focus toggle (needs a tabindex
  contract on both panes, and Tab already works).
- `/`           — focus chip row (chips are scoped to the timeline
  body; first-chip focus needs a `tabindex` story).

Chord state: pressing `g` arms a flag with an 800ms timeout. If the
next key is `o` or `i`, fire the jump and clear; if it's anything
else, clear and fall through to the single-key dispatcher. The cleared
key is still eligible as a regular shortcut — but `o` / `i` alone are
no-op (they only mean something inside a chord).

Focus guard: `isEditableTarget(target)` returns true for any
`INPUT` / `TEXTAREA` / `SELECT` / `contenteditable`. All shortcuts —
including `Esc` — are no-op when focus is in an editable target. The
proposal's blanket rule wins; native browser behaviour handles
"escape from this field" outside our scope.

Modifier guard: any of `metaKey` / `ctrlKey` / `altKey` aborts. Keeps
our shortcuts clear of Cmd-J / Ctrl-K / etc.

Selectable list (for `j` / `k`): `[overview, iter:1, …, iter:N]` only.
Artifacts and children are intentionally not in the walk — the rail's
artifacts are a tree (FileTree's expand/collapse behaviour), and
children navigate to a different run. Walking them needs a deliberate
contract.

Tests: 11 new `RunDetailView` integration tests covering j/k/↑/↓
boundaries, g o + g i, chord cancellation on an unrecognised second
key, f toggle on live + no-op on terminal, c focus on Cancel,
input-focus no-op, modifier no-op, Esc-blurs-rail-row. The two
focus-assertion tests (`c` and `Esc`) use `attachTo: document.body`
because jsdom's focus only sets `document.activeElement` for elements
in the document tree.

## What did NOT happen

- No `@vueuse/core` dep added.
- No new event-kind subscription — `iter_started` already drove the
  store's `INVALIDATING_KINDS`; the auto-promote watcher just reads
  `iters.length`.
- No new file. All three pieces fit in `RunDetailView.vue` +
  `RunRightPane.vue` + `runView.ts`. A `useKeyboardNav` composable
  was considered for the chord logic but the view is the only caller
  — extracting it would be the kind of speculative abstraction
  CLAUDE.md tells us to skip.
- No live-browser smoke this round. Coverage is comprehensive at the
  vitest level (j/k/g chords/f/c/Esc/input-guard/modifier-guard/auto-
  promote/auto-engage/un-pin all asserted), but a real live-tail
  smoke would need a running pi run. The user can spot-check during
  the next acceptance pass — the freeze stance pre-supposes that.

## Proposal-reality drift

The proposal's Phase 3 description matches what shipped almost
verbatim. The keyboard table is partial-shipped (see above), which is a
fair scope call per the user's "ship all three in proposed order"
direction — that meant "ship the three sub-pieces", not "ship every
shortcut in the table". The deferred shortcuts are documented in the
3c commit message and here.

Phase 2's chip-row drift (the one the original task brief called out —
shipped as a localStorage-backed expand-by-default toggle instead of a
URL-serialised visibility filter) is still untouched in the proposal
doc itself. Reconciling that is a doc-only follow-up.
