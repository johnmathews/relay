# 2026-05-25 — Dashboard UX nits

Triage of a six-item UX feedback batch from the running dashboard.
Four items were already addressed by earlier commits on this branch
day; two were genuine fixes; one was a question; one was positive
feedback worth saving to memory.

## Triage table

| # | User report | Verdict | Action |
| --- | --- | --- | --- |
| 1 | "server default" placeholder in Options is opaque; prefill with the real default | Done earlier (`8173d4a`) — `StepOptions.vue` already reads `GET /api/system/defaults` and prefills `max_iters` / `iter_timeout` | None |
| 2 | Merge wizard steps 3 (Preview) and 4 (Start) into one | Done earlier (`426f6da`) — wizard now Prompt → Options → Preview & start | None |
| 3 | `live · 120m 18s ago` — CEST treated as UTC | Done earlier (`8b00e61` — `relay_v2._time.UtcDatetime` + `to_utc_iso` for SSE/replay/schemas). User's screenshot was pre-fix. But the `Started` header still rendered the raw UTC ISO string | Format `started_at` via `toLocaleString` in `RunDetailView.vue`; raw value in tooltip |
| 4 | "I can only copy the top-level item in each step" — wants to copy THINKING etc. | Buttons existed on every row but were `opacity: 0` until row-hover, so users thought nested rows had none | Default to `opacity: 0.55`, full on hover/focus, in `TimelinePane.vue` |
| 5 | Where is the expand/collapse settings UI? | The gear popover at the top-right of the timeline pane already does this — it was rendered as a bare `⚙` glyph with no label, easy to miss | Add `Display` text label and bump padding so it reads as a labelled button |
| 6 | Yellow borders on pause-input affordances are excellent | Validation — keep `#e0b341` reserved for human-attention affordances | Saved feedback memory `yellow-pause-borders-validated.md` |

## What landed in `96307da`

Three small frontend edits, no backend / schema change:

```
frontend/src/components/runs/TimelinePane.vue | 24 ++++++++++++++++++------
frontend/src/views/RunDetailView.vue          | 27 ++++++++++++++++++++++++++-
2 files changed, 44 insertions(+), 7 deletions(-)
```

### `RunDetailView.vue` — `formatStarted`

`detail.started_at` is now `2026-05-25T14:07:58+00:00` since
`8b00e61` tagged it with `UtcDatetime`. Rendered through a small
helper that parses the ISO string and formats it with
`toLocaleString(undefined, { year, month, day, hour, minute, second })`
so the viewer sees `25 May 2026, 16:07:58` in CEST. Raw value is in
the tooltip via `:title="detail.started_at"`. Fallback to the raw
string on parse failure so a future format change can't blank the
header.

### `TimelinePane.vue` — row controls always visible

The row controls block (`copy-step` + `toggle-step`) was
`opacity: 0` by default, lifted to `1` on `:hover` / `:focus-within`.
On a busy run with nested rows like THINKING the user perceived a
single copy button at the top of each step and assumed children
weren't copyable. Switched the default to `opacity: 0.55` so the
buttons are always perceptible; hover/focus still goes to full
opacity for the active row.

### `TimelinePane.vue` — labelled gear

The display-options gear was just a `⚙` glyph rendered in
`color: var(--color-text-dim)` and `padding: 0.2em 0.55em`. It got
lost against the timeline header. Wrapped the glyph in a `<span
aria-hidden="true">`, added a `Display` text label, switched to
`display: inline-flex` with a 0.4em gap, used `var(--color-text)` and
bumped padding so it reads as a labelled button. `data-testid` is
unchanged (`display-gear`) so the existing popover test in
`TimelinePane.spec.ts:292` still finds it.

## Notes for future work

The `Started` field is now the only one in the header that does this
local-time formatting. `ended_at` (on the schema but not currently
rendered in `RunDetailView`) and event timestamps in `TimelinePane`
would benefit from the same treatment if/when they're surfaced. The
helper is small enough to inline twice; if it grows a third caller,
lift it to `frontend/src/lib/datetime.ts`.

The other agent's WIP `copyText` extraction on `TimelinePane.vue`
(uncommitted `git stash` survived a defensive stash/pop across the
ff-merge into main) is unrelated to this work and was preserved
intact. They're factoring `copyRow` so the pending-row copy button
can share the clipboard write — additive to today's CSS-only change,
no merge conflict.
