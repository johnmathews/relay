# 2026-05-29 — Minimap brightening + `CANCELLED` UsageRow note

Two small UX items from a live-run review.

## Q1 — `harness_session_ended` badge reads as `CANCELLED` on every normal iter close

Confirmed (not fixed). The `UsageRow.vue` (Phase 9g, ADR-39) badge shows
pi's `stop_reason`. On every signal-closed iter — `done`, `handoff`,
`pause-for-input`, `unit-abandoned`, `fanout` — the loop breaks at the
terminal sentinel (`loop.py:192-194`), then the `finally` block calls
`session.cancel()` (`loop.py:202`) which terminates pi before its own
`agent_end` arrives. `pi.py:wait()` then synthesises
`stop_reason="cancelled"` because `_cancelled` was set by the cancel.
Zero tokens on the same row for the same reason — pi's per-message
usage lives in `agent_end` which never landed.

So the badge is technically truthful but consistently misleading: a
healthy iter looks like an aborted one, especially next to a zero-token
row. No code change this session — flagged for a possible follow-up
that maps `stop_reason="cancelled" + outcome.signal != None` to a
softer label like `closed-on-signal`. Documented this answer in the
session response; left the implementation decision to a later call.

## Q2 — Minimap was too dim to read

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

## Gate

Frontend `npm run check` green: eslint (`--max-warnings 0`) + `vue-tsc
-b --force` + 40 vitest files / 391 specs pass. Backend untouched
this session.
