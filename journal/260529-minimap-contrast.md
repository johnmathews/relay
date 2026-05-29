# 2026-05-29 — Minimap brightening (×2) + `closed-on-signal` UsageRow relabel (ADR-48)

Two small UX items from a live-run review.

## Q1 / ADR-48 — `harness_session_ended` badge soft-relabel

Diagnosed and **fixed**. The `UsageRow.vue` (Phase 9g, ADR-39) badge
shows pi's `stop_reason`. On every signal-closed iter — `done`,
`handoff`, `pause-for-input`, `unit-abandoned`, `fanout` — the loop
breaks at the terminal sentinel (`loop.py:192-194`), then the
`finally` block calls `session.cancel()` (`loop.py:202`) which
terminates pi BEFORE its own `agent_end` arrives. `pi.py:wait()` then
synthesises `stop_reason="cancelled"` because `_cancelled` was set by
the cancel. Zero tokens on the same row for the same reason — pi's
per-message usage lives in `agent_end` which never landed. The badge
was technically truthful but consistently misleading: a healthy iter
looked like an aborted one.

**Fix (ADR-48).** Widen the `harness_session_ended` payload from
`{stop_reason, messages, summary}` to
`{stop_reason, messages, summary, exit_reason}`, mirroring the value
the loop already writes to the paired `iter_ended` event in the same
`_finish_iter` call. `UsageRow.vue` now derives a `displayLabel`:
`stop_reason="cancelled" + exit_reason="signal"` →
`closed-on-signal`; everything else (including old replays where
`exit_reason` is absent) falls back to the raw `stop_reason`. A
genuine mid-iter cancel (`exit_reason="cancelled"` from a `cancel_run`
call) keeps the loud `cancelled` label. Considered a frontend-only fix
via pairing with `iter_ended` in the events store; rejected because
the store deliberately drops `iter_id` from `StreamEvent`
(`events.ts:93-100`) and restoring it is a bigger change than
mirroring one string. Considered renaming pi's `stop_reason` itself;
rejected — ADR-39 persists it verbatim per ADR-18 opacity. Spec §3.x
payload table updated; tests added (`test_loop.py` asserts
`exit_reason="signal"`; `UsageRow.spec.ts` covers both the
soft-relabel and the genuine-cancel branches).

## Q2 — Minimap: two passes

### Pass 1 (dark theme felt dim)

`TimelineMinimap.vue` looked nearly black against the dark surface —
both the per-row coloured ticks and (especially) the viewport overlay.
Three small CSS-only changes:

1. **`base.css`** — added `--color-accent-soft-strong` (dark `rgba(91,
   157, 255, 0.28)`, light `rgba(9, 105, 218, 0.24)`) paralleling the
   existing `--color-accent-soft`. Added to all three palettes (dark /
   light / `prefers-color-scheme` auto block).
2. **`TimelineMinimap.vue`** — ticks 2px → 4px high, `left`/`right` 2px
   → 1px (denser bands, but per-row-type border colours unchanged so
   they still match the main timeline cards 1:1). Strip widened 22px →
   24px. Border switched to `--color-border-strong`; hover/focus state
   switched to `--color-accent` for a clearer affordance. Viewport
   overlay: 1px → 2px top/bottom borders, `inset 0 0 0 1px` accent-soft
   halo, fill switched to the new `--color-accent-soft-strong`.
3. **`TimelinePane.vue` + `docs/dashboard.md`** — comment / doc width
   bumped 22 → 24 to stay accurate.

The 22px DOMRect mock in `tests/TimelineMinimap.spec.ts:102` is used
only for the drag-math test which reads `rect.height`; the mocked
`width` value is irrelevant to the math, so no test change.

### Pass 2 (light theme dense runs slammed into dark blocks)

Pass 1 brightened the viewport overlay but kept ticks on the per-row
**border** palette (`#0f766e` teal-700, `#7c3aed` violet-600, etc).
In light theme on a 700-event run dominated by tool calls, the 4px
ticks packed into a solid `#0f766e` slab — heavy and dark, the
opposite of what the user wanted. Cards in the main timeline get
their friendly look from **pastel bg + dark border** combo, not the
border alone.

Introduced a dedicated `--color-row-*-minimap` palette kept
deliberately separate from the border palette (the border tokens
have a WCAG 3:1 contrast contract that the minimap ticks
deliberately don't share):

- **Dark theme:** mirrors the existing border colours (they're
  already bright pastels on `#181b21`).
- **Light theme:** drops to Tailwind 300-band — `#93c5fd` blue,
  `#c4b5fd` violet, `#5eead4` teal, `#86efac` green, `#cbd5e1`
  slate, `#fcd34d` amber. Same family as the card bg, slightly
  more saturated so a 60-tick tool burst reads as a friendly
  pastel band instead of dark teal.

`TimelineMinimap.vue` switched its `data-row-type` selectors from
`--color-row-*-border` → `--color-row-*-minimap` (plus
`--color-warning` → `--color-row-warning-minimap` for the
pause/artifact_edited rows). No structural change.

## Gate

- Backend: `ruff check .` clean, `mypy src` clean (40 source files),
  `pytest` 371 passed / 3 skipped (pi-integration gated), 95% coverage
  preserved.
- Frontend: `npm run check` green — eslint (`--max-warnings 0`) +
  `vue-tsc -b --force` + vitest 393 specs (added 2: UsageRow
  soft-relabel + genuine-cancel branches).
