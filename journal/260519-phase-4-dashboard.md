# 260519 — Phase 4: Vue 3 dashboard MVP

Built the entire Phase 4 dashboard (`docs/plan.md` Phase 4, spec §9,
ADR-15) via the engineering-team Build workflow in an isolated worktree
(`eng-phase-4-dashboard`, branched cleanly from `712a441` — verified
HEAD == local main == origin/main first, no baseRef gotcha). Scoping
was already settled in
`.engineering-team/runs/manual-20260519T145208Z/discussions/260519-phase-4-dashboard-scope.md`;
this session was a focused build, not a re-scope.

## What shipped

A new `frontend/` (Vue 3 + vue-router v5 + Pinia + Pinia Colada + Vite,
TypeScript strict). The dashboard is the primary control plane per
ADR-15:

- **Hub** (`/`) — registered projects with latest-run status (the
  accepted N+1 at single-user scale), register-project form.
- **Project view** (`/projects/:id`) — tab-switched Runs / Prompts /
  Files panes + New-run button.
- **New-Run wizard** (`/projects/:id/new-run`) — 4 steps (prompt
  select/inline → options → side-effect-free preview → start). Start is
  gated on the preview having loaded; cancel creates no `runs` row;
  preview uses the project-id path segment + prompt query param
  (api.md's documented quirk).
- **Run detail** (`/runs/:id`) — header + cancel, live SSE timeline
  (mixed event kinds, `signal_emit` styled + anchored, tool-call cards
  collapsed, virtualized > 1000 events), iters pane filtering the
  timeline by iter seq, artifacts pane (ADR-25 endpoints), degraded
  worktree pane, pause/resume form.
- **Prompts CRUD** (create / edit-bumps-version / delete-all-versions /
  read-only version history) and project register/unregister.
- **File render pipeline** — markdown-it (+footnote/task-list,
  `html:false`), shiki (lazy core + JS regex engine + per-lang
  grammars), mermaid (dynamic import), diff2html. One
  `FileTree`/`FileViewer` + `BrowserSource` abstraction serves both the
  project file browser and the run artifacts (mirrors ADR-25's
  single-sourced backend — no duplicate tree/viewer).

Built vertical-slice-first: api client + SSE wrapper → Hub → wizard →
run-detail with the live timeline (demoable end-to-end), then the
remaining panes fanned out. 8 work units (W1–W8), each
doc → tests → code, lead-reviewed before acceptance.

## Key decisions (ADR-26)

The plan.md stack was kept whole (no swaps; Pinia Colada 1.3 and
openapi-typescript 7 risks were already cleared). Five toolchain
mandates were recorded and implemented, because the libraries had
drifted since the plan was written:

1. vue-router is **v5** (not v4) — adopted directly.
2. shiki = `createHighlighterCore` + `@shikijs/engine-javascript` +
   lazily-imported grammars — never the convenience bundle.
3. mermaid = dynamic `import()` on first render only — never static.
4. Vite SSE dev-proxy: long `proxyTimeout` + no buffering for
   `text/event-stream`.
5. vitest v4 **removed** the `coverage.all` toggle entirely — the
   plan's "set it explicitly" is impossible; intent met via explicit
   `coverage.include`. (The literal mandate predated the v4 type
   change — recorded so it isn't "fixed" back to an invalid option.)

Plus two implementation calls: **diff2html kept** (spec/plan prescribe
it, pinned `^3.4`, only its stable `html()` used, no MVP-relevant risk;
v-code-diff documented as the future alternative), and **routed views
keyed by `route.fullPath`** so a param-only navigation remounts
(Vue Router otherwise reuses the instance, which would carry per-run
module state / an SSE EventStream / setup-scoped stores across runs).

## Independent code review (pre-merge)

A separate reviewer pass returned GO-WITH-FIXES. Two High findings,
both fixed:

1. **Orphaned EventSource on pause→resume.** `eventsStore.open()` only
   `reset()`s when the runId changes; a resume re-`open()`s the *same*
   run, so `openLive` overwrote `stream` while the prior (paused-run)
   EventSource stayed alive and unreachable by `close()`/`markTerminal()`
   — the reconnect-storm failure ADR-23 guards against. Fixed by closing
   any existing stream before re-choosing a strategy; added a regression
   test (same-run re-open closes the prior stream, no orphan).
2. **Composable-in-`computed` hazard** + the related param-reuse
   `opened`-flag bug. Resolved structurally by the keyed `RouterView`
   (remount on id change) — fixes the whole class rather than patching
   one component.

Two Medium findings (a slightly-misleading mermaid comment; replay
pagination using `after_seq:0`+offset, which is correct for immutable
terminal runs) were judged MVP-acceptable and noted in ADR-26 /
reviewed rather than churned.

## Gates

- Python: `ruff` clean, `mypy --strict` clean, `pytest` **142 passed,
  3 pi-e2e skipped** (gated behind `PI_INTEGRATION=1` — unchanged
  baseline), backend coverage 92%.
- Frontend gate added and wired: `npm run check` = `eslint
  --max-warnings 0` (hardened from the default so warnings fail) +
  `vue-tsc` + `vitest` — **136 passed (24 files)**, ~85% line coverage.
- `vite build` succeeds; eager first-load ≈ 41 KB gz (vue + entry +
  css), heavy renderers (shiki langs, mermaid, katex, cytoscape) all
  lazy chunks — far under the 800 KB-gz Phase-4 budget.
- Every `docs/plan.md` Phase 4 verification criterion maps to a
  proving test: mixed-event timeline, Last-Event-ID reconnect,
  pause/resume, markdown+mermaid, 7-language shiki highlight (real
  shiki, not mocked), diff render, wizard preview-gate + no-row-on-
  cancel, prompt create/edit/version-history read-only.

## Deferred (named post-MVP gaps, not regressions)

- Worktree pane live git status / per-file diff (degraded to read-only
  `worktree_path`/`branch`; no git subprocess surface) — Phase-4
  scoping decision G2.
- Hub "latest run status per project" batch endpoint (N+1 accepted at
  single-user scale).
- The "live tail keeps up with a *real pi* run" eyeball is the
  `PI_INTEGRATION=1` manual step; the SSE wrapper, run-status-aware
  replay/live orchestration, and the rendered-vs-total event-count
  parity surface are all unit-tested offline.

## Docs

CLAUDE.md current-state → Phases 0–4 + a frontend Toolchain entry;
ADR-26 appended (append-only respected); spec §9.1 worktree-degraded
note + §9.4 pointer to ADR-26 and the BrowserSource abstraction.
`frontend/README.md` carries the operational form. Next coding work:
Phase 5 (MCP server).
