# 260530 — Chip filter becomes a visibility filter + readable signal/boundary payloads

## What changed

Two-part run-detail UX improvement, both on the frontend, no backend
touched.

1. **`EventKindFilter.vue` chip row is now a per-category visibility
   filter instead of a per-type expand-by-default toggle.** Clicking
   `Tool calls` hides every tool-call row from the timeline; clicking
   it again brings them back. Default = every chip lit (every kind
   visible). The toggle replaces the previous "lit chip = rows of
   that kind expand on first render" semantics, which was confusing
   in practice — the rows still existed but rendered as collapsed
   headers, and the operator's intuition from the icon was always
   "this hides things, doesn't it?" — so we made that true.

2. **Eight chips instead of five, with tooltips.** The old `signal`
   bucket lumped sentinel signals, iter/run lifecycle, pause + fanout
   coordination, and `harness_session_ended` into one chip; the old
   `other` bucket silently held `artifact_edited` alongside any
   future unknown kinds. Both were opaque. The new mapping is:

   | Category | Event kinds |
   |---|---|
   | `assistant` | `assistant_text` (text) |
   | `thinking` | `assistant_text` (thinking) |
   | `tool` | `tool_use_start`, `tool_use_end` |
   | `signal` | `signal_emit` |
   | `boundary` | `iter_started`, `iter_ended`, `run_started`, `run_ended`, `harness_session_ended` |
   | `pause` | `pause_requested`, `pause_resolved`, `subagent_dispatch`, `subagent_return`, `child_runs_resolved` |
   | `artifact` | `artifact_edited` |
   | `other` | unknown / future kinds only |

   Each chip's `title` tooltip lists its members verbatim from
   `KIND_MEMBERS` in `src/lib/eventKinds.ts`. `Other` is finally
   honest — it's the unknown-futures bucket, nothing concrete.

3. **New `EventPayloadView.vue` — field-aware payload rendering.**
   The motivating bug: an expanded `iter_started` row dumped the
   payload as one `JSON.stringify(payload, null, 2)` `<pre>` block.
   The `prompt` field carried 5–10 KB of agent context with embedded
   `\n` characters that rendered as the literal two-character escape
   `\n` — unreadable. Operators were the primary audience for that
   prompt (it's the iter's actual instruction). The new component
   walks top-level fields and renders multi-line strings with real
   newlines in a `<pre>` block with a `Show all N lines` toggle past
   12 lines. Short scalars render inline next to a label. Nested
   structures fall back to indented JSON. A
   `[ View raw JSON / View formatted ]` toggle in the corner swaps
   to a single shiki-highlighted JSON block via
   `lib/render.renderCode(src, 'json')` for the rare case where the
   structural shape matters.

   `EventPayloadView` is wired into both `SignalCard.vue` (replaces
   its inline `<pre>{JSON.stringify(args)}</pre>` block) and the
   boundary/generic fallback in `TimelinePane.vue` (replaces the
   `.timeline__bmeta--pretty <pre>{prettyJson(ev)}</pre>` block).

## Why now

The user opened a run detail at iter 3 and tried to read the
`iter_started` prompt to understand what the run was actually
doing. The JSON wall hid the answer. The chip-row confusion
("this lights up but the rows don't disappear?") came up in the
same session, so both fixes shipped together. Per
`docs/acceptance-testing.md`, the MVP is in operator-acceptance mode
and this category of friction is exactly what the gate is meant to
flush out before sign-off.

## Mid-session refinement — focus-style filter

The first cut shipped a plain hidden-set model (`isHidden` /
`toggleHidden` / `showAll`, persisted as `KindCategory[]` under
`relay.timeline.hiddenKinds`). On reading the result back, that
intuition felt wrong for the operator's mental model — flipping
seven chips to "look at just thinking" is the inverse of what they
want to do. Pivoted in the same session to a Gmail-label /
Material-filter-chip focus model: three modes (`all` / `subset` /
`none`), first chip click enters `subset` with just that kind,
subsequent clicks add/remove. Removing the last selected chip
snaps back to `all` (load-bearing dead-state guard — the operator
can't strand themselves on an empty timeline via chip clicks
alone; reaching `none` requires the explicit `Show none` button).

Store surface settled on:

- `mode: 'all' | 'subset' | 'none'`
- `selected: Set<KindCategory>` (the active set in subset mode)
- `isHidden(c)` / `isActive(c)` (read-side; `isActive` is the
  chip-template alias)
- `toggle(c)` — Gmail-label add/remove with the snap-back guard
- `showAll()` / `showNone()` — jump-to buttons
- `hasSelection` (computed, drives the `Show all` button)

LS key changed from `relay.timeline.hiddenKinds` →
`relay.timeline.kindFilter` and the payload shape from `string[]`
to `{mode, selected}` to record the tri-state cleanly.

`EventKindFilter.vue` gained a `Show none` action button alongside
the existing `Show all`, both rendered conditionally
(`mode !== 'none'` / `mode !== 'all'`) so they're a one-click escape
to either extreme without ever showing the no-op button.

`TimelinePane.vue`'s minimap re-measure watcher now keys off
`[() => prefs.mode, () => prefs.selected]` (was `() => prefs.hidden`
on the first cut) so the viewport overlay tracks the row count when
the active set changes.

## Migration / persistence

`useTimelinePrefsStore` now persists a different shape under a
different localStorage key:

- Was: `relay.timeline.expanded` → `Record<TimelineRowType, boolean>`
  (per-row-type expand-by-default).
- Is: `relay.timeline.kindFilter` → `{mode, selected: string[]}`
  (tri-state focus filter).

The old key is silently ignored — fresh defaults (all visible) on
first load. The `TimelineRowType` type was removed from the store
since the chip no longer drives expand state; the store re-exports
nothing from `eventKinds.ts` so the dependency goes one way
(store → eventKinds, not the other way round) eliminating the
cyclic-vocabulary tension that motivated the original
`categoryToRowType` bridge.

`TimelinePane.vue::isRowExpanded` simplified: per-row override
wins; default = collapsed. No more
`prefs.isExpandedByDefault(row.type)` fallback. The per-row click-
to-expand on the row's own header is the sole expand control.

## Load-bearing details

1. **Counts are pre-filter.** Per-chip count badges still show how
   many rows EXIST in scope, not how many are currently visible —
   otherwise hiding a chip would zero its own counter and lose the
   "N hidden by me" affordance. The counts accumulator iterates
   `props.events` in `OverviewPanel.vue` / `IterTimelinePanel.vue`
   BEFORE the visibility filter applies in `TimelinePane`'s `rows`.

2. **`KIND_MEMBERS` is the single source of truth for tooltips.**
   The chip's `title` reads strings from `KIND_MEMBERS[category]`,
   not hard-coded per chip. Adding a new event kind to the
   classifier only requires editing `eventKinds.ts` in one place;
   the tooltip refreshes automatically.

3. **`EventPayloadView.vue` raw-mode `v-html`** uses the same
   pattern as `MarkdownRender.vue` — `<!-- eslint-disable
   vue/no-v-html -->` block around the single `<div v-html="...">`.
   The HTML is sanitised by `renderCode` (shiki tokeniser HTML-
   escapes content). Safe.

4. **Visibility filter inserted in `rows` computed**, not in the
   render template, so the windowing math + tool-call grouping +
   group-anchor counts all see the filtered list and stay
   consistent.

5. **Minimap re-measure watcher updated** —
   `watch([rowOverrides, groupExpanded, () => prefs.expanded], …)`
   became `… () => prefs.hidden`. Without this swap, hiding a
   category wouldn't trigger the minimap's viewport overlay
   re-measure and the strip would lag the row count.

## Test deltas

1. **`eventKinds.spec.ts`** — rewrote: dropped `categoryToRowType`
   tests; added a per-category routing test for `boundary` / `pause`
   / `artifact`; asserted the canonical 8-chip display order.

2. **`timelinePrefs.store.spec.ts`** — rewrote: tests now drive
   `toggleHidden` / `isHidden` / `showAll` against the new
   `relay.timeline.hiddenKinds` LS key. Added a forward-compat test
   that drops unknown category strings on load (a future build's
   persisted state shouldn't crash an older bundle).

3. **`EventKindFilter.spec.ts`** — rewrote: chips start lit (default
   visible); clicking hides; tooltip contains underlying kinds
   pulled from `KIND_MEMBERS`.

4. **`TimelinePane.spec.ts`** — flipped the prefs-store-integration
   test from "toggle a type → row expands" to "toggle hidden → row
   disappears, toggle again → reappears". Updated the boundary-body
   test selector from `.timeline__bmeta--pretty` to
   `[data-testid="event-payload-view"]` + asserted field text
   (`phase`, `wrap-up`) rather than raw JSON string.

5. **`RunDetailView.spec.ts`** — flipped the chip-press assertion:
   default `aria-pressed="true"` (lit/visible), post-click
   `aria-pressed="false"` (dim/hidden). LS-cleanup `beforeEach`
   migrated to the new key.

6. **`EventPayloadView.spec.ts`** — new: asserts field-aware layout
   for `{seq, phase, misc}`, that multi-line strings render with
   real newlines (NOT `\\n` escapes), the `Show all N lines` toggle
   past `collapseLinesAt`, the `View raw JSON` toggle button text
   swap, and that empty payloads render no field rows.

## Gate status

- Frontend: 403/403 pass, eslint 0 warnings, vue-tsc clean.
- Backend: 371 pass + 3 skipped (pi-integration, gated by
  `PI_INTEGRATION=1`), `ruff` + `mypy --strict` clean, 95% coverage.

## Docs updated

- `docs/dashboard.md` — rewrote the "Chip row — per-type expand-by-
  default" section as "Chip row — per-category visibility (260530)"
  with the new 8-chip table; rewrote the
  "Boundary / generic body — pretty-printed JSON" section as
  "Boundary / generic body — field-aware via `EventPayloadView`
  (260530)".
- No spec / decisions / motivation change. The chip row was always
  documented in `dashboard.md`, not the spec, and the change is UX-
  level (no new architecture, no protocol change, no new event
  kind), so no ADR.

## Files touched

```
docs/dashboard.md
frontend/src/components/runs/EventKindFilter.vue
frontend/src/components/runs/EventPayloadView.vue        (new)
frontend/src/components/runs/SignalCard.vue
frontend/src/components/runs/TimelinePane.vue
frontend/src/components/runs/layout/IterTimelinePanel.vue
frontend/src/components/runs/layout/OverviewPanel.vue
frontend/src/lib/eventKinds.ts
frontend/src/stores/timelinePrefs.ts
frontend/tests/EventKindFilter.spec.ts
frontend/tests/EventPayloadView.spec.ts                  (new)
frontend/tests/RunDetailView.spec.ts
frontend/tests/TimelinePane.spec.ts
frontend/tests/eventKinds.spec.ts
frontend/tests/timelinePrefs.store.spec.ts
```

Plus an incidental `frontend/src/api/schema.d.ts` regeneration from
`npm run gen:api` (picked up a docstring update on the
`delete_project` endpoint that was already in the running backend's
OpenAPI). Harmless; reflects current backend.
