# 260528 — Run-detail layout shell (Phase 1)

Landed the two-column master-detail shell for `RunDetailView`
(spec: docs/proposals/run-detail-layout.md; plan:
docs/plans/2026-05-28-run-detail-layout-shell.md).

What changed visibly:
- Left rail (Overview / Iters / Artifacts / Children) lives on every
  run-detail page.
- Right pane renders one of OverviewPanel / IterTimelinePanel /
  ArtifactPanel based on selection.
- URL reflects selection (`?view=overview|iter:N|artifact:<path>`);
  refresh preserves view; smart-default hydrates the URL when
  absent (running/awaiting_children → latest iter; everything else
  → overview).

What did NOT change:
- Backend, REST, SSE, OTel, sentinel grammar, schema.
- TimelinePane, PauseAnswerForm, ChildrenPane, WorktreePane,
  FileTree, FileViewer internals.
- Events store dual-list contract (no new event kind in Phase 1).
- The load-bearing SSE-open / lifecycle / cancel / resume /
  pauseReviewPaths plumbing in RunDetailView — preserved verbatim
  per the plan's "critical contracts" section.

ARIA refinement made during implementation (not in the original
proposal): the rail uses `<nav>` + `aria-current="page"` rather
than `role="listbox"` + `aria-selected`. `listbox` is invalid when
mixing buttons with `<a>` (router-link) children; `<nav>` is the
correct semantic for a navigation list with a highlighted current
item. The proposal section on accessibility is now slightly
aspirational on that detail; future polish in Phase 7 will sweep
it.

Deferred to later phases (separate plans for each):
- Filter chips + color-coded event kinds (Phase 2).
- Follow-live pin (Phase 3).
- Sticky pause banner + paused-default artifact selection (Phase 4).
- Tool-call detail drawer (Phase 5).
- Responsive collapse below 900px (Phase 6 — current behaviour:
  rail falls under the right pane in a single column).
- Keyboard nav, ARIA polish, empty-state copy (Phase 3 + Phase 7).

MVP-acceptance-testing exception authorised by the user 2026-05-28;
this is the smallest shippable slice that fixes the structural
problem acceptance testing surfaced. Subsequent phases re-evaluate
the freeze case-by-case.

Commits in this slice:
- `bfa855d` feat(frontend): runView — URL state helpers for run-detail layout
- `61e3a8e` test(frontend): runView — round-trip + digit-only iter guard
- `20fb96e` feat(frontend): RunSidebar — Overview / Iters / Children rail
- `41dc427` refactor(frontend): RunSidebar — switch listbox→nav, polish
- `69fe0eb` feat(frontend): RunSidebar — Artifacts section via runArtifactSource
- `b3ee9da` docs(frontend): RunSidebar — explain Artifacts invariants
- `7b9c89a` feat(frontend): body panels — Overview, IterTimeline, Artifact
- `146d7e8` docs(frontend): body panels — explain ReadonlyArray→[] casts
- `2c33178` feat(frontend): RunRightPane — header + selection-routed body
- `9460f5d` refactor(frontend): RunRightPane — drop dead casts, cover failure banner
- `6f010cc` refactor(frontend): RunDetailView — two-column layout shell
- `50db763` test(frontend): RunDetailView — port spec to new layout
- `0b06f08` test(frontend): RunDetailView — assert rendered panel in URL tests
- `79f5fd0` refactor(frontend): delete ArtifactsPane (superseded by layout shell)
- `5526a50` chore(frontend): scrub stale ArtifactsPane references
- (this commit) Task 9 gate + journal

Gate state: ruff/mypy/pytest/frontend all green at this commit.
274 frontend tests / 33 files. Manual browser smoke is owed —
listed in PR / hand-off notes.
