# 260528 — display dropdown affordance + per-type row colors

Third polish pass on the run-detail timeline. After the
`260528-step-cards-redesign.md` work, the user flagged two more
issues:

1. **Display button looked like a status badge.** Same rounded-rect
   chrome as the StatusBadge pills, no chevron, no obvious dropdown
   affordance. The user hinted it's also "the beginning of some sort
   of filter" — so the button needs to look forward to that future.
2. **All step cards looked the same.** With a uniform pale-gray card
   chrome, scanning the timeline by step type was still hard. The
   user wanted distinct backgrounds and borders per type — "pastel
   shades… and the border a more saturated hue of the pastel".

## What landed

### 1. Display menu reads as a dropdown trigger

`TimelineDisplayMenu.vue` updates:

- Added a trailing chevron `▾` that rotates 180° via class binding
  when the popover is open. Pattern aligned with GitHub filter
  pills / Linear's Display button / Tailwind UI menu button.
- Added `aria-haspopup="menu"` and kept `aria-expanded` reflecting
  open state. Both flip together with the chevron rotation.
- Tuned the button visual: heavier resting border
  (`--color-border-strong`), a font-weight bump (500) so the label
  reads as an action rather than a static tag, padded asymmetrically
  (`0.45em 0.55em 0.45em 0.7em`) so the chevron has tight
  trailing space without leaving the gear glyph cramped.
- Open state mirrors hover (`background: var(--color-surface-hover)`
  + `border-color: var(--color-text-dim)`) so the visual stays put
  when the menu is open and you've moved the mouse away.

New test asserts the aria + chevron contract toggles in sync with
`aria-expanded`.

### 2. Per-type pastel palette

New tokens in `styles/base.css` covering all five collapsible row
types, with parallel light + dark + auto definitions:

```
--color-row-assistant-bg / --color-row-assistant-border   (blue)
--color-row-thinking-bg  / --color-row-thinking-border    (violet)
--color-row-tool-bg      / --color-row-tool-border        (amber)
--color-row-signal-bg    / --color-row-signal-border      (green)
--color-row-other-bg     / --color-row-other-border       (slate)
```

Light theme uses solid pastel surfaces (`#eff6ff`, `#f5f3ff`,
`#fef9c3`, `#dcfce7`, `#f1f5f9`) with same-hue saturated borders
(`#60a5fa`, `#a78bfa`, `#eab308`, `#4ade80`, `#94a3b8`). Dark
theme uses low-alpha tints over the dark surface for the
background (`rgba(96, 165, 250, 0.10)` etc.) with higher-alpha
borders. The auto branch in the `@media (prefers-color-scheme:
light)` block duplicates the light values literally — the
alternative (re-using CSS custom properties as the source) is
fragile under media queries, so kept the duplication explicit.

TimelinePane consumes them via `data-row-type` attribute
selectors:

```css
.timeline__row--card[data-row-type='assistant'] {
  border-color: var(--color-row-assistant-border);
  background: var(--color-row-assistant-bg);
}
/* … one rule per type */

.timeline__row--card.timeline__row--error {
  border-color: var(--color-danger);
}
```

The error state on a tool row still wins — a failed bash should
read as "fix this" at a glance even though the type colour is
amber. The `.timeline__row--error` class is applied last in the
CSS so its border-color overrides the per-type rule.

The card body keeps `background: var(--color-surface)` so code /
text content stays on a clean surface; the pastel hue lives in
the header strip and the outer border, framing the body like a
colored matte.

New test asserts each row carries `data-row-type="<type>"` (the
contract the colours hang off) without coupling to specific hex
values — those are theme-dependent and tuned in design review.

## Traps + things I'd re-step on

- **CSS custom properties don't cascade through `@media` cleanly.**
  My first attempt tried setting the per-type tokens once in
  `:root[data-theme='light']` and reusing them in the
  `prefers-color-scheme` auto block via `var(...)`. It worked
  intermittently — CSS variable resolution against the matched
  media query is unintuitive once you're mixing attribute selectors
  + media. Kept the literal hex duplication; it's three places
  (dark / light / auto-light) so the maintenance cost is modest.
- **Background colour collision on expanded header.** Pre-colour,
  the expanded card header used `var(--color-surface-hover)` as a
  subtle "this section is open" cue. With the row's bg now a
  pastel, that surface-hover overlay sits on top of the pastel.
  In light it reads correctly (the rgba darken applies on any
  base); in dark the same overlay against a low-alpha tint
  produces a barely-visible darker tint. Left as-is — the
  border-bottom divider is now the primary "open" cue, so the
  background distinction is a secondary nicety.
- **Body bg stays at `--color-surface`, NOT the row tint.** The
  user said "pastel backgrounds" — could be read as "the whole
  card is pastel including the body". I went with "frame = pastel,
  body = surface" so tool args / assistant text render on a clean
  high-contrast base. If the user wants full-card-pastel that's a
  one-line change (drop the `.timeline__card-body {background}`
  declaration).

## Verification

- `npm run check` clean: 35 test files, **290 passing** (+ 2 new:
  per-type data attribute on each card row, Display chevron / aria
  contract toggle).
- Playwright walk on the meeting-assistant run with 134 cards.
  Verified:
  - Yellow (tool) band reading down the timeline for the long
    bash/read/write sequence — scannable at a glance.
  - Purple (thinking) and blue (assistant) cards in the wrap-up
    area, distinct from the surrounding amber tool rows.
  - Failed `edit` row still wears the red error border over the
    amber tool tint.
  - Display button shows `[⚙ Display ▾]`; chevron rotates 180° on
    open; popover anchored below.
  - Both light and dark themes render correctly.

## Wrap-up code-review fixes

Three issues from the `/done` Phase-5 code review (all addressed
before committing):

1. **Undefined CSS tokens with light-mode fallbacks.**
   `TimelinePane.vue` and `UsageRow.vue` both referenced
   `var(--color-text-muted, #888)` and
   `var(--color-border-subtle, #e0e0e0)` — neither token is
   defined in `styles/base.css`, so the hardcoded fallbacks were
   the actual rendered values. The `#e0e0e0` left-border on the
   `artifact_edited` row produced a too-bright line over the
   `#0f1115` dark surface. Replaced with the existing
   `--color-text-dim` and `--color-border` tokens so both themes
   resolve correctly.

2. **theme.ts: misleading comment about reactivity.** The
   `installOsListener` registered a `change` listener that
   re-assigned `choiceRef.value = 'auto'` to "force a tick" when
   the OS preference flipped. Vue 3 short-circuits ref-equal
   assignments, so this was a no-op. The CSS `@media
   (prefers-color-scheme: light)` block already owns the visual
   flip when `data-theme="auto"`, and no component reads
   `useTheme().resolved` for non-CSS decisions today, so the
   no-op was harmless — but the comment falsely claimed otherwise.
   Replaced the listener body with a no-op + documented why we
   don't subscribe.

3. **`previewFor(row)` called 3× per row per render.** The card
   header had `v-if="previewFor(row)"` + `:title="previewFor(row)"`
   + `{{ previewFor(row) }}` — three full string-matching passes
   per row per reactive update. Added `preview: string` to the
   `Row` interface and populate it once in a post-pass at the end
   of the `rows` computed; the template now reads `row.preview`.
   For a 134-card timeline this drops from ~400 string ops to
   ~134 per re-render, AND only re-runs when the underlying
   events list changes (Vue caches the computed).

## Files touched

- `frontend/src/styles/base.css` — 5 new per-type token pairs in
  each palette block (dark + light + auto)
- `frontend/src/components/runs/TimelinePane.vue` — `data-row-type`
  selectors apply the tokens; error class wins over the type tint
- `frontend/src/components/runs/TimelineDisplayMenu.vue` — chevron
  + `aria-haspopup` + open-state visual; CSS rotation transition
- `frontend/tests/TimelinePane.spec.ts` — data-row-type contract test
- `frontend/tests/TimelineDisplayMenu.spec.ts` — chevron + aria test
- `docs/dashboard.md` — extended "Timeline step cards" section with
  Display button affordance + per-type palette table
