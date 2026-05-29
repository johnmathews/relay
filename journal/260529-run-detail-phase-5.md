# 2026-05-29 — Run-detail layout Phase 5: Tool-call detail drawer

Phase 5 of the run-detail layout proposal
(`docs/proposals/run-detail-layout.md` §"Build sequence" item 5 /
§"Tool-call detail drawer") shipped as a single commit on `main`.
328 → 347 frontend tests (+19 new across two new specs). Backend
untouched. No SSE / sentinel / schema / dual-list change.

## Freeze stance

Same call as Phases 1–4 — dashboard-only, no backend / SSE / sentinel
surface, no new event-kind subscription. A drawer for long tool-call
results is a clear readability win for noisy iters: today the
inline-expand toggle on `ToolCallCard` blows out the timeline when a
single `Bash` result spans hundreds of lines, and there is no place
for shiki / markdown / diff renderers to land in a step-card body
without dominating it.

## What shipped

- **New `frontend/src/composables/useFocusTrap.ts`** — small
  hand-rolled trap (~70 LOC). Captures `document.activeElement` on
  activate, intercepts `Tab` / `Shift+Tab` on the trap root for first
  ↔ last wrapping, intercepts `Escape` and calls an `onEscape`
  callback. On deactivate, restores focus to the previously-active
  element if it's still in the DOM. The activate path runs in a
  `queueMicrotask` so the drawer's root ref is populated before the
  trap reads from it.
- **New `frontend/src/components/runs/ToolCallDetailDrawer.vue`** —
  `<Teleport to="body">` slide-in panel anchored to the right edge,
  50vw wide on desktop, with `role="dialog"` `aria-modal="true"` and
  an `aria-label` carrying the tool name. CSS `translateX` keyframe
  animation (160ms) that no-ops under `prefers-reduced-motion`.
  Backdrop click + Esc + close-button all emit `close`; the dialog
  body has `@click.stop` so clicks inside the panel never bubble to
  the backdrop. Header carries the tool name + error badge + duration
  + a `[Code | Markdown | Diff]` mode dropdown + close button.
- **`ToolCallCard.vue`** — gains a `tool-card__view-full` button next
  to the existing `tool-card__toggle`. The new button calls an
  injected `openToolDetail({name,args,result,isError,durationMs})`
  callback; absence of the inject (older call-sites / direct test
  mounts) hides the button entirely so the inline 5-line collapse
  stays the only expand. Both affordances coexist when both apply.
- **`RunRightPane.vue`** — owns local `drawerOpen` / `drawerPayload`
  refs and `provide`s `openToolDetail` to any descendant. Renders the
  drawer as a sibling of `.right-pane__body`. A watcher on
  `props.detail.id` clears the drawer state on run navigation so a
  stale tool from the previous run can't re-open from cache.

## Renderer composition — what each mode shows

The drawer composes the existing `@/components/files/{Code,Markdown,Diff}Render.vue`
without modification:

- **Code** (default): `CodeRender` for `args` (`lang="json"` when
  args is structured, `"text"` when it was already a string), and
  for `result` (default `"text"` — tool results have no reliable
  language signal). Args + result render as separate labeled
  sections.
- **Markdown**: `MarkdownRender` over the stringified args and
  result. Useful for `Read`-tool results that return markdown content
  verbatim.
- **Diff**: only meaningful when args carries an Edit-tool shape
  (`old_string` + `new_string`, optionally `file_path` for the
  filename). In that case `DiffRender` renders the inferred change.
  For any other tool, a `data-testid="tool-drawer-diff-empty"`
  message says *"Diff not applicable for this tool — no `old_string`
  / `new_string` pair in args."*. No heuristic guessing.

Mode resets to `code` each time the drawer re-opens — a previous
Markdown / Diff selection doesn't carry across into an unrelated tool
call. The reset is keyed on the `open` prop edge, not on payload
identity, so opening + browsing two consecutive tools within a single
drawer session keeps the operator's mode choice.

## Trigger UX — separate "View full" button, NOT a row-click

The proposal text says "when a tool-call row is clicked", but the
existing inline `tool-card__toggle` ("Show full" / "Show less") is
load-bearing for moderate-length cases where opening a drawer is
overkill. Two affordances, two affordances' worth of UX:

- **Show full** (inline `tool-card__toggle`): expands the 5-line
  collapsed `<pre>` in place. Cheap, no chrome change. Unchanged from
  pre-Phase 5.
- **View full** (new `tool-card__view-full`): opens the drawer with
  shiki / markdown / diff rendering. New in Phase 5.

The two buttons render side-by-side in a `.tool-card__actions` row
(replacing the bare margin-top toggle). When the card doesn't
overflow, only "View full" shows; when no provider is wired (e.g.
direct test mounts), only "Show full" shows. Both can coexist.

## Provide/inject vs. event chain

`ToolCallCard` is rendered inside `TimelinePane`, which is inside
`OverviewPanel` / `IterTimelinePanel`, which is inside `RunRightPane`.
Four levels of prop-drilling for a click event would have meant a new
emit on each intermediate component (`@open-tool-detail` plumbed
verbatim) and a corresponding `defineEmits` declaration. The
`provide`/`inject` pair on a single key (`'openToolDetail'`) lets the
intermediate layers stay byte-identical. The drawer state itself
still lives in `RunRightPane` — what's injected is just the
*callback*, not the state.

The injection's typed nullable (`(p) => void | null`) so call-sites
that mount `ToolCallCard` directly without a provider still type-check
and quietly hide the "View full" affordance.

## Drawer state is NOT URL-reflected

Per the proposal's §"URL contract" / §"State management": "Tool-call
drawer open/closed + payload — local ref in RunRightPane. Transient;
not URL-reflected." Honoured verbatim. The drawer does not surface
in `?view=` or `?kinds=` — closing the tab and reopening the same URL
returns to the same selected `view` / `kinds` with the drawer closed.

This is the correct trade-off: a drawer that re-opens on refresh
would surprise the operator (they're inspecting transiently, not
declaring a navigation), and a tool-call payload key in the URL
would balloon link length.

## Focus trap — what's there and why simple

The trap (`useFocusTrap`) is intentionally narrow:

- **No roving tabindex**. The drawer's controls are a flat list (mode
  select, close button, anything inside the rendered content). Tab
  cycles them in DOM order.
- **No `inert` outside the root**. jsdom doesn't support `inert`
  anyway; modern browsers do but we'd need a polyfill story. The
  backdrop swallows pointer events; the trap handles keyboard.
- **No `document`-level keydown**. The Escape listener is attached to
  the drawer's root element, which receives focus on open
  (`tabindex="-1"`). Keydowns bubble there; the listener swallows
  `Escape` and Tab keys.
- **No layout-visibility filter**. `focusableWithin` returns
  everything matched by the focusable-selector query — no
  `offsetParent !== null` check. jsdom doesn't compute `offsetParent`
  (everything reads `null`), so the filter would empty the list under
  test. For the drawer's known controls in production this is fine;
  the drawer never hides individual focusables behind `display:none`
  while it is itself open.

This is the right granularity for ARIA-dialog-on-a-known-control-set.
If a future modal needs richer behaviour (`inert` outside, roving
tabindex, etc.) the composable can grow.

## Drift — proposal said row-click, shipped as button

Already covered above. The row-click option was rejected because:

1. It would collide with the existing inline `tool-card__toggle`
   button (needing `stopPropagation` on the toggle).
2. It would collide with text selection over the `<pre>` blocks
   (operators copy bash commands and result snippets).
3. A separate button leaves the existing
   `TimelinePane.spec.ts:88,321,336–343` assertions on
   `data-testid="tool-call-card"` and `tool-card-toggle` unchanged.

The drawer is one click farther than a row-click, but it's
discoverable (a labeled button in a known affordance row) and never
fires by accident.

## Test changes

**New `frontend/tests/ToolCallDetailDrawer.spec.ts`** — 15 cases:

- **render gate**: nothing renders when `open=false` or `payload=null`;
  the dialog mounts with `role="dialog"` `aria-modal="true"`
  `aria-label` carrying the tool name when both are present.
- **close paths**: close-button click, backdrop click, and `Escape`
  keydown each emit `close`; a click inside the dialog body does NOT
  (`@click.stop`).
- **render-mode dropdown**: defaults to `code`; switching to `diff`
  without `old_string`/`new_string` in args shows the empty state;
  switching to `diff` with Edit-tool args renders DiffRender; mode
  resets to `code` each time the drawer re-opens.
- **focus trap**: opening the drawer moves focus from a previously
  focused trigger button into the dialog; closing restores focus to
  the trigger; Tab on the last focusable wraps to the first;
  Shift+Tab on the first wraps to the last.

**New `frontend/tests/ToolCallCard.spec.ts`** — 3 cases:

- "View full" not rendered without an injected provider.
- "View full" rendered + invokes the injected callback with the full
  payload shape `{name, args, result, isError, durationMs}` when
  clicked.
- Both inline `tool-card-toggle` and new `tool-card-view-full`
  coexist when content overflows AND a provider is wired.

**`RunRightPane.spec.ts`** — one new case: drawer is not in
`document.body` when state is closed (closed = no zombie focus trap,
no stale teleported DOM).

`TimelinePane.spec.ts:88,321,336–343` were untouched and still pass
— the inline toggle behaviour is byte-identical.

## What did NOT happen

- **No `KNOWN_EVENT_TYPES` / `INVALIDATING_KINDS` change.** The
  dual-list contract from the 9f bug-fix sweep is untouched — Phase 5
  is presentation rearrangement on existing `tool_use_start` /
  `tool_use_end` event data.
- **No `TimelinePane` change.** The drawer's trigger lives on
  `ToolCallCard` and the injection plumbing crosses through
  `TimelinePane` invisibly via `provide`/`inject`.
- **No render-component change.** `CodeRender` / `MarkdownRender` /
  `DiffRender` are composed verbatim. The drawer doesn't touch
  `lib/render.ts`.
- **No URL state.** Per the proposal — drawer is transient inspection.
- **No live-browser smoke this round.** The drawer's mount, focus
  trap, and ARIA wiring are vitest-testable; the slide animation is
  CSS-only and visual. The user can spot-check during the next
  acceptance pass — a long Bash result on a real run is the natural
  trigger.

## Drift call — `defineComponent` test helper count

`vue/one-component-per-file` flagged a pair of inline
`defineComponent({ setup })` helpers used for open→close lifecycle
tests. Consolidated to a single file-scope `DrawerHarness` parametrised
by a module-scope `parentRefs` slot the tests populate before mount.
Slightly uglier than two inline defines but keeps the lint at zero
warnings (`--max-warnings 0` is the gate contract).

## What's left

Per the proposal:

- **Phase 6** — Responsive collapse below 900px. Natural home for the
  per-pane scroll story if it becomes load-bearing (the Phase 4
  sticky-banner work parked it). The drawer's 50vw is fine on desktop;
  on narrow viewports `min-width: 320px` and `max-width: 100vw` keep
  it usable.
- **Phase 7** — Accessibility + empty-state polish. Some of Phase 5's
  ARIA work (dialog/aria-modal/focus trap) is a down-payment on
  Phase 7; remaining items per the proposal are mostly the rail's
  `listbox`/`option` markup and contrast spot-checks.

Phase 2's chip-row drift (localStorage expand-by-default vs the
proposal's URL-serialised `?kinds=` visibility filter) is still
standing — same call as Phases 3/4, a doc-only follow-up not in
scope for any of these build phases.
