# 260528 — step-cards redesign (TimelinePane v2)

Follow-up to `260528-dashboard-ux-polish.md`. The user reviewed the
first polish bundle and flagged two issues:

1. The Display popover button was floating awkwardly inside the
   timeline scroll container — sticky `pointer-events: none` topbar
   with the gear as the only `pointer-events: auto` child. The user
   asked for it to be inside the `right-pane__meta` chrome instead.
2. Tool rows were useless when collapsed — `bash` rows in a
   development iter just said `bash` and `ARGS` with the body masked
   out. Copy/Expand buttons floated between rows in dead space,
   visually anchored to nothing. Asked to brainstorm + research UX
   patterns; settled on a bordered card with a header strip carrying
   a smart preview.

## What landed

### 1. Display menu extraction (`TimelineDisplayMenu.vue`)

- Pulled the gear button + popover out of `TimelinePane.vue` into a
  new `components/runs/TimelineDisplayMenu.vue`. The popover content
  (per-type expand-by-default toggles, Reset button) is identical;
  state still lives in the global `useTimelinePrefsStore` so the
  button can be mounted anywhere. Mounted from `RunRightPane`'s
  `.right-pane__actions` row next to the Cancel button — proper page
  chrome anchor instead of sticky float. Closes on outside-click and
  Escape via `document.addEventListener`.
- `TimelinePane` lost ~95 lines of gear/popover template + CSS.
- Test for the prefs popover moved from `TimelinePane.spec.ts` (which
  retains a contract test that flipping prefs flips row collapsed
  state) to a focused `TimelineDisplayMenu.spec.ts` (open/close, type
  toggle, reset).

### 2. Step cards — bordered card + header strip

User picked Option A (bordered card with header strip) from a 3-way
brainstorm referencing established patterns: Datadog/Stripe span
rows (Option A), GitHub Actions log lines (Option B, single-line
collapsible), Chrome DevTools Network (Option C, dense table).

New row structure for collapsible types (`tool` / `signal` /
`assistant` / `thinking` / `generic`):

```
<li.timeline__row.timeline__row--card[.timeline__row--collapsed][.timeline__row--error][.timeline__row--assistant]>
  <header.timeline__card-header role="button" @click="toggleRow">
    #seq · glyph · name · status · duration · smart-preview
    spacer
    [⧉ Copy] [▸ Expand|▾ Collapse]
  </header>
  <div.timeline__card-body v-if="isRowExpanded">
    <ToolCallCard | SignalCard | <p.timeline__text> | <code.timeline__bmeta> />
  </div>
</li>
```

Inline rows (`boundary` / `pause` / `usage` / `artifact_edited`)
keep the legacy positioned-control layout — they have nothing to
collapse into.

**Smart preview** (`previewFor`): tool-name-keyed, case-insensitive.

- `bash` → `$ <command>` (whitespace collapsed via `replace(/\s+/g, ' ')`)
- `write` / `edit` → `→ <file_path>` (falls back to `path` / `filename`)
- `read` → `← <file_path>`
- `grep` → `? <pattern>`
- `glob` → `* <pattern>`
- `task` / `agent` → `<description>` (or `prompt`)
- assistant / thinking → `firstLine(text)`
- generic → stringified payload

Truncated to 140 chars with `…`.

**Duration formatter**: `<1s → "{ms}ms"`, `<1m → "{s.s}s"`, else
`"{m}m {s}s"`. Status icon for tool rows derives from `toolEnd`:
no toolEnd → `…` pending, `is_error === true` → `✗` err, else `✓`.

### 3. Inner-renderer de-duplication

ToolCallCard, SignalCard, and the inline message/generic blocks all
carried their own border + padding + background — sized for the old
flat row layout. Inside the new card body that creates nested-card
visual noise. Stripped via `:deep()` overrides in TimelinePane's
scoped CSS:

```css
.timeline__card-body :deep(.tool-card) { border: none; padding: 0; background: transparent; }
.timeline__card-body :deep(.tool-card__head) { display: none; }
.timeline__card-body :deep(.signal-card) { border: none; border-left: 3px solid var(--color-warning); … }
```

`.tool-card__head` carried the name + duration — now duplicated by
the card header, so hidden. The error badge inside `tool-card__head`
becomes `data-status="err"` on the card header instead.

## Traps + things I'd re-step on

- **Tool names come lowercase from pi.** First implementation matched
  the literal `'Bash'`/`'Write'`/`'Read'` strings (capitalized — as
  the unit test fixtures and old `findings.md` had them). At runtime
  pi emits `bash`/`write`/`read`. Result: every preview hit the
  fallback branch and showed `command: ...` / `path: ...` instead of
  `$ ...` / `→ ...`. **Lesson: when matching tool names, always
  `.toLowerCase()` first**. Pi normalises sometimes but you can't
  count on it; the headerName() output keeps the original casing.
- **Conditional body rendering broke 4 TimelinePane tests.** Tests
  asserted `[data-testid="tool-call-card"]` and
  `[data-testid="signal-card"]` were present at mount. With
  collapsed-by-default and the body unrendered when collapsed, those
  cards aren't in the DOM. The fix is to expand the row first
  (`await trigger('click')` on the row header) before asserting on
  body content. Kept conditional rendering — for a 1000-event live
  iter, rendering 1000 ToolCallCards with their `<pre>` blocks
  beneath collapsed headers would be measurable overhead.
- **`.timeline__card-spacer` + `flex: 1` on the preview.** Both
  compete for the same role (push the controls to the right). When
  the preview is non-empty its `flex: 1` already does the job — the
  spacer collapses via `.timeline__card-preview + .timeline__card-spacer { display: none }`. When no preview, the spacer
  pushes the controls right. This handles boundary-case rows that
  yield empty previews (assistant with empty text, signal without
  args) without an extra `v-if` ladder.

## Verification

- `npm run check` clean: 35 test files, **288 passing** (+ 3 new
  smart-preview / header-toggle tests).
- Playwright walk on a real meeting-assistant run with 130+ tool
  cards. Verified:
  - Display button sits in the right-pane action row, opens its
    popover anchored properly.
  - Every bash row now reads as `#seq ⚒ bash ✓ {duration} $ <command>`
    in a clearly bordered card — readable without expanding.
  - Read rows: `#seq ⚒ read ✓ {duration} ← <path>`.
  - Click anywhere on the header expands; click again collapses.
  - Copy button still works; click no longer collapses the row
    (`@click.stop` on the controls container).
  - Light + dark theme both render correctly.

## Files touched

- `frontend/src/components/runs/TimelinePane.vue` — row template +
  smart-preview helpers + CSS rewrite; removed inline gear/popover
- `frontend/src/components/runs/TimelineDisplayMenu.vue` (new)
- `frontend/src/components/runs/layout/RunRightPane.vue` — mount
  `TimelineDisplayMenu` in `.right-pane__actions`
- `frontend/tests/TimelinePane.spec.ts` — 4 expand-first updates +
  3 new smart-preview / header-toggle tests
- `frontend/tests/TimelineDisplayMenu.spec.ts` (new)
- `docs/dashboard.md` — replaced "Timeline row controls" section
  with "Timeline step cards" (anatomy + smart preview + Display
  menu relocation)
