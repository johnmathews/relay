# 2026-05-28 — Run-detail layout Phase 2: event-kind filter chips

Phase 2 of the run-detail layout proposal (`docs/proposals/run-detail-layout.md`)
shipped. Phase 1 (the two-column layout shell) landed at 4f6b803 earlier
today plus the post-merge polish in 8180ace (per-card backgrounds +
smart preview); Phase 2 adds the chip-row visibility filter that's now
the persistent legend for the row palette and the URL-reflected
filter state.

## What changed

1. **`src/lib/eventKinds.ts`** — single source of truth for kind →
   category mapping. Five categories: `assistant`, `thinking`,
   `tool`, `signal`, `other`. `assistant_text` splits on
   `payload.kind`; `tool_use_start`/`tool_use_end` collapse to `tool`;
   the structural / boundary kinds (`signal_emit`, `iter_*`,
   `run_*`, `subagent_*`, `child_runs_resolved`,
   `harness_session_ended`, `pause_*`) fold to `signal`; everything
   else (including `artifact_edited`, `usage`, future-kinds) →
   `other`. `parseKinds`/`serializeKinds` are the URL helpers — they
   normalise null / empty / full-set / unknown-token inputs back to
   "no filter" so the URL is stable across roundtrips.
2. **`src/components/runs/EventKindFilter.vue`** — five-chip
   `role="toolbar"`. Each chip is `aria-pressed`, carries a
   colour-keyed dot (reusing the `--color-row-<kind>-border` tokens
   from 8180ace so the chip dot and the per-card border match), the
   label, and an in-scope count. Clicking a chip toggles its
   category; the first click against `modelValue=null` resolves to
   "everything but the clicked chip" (intuitive single-click hide).
   A Clear button appears only when a filter is active.
3. **`TimelinePane.vue`** — accepts a new `kindsFilter` prop and
   applies it **after** the iter-scope walk; order is load-bearing
   because the scope walk anchors on `iter_started`/`iter_ended`
   boundaries that would themselves be filtered out if we applied
   kinds first whenever the user hid the Signal chip. Pending
   (ADR-46 `assistant_delta`) rows respect the filter too. Each row
   gains a small colour-tinted kind label next to `#seq`; the
   8180ace per-card backgrounds stay untouched (the user-question
   answer was "Keep current backgrounds, add label" — belt +
   labels, no 4-px-border variant). Props widened to
   `ReadonlyArray` — the Phase 1 cast scaffolding in
   `OverviewPanel` / `IterTimelinePanel` drops.
4. **`OverviewPanel.vue`** + **`IterTimelinePanel.vue`** — render
   `EventKindFilter` above the timeline with per-panel scoped
   counts. Overview counts the full event list; Iter walks the
   `iter_started`/`iter_ended` boundaries to scope. Tool count is
   `Math.ceil(N/2)` so the chip reads as "cards visible" matching
   the paired start+end card rendering.
5. **`RunRightPane.vue`** + **`RunDetailView.vue`** — thread
   `kindsFilter` and `update:kindsFilter` through. URL is the
   source of truth: `RunDetailView` reads `?kinds=` via the route,
   serialises updates back, and deletes the param entirely when
   the filter is null. Mirrors the Phase 1 `?view=` pattern.

## Open question resolutions

The user answered two clarifications before I started building:

1. **"The Display menu controls expand-by-default, not visibility"** —
   keep both. The two controls are orthogonal concerns (visibility
   vs. expand-state) and a single combined control would conflate
   them. `TimelineDisplayMenu.vue` is unchanged.
2. **"The current code already paints each card with per-type
   backgrounds; the spec proposed 4px left borders"** — keep the
   backgrounds, add only the kind label next to `#seq`. Avoids
   reverting 8180ace polish.

These decisions are recorded in `docs/proposals/run-detail-layout.md`
under the Phase 2 build-sequence line.

## Load-bearing invariants worth preserving across future phases

1. **Two-stage filter ordering in `TimelinePane.vue`.** Iter scope
   first, kinds second. The reverse would lose
   `iter_started`/`iter_ended` events whenever the user hid the
   Signal chip and silently break the scope walk. There's a regression
   test
   (`tests/TimelinePane.spec.ts::"respects iter scope before kinds"`).
2. **URL contract symmetry.** Both `parseKinds` and `serializeKinds`
   normalise full-set / empty / unknown back to "no filter". The
   round-trip is the source of truth — touch one without the other
   and the URL ↔ state binding breaks.
3. **The chip dot, the card border, and the small kind label
   share `--color-row-<kind>-border` tokens.** A future theme edit
   that breaks any one of the three is visible in the others, but
   the chip row is the canonical legend so it's the obvious test
   target.

## Verification

Frontend gate green: `eslint --max-warnings 0` + `vue-tsc -b
--force` + `vitest run` — **37 files / 328 tests** (38 new across
`eventKinds.spec.ts`, `EventKindFilter.spec.ts`, the Phase 2 block
in `TimelinePane.spec.ts`, and the URL-plumbing block in
`RunDetailView.spec.ts`). Backend gate also green (371 passed +
3 pi-gated skipped, 95% coverage). Live-smoked in a real browser
via Playwright against a `done`-status run with 313 tool calls and
18 signal events: counts matched, Signals chip off correctly
filtered the rows, navigating Overview → Iter#2 preserved both
`view=` and `kinds=` query params, Clear dropped the kinds param
from the URL entirely.

## What's next

Per the proposal, Phase 3 is **Follow-live pin + smart default +
keyboard nav** (`f` toggle, `j`/`k`/`g o`/etc., auto-engage rules
on entry to a live run). Phases 4–7 still proposed.
