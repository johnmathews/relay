# 260528 — dashboard UX polish bundle

Five small fixes the user surfaced after the run-detail layout shell
landed (260528-run-detail-layout-shell). All scoped to the
`frontend/` tree; no backend, sentinel, or schema change.

## What landed

1. **Light + dark theme.** `styles/base.css` rewritten around two
   parallel token sets selected via `<html data-theme="…">`. New
   `lib/theme.ts` is the single writer (persists `'auto' | 'light' |
   'dark'` in `localStorage`; `applyInitialTheme()` runs in `main.ts`
   before `app.mount` so the first paint has the right palette — no
   FOUC). New `components/shared/ThemeToggle.vue` sits in the top nav
   and cycles `auto → light → dark → auto`. Semantic tokens added —
   `--color-{danger,danger-strong,danger-bg,danger-border,warning,
   warning-bg,warning-bg-strong,success,running,accent-soft,
   accent-fg,surface-hover,surface-elevated,border-strong,shadow}` —
   and every component refactored off hex literals. Light-mode
   contrast tuned for ≥ AA (banner title `#8e0e0e` on near-white ≈
   11:1; AAA).

2. **Project title in `RunSidebar`.** A new top row renders
   "PROJECT / <name>" with a router-link back to `/projects/:id`.
   Threaded from `RunDetailView` via `useProjectQuery` driven off
   `detail.project_id`. `useProjectQuery` grew an `enabled` flag so
   the pre-detail sentinel id `0` doesn't fire a 404 request.

3. **Duplicate iter list removed.** `IterTimelinePanel.vue` used to
   render `<ItersPane>` below the scoped TimelinePane (a leftover
   from the pre-layout-shell world). The sidebar already lists every
   iter as a click-target — having both was confusing. Now the panel
   is the scoped timeline only. The `ItersPane.vue` component still
   exists; just no longer imported by the layout shell.

4. **Dismissable failure banner.** The red `right-pane__failure`
   banner grew a `×` close affordance
   (`[data-testid="dismiss-failure-banner"]`). Dismissal persists in
   `localStorage['relay.failureBanner.dismissed:<runId>'] = '1'` so a
   reload of the same run keeps it hidden. Per-run — a different
   failed run shows its banner again.

5. **Larger labelled row controls.** The Copy / Expand / Collapse
   buttons at top-right of every TimelinePane row now show a text
   label ("Copy", "Expand", "Collapse") alongside the glyph, have a
   comfortable click target (`min-height: 1.9rem`, padding `0.35em
   0.65em`), and are full-opacity at rest. The row itself reserves
   `padding-top: 2.4rem` so the controls never overlap row content,
   collapsed or expanded.

## Traps + things I'd re-step on

- **`useProjectQuery` fired immediately with id=`0`.** First pass
  did `useProjectQuery(() => detail.value?.project_id ?? 0)` —
  works, but produces a 404 request on first paint before detail
  lands. Fix was adding an `enabled` flag (`toValue(id) > 0`); now
  consistent with `useRunDetailQuery` / `useSelectionFilesQuery` /
  `useFileContentQuery` which all already had an `enabled` knob.
- **`ReturnType<typeof computed<X>>` resolves to
  `WritableComputedRef`, not `ComputedRef`.** TS picks the
  writable-overload of `computed`'s union signature when reflected
  through `ReturnType<>`. Original `useTheme` signature broke
  typecheck. Fixed with an explicit `ComputedRef<ResolvedTheme>`
  import + return type.
- **`@media (prefers-color-scheme: light)` doesn't fire under an
  explicit `data-theme="dark"`.** The auto branch only kicks in for
  `data-theme="auto"` or the no-attribute fallback; both `light` and
  `dark` palettes are duplicated literally in `:root[data-theme]`
  selectors + the auto media block. There's some duplication but
  the alternative (re-using CSS custom properties as the source) is
  fragile with media queries — kept it explicit.
- **`stores/files.ts` shares a `run:<runId>` browser key with
  `TimelinePane`'s artifact-edited row handler.** This wasn't part
  of this bundle's scope but it's worth remembering for the next
  artifact-row-related change — the timeline writes
  `currentRun.selectedIterId` and the artifact-row handler writes
  `browserStore.selectedPath` for the same scope.

## Verification

- `npm run check` — 282 tests pass (was 276; added 6 — theme
  controller spec + RunSidebar project-title + RunRightPane banner
  dismiss).
- Playwright walk: hub → toggle theme (auto/light/dark) → click into
  the failed `20260525-160758-11ce` run → project title visible,
  banner dismissable + persists on reload, iter click shows no
  duplicate list, Copy/Expand labels visible at row top-right. Both
  themes rendered in screenshots; banner contrast measured
  programmatically.

## Files touched

- `frontend/src/styles/base.css` — token rewrite + light/dark palettes
- `frontend/src/lib/theme.ts` (new) — theme state + persistence
- `frontend/src/main.ts` — `applyInitialTheme()` before mount
- `frontend/src/App.vue` — `ThemeToggle` in nav + spacer
- `frontend/src/components/shared/ThemeToggle.vue` (new)
- `frontend/src/lib/queries.ts` — `useProjectQuery` optional `enabled`
- `frontend/src/views/RunDetailView.vue` — project query + threading
- `frontend/src/components/runs/layout/RunSidebar.vue` — project title row
- `frontend/src/components/runs/layout/RunRightPane.vue` — banner dismiss
- `frontend/src/components/runs/layout/IterTimelinePanel.vue` — drop ItersPane
- `frontend/src/components/runs/TimelinePane.vue` — labelled row controls
- `frontend/src/components/shared/StatusBadge.vue`,
  `RunHealthBadge.vue`, `ActionButton.vue`,
  `PauseAnswerForm.vue`, `SignalCard.vue`, `ToolCallCard.vue`,
  `AsyncBoundary.vue`, `files/{DiffRender,FileViewer,MarkdownRender,
  MermaidRender}.vue`, `projects/{DirectoryPicker,RegisterProjectForm}.vue`,
  `prompts/PromptEditor.vue`, `wizard/StepPromptSelect.vue`,
  `views/{NewRunWizard,ProjectView}.vue` — hex → token migration
- `frontend/tests/{RunSidebar,RunRightPane}.spec.ts` + new
  `tests/theme.spec.ts`
- `docs/dashboard.md` — Theme system + layout shell + dismiss + row
  controls sections
