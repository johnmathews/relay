# 2026-05-29 — Kind-colour contrast rebalance (WCAG 1.4.11)

Closes the deferred Phase-7 item recorded in
[`260529-run-detail-phase-7.md`](260529-run-detail-phase-7.md)
§"Deferred — kind-colour contrast". Pure CSS token update in
`frontend/src/styles/base.css`; no component / structural / a11y /
SSE / event-store change. Backend untouched.

## Decision

Picked option (b) from the Phase-7 menu: **solid bolder hues across
both themes**. Dark-theme borders move from `rgba(…, 0.35–0.45)` to
the matching solid hex (alpha 1.0); light-theme borders move from
Tailwind 400-band hues to the 600/700-band. The pastel row
backgrounds keep their character (dark: 0.08–0.10 alpha tint;
light: 50/100-band pastel hexes).

**Tool kind swapped off amber → teal.** Amber (`#e0b341`) is reserved
for human-attention affordances per the
`yellow-pause-borders-validated` memory (the pause banner border).
Bumping tool's amber up to clear contrast would have collided
visually with the pause banner — easier to shift the hue. Teal
(Tailwind teal-400 `#2dd4bf` dark / teal-700 `#0f766e` light) is
distinct from both assistant blue and signal green, and reads
"neutral / mechanical" which matches tool-call semantics.

The other four kinds keep their hue family (assistant=blue,
thinking=violet, signal=green, other=slate) — only the saturation /
darkness changed. A kind reads the same hue in both themes; no
re-theme regression risk.

## Before → after ratios

Both audits computed with the same Python WCAG snippet (relative
luminance via the standard `srgb_to_lin` piecewise + composite over
surface for `rgba` borders). 3:1 is the WCAG 1.4.11 non-text
threshold; aimed comfortably above so a future palette tweak doesn't
re-regress.

```
DARK THEME (--color-surface = #181b21)
                BEFORE                          AFTER
            vs surface  vs row-bg          vs surface  vs row-bg
assistant     2.40:1     2.31:1              6.78:1     5.78:1
thinking      2.30:1     2.21:1              6.34:1     5.44:1
tool          2.72:1     2.62:1              9.27:1     7.67:1
signal        3.02:1     2.87:1              9.90:1     8.12:1
other         1.95:1     1.92:1              6.73:1     5.93:1

LIGHT THEME (--color-surface = #ffffff)
                BEFORE                          AFTER
            vs surface  vs row-bg          vs surface  vs row-bg
assistant     2.54:1     2.34:1              5.17:1     4.75:1
thinking      2.72:1     2.48:1              5.70:1     5.20:1
tool          1.92:1     1.79:1              5.47:1     4.86:1
signal        1.74:1     1.59:1              5.02:1     4.57:1
other         2.56:1     2.34:1              7.58:1     6.92:1
```

Lowest post-fix ratio: **4.57:1** (light-theme signal border vs row
bg). Every cell clears 3:1 by a margin of ≥ 1.57.

## Token changes

Dark theme (`:root`, `:root[data-theme='dark']`):

```
assistant border  rgba(96, 165, 250, 0.45) → #60a5fa
thinking  border  rgba(167, 139, 250, 0.45) → #a78bfa
tool      bg      rgba(251, 191, 36, 0.08) → rgba(45, 212, 191, 0.10)
tool      border  rgba(251, 191, 36, 0.40) → #2dd4bf
signal    bg      rgba(74, 222, 128, 0.09) → rgba(74, 222, 128, 0.10)
signal    border  rgba(74, 222, 128, 0.45) → #4ade80
other     bg      rgba(148, 163, 184, 0.07) → rgba(148, 163, 184, 0.08)
other     border  rgba(148, 163, 184, 0.35) → #94a3b8
```

Light theme (`:root[data-theme='light']` and the
`@media (prefers-color-scheme: light)` `:root[data-theme='auto']`
fallback — both blocks updated identically):

```
assistant border  #60a5fa → #2563eb  (Tailwind blue-600)
thinking  border  #a78bfa → #7c3aed  (violet-600)
tool      bg      #fef9c3 → #ccfbf1  (teal-100)
tool      border  #eab308 → #0f766e  (teal-700)
signal    border  #4ade80 → #15803d  (green-700)
other     border  #94a3b8 → #475569  (slate-600)
```

## Rejected palettes

- **(a) Just bump dark-theme alpha to ~0.80.** Would have cleared
  the dark-theme audit but not the light-theme one (light borders
  are already solid; only a hue change moves the needle there). And
  half-measures across themes is exactly the "blue in dark, yellow
  in light" failure mode the brief flagged.
- **Bumping tool's amber instead of swapping hue.** Cleared the
  math but reintroduced the visual collision with the pause-banner
  amber. The memory `yellow-pause-borders-validated` exists
  specifically because that collision is a recurring footgun.
- **Tool = cyan-400 / cyan-600.** Worked numerically but reads as
  close to assistant blue under fluorescent office light. Teal is
  greener — distinguishable from both blue and signal-green at a
  glance.
- **Tool = rose / pink.** Hue-distinct but semantically wrong:
  tool-call rows are not warning-like, and pink would have read as
  a danger affordance.

## Impact surface

Two components consume the row tokens (verified by grep):

- `TimelinePane.vue:1028–1049` — row left-border + bg per kind.
- `EventKindFilter.vue:142–192` — chip lit-state bg + border AND
  chip-dot solid-colour background. The dot reuses the `*-border`
  token directly as `background:`, so the alpha bump to 1.0 also
  makes the dots fully opaque on dark — that's the intended visual
  cue and a tiny improvement on the legacy 0.35–0.45 dots.

Both update by token resolution. No `*.vue` / `*.ts` / spec touch.

## Verification

- WCAG: Python snippet (`tmp/contrast.py` during development) walked
  all 5 kinds × 2 themes × 2 surfaces; every ratio cleared 3:1 with
  ≥ 1.57 margin. Numbers in the BEFORE → AFTER table above.
- Frontend gate: `npm run check` (eslint --max-warnings 0 + vue-tsc
  + vitest). 375 frontend tests, all pass.
- Backend untouched (no Python / SQLite / SSE / OTel change). Last
  green totals (371 backend + 3 pi-e2e gated, 95% coverage) carry
  forward.
- Visual: run-detail timeline + EventKindFilter chip dots verified
  to render the new palette on both light and dark themes; chip-dot
  colour matches row-border colour (single token).

## What did NOT change

- Number of tokens, token names, or component CSS — only the values.
- Pause-banner amber (`#e0b341` family). Still the sole carrier per
  the validated memory.
- The `--color-warning-*` token family (different from `--color-row-*`)
  — those drive the StatusBadge / pause banner amber and stay
  untouched.
- Frontend test suite count — no new tests added; the contrast
  audit is a build-time Python check, not a Vitest assertion (no
  natural API surface for a vitest `expect(contrast(…)).toBeGreaterThan(3)`).
