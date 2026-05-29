# Plan — Phase 14e (pause-for-review: audit polish — diff toggle, timeline navigation, OTel attr, fanout-docs phase-2 link)

**Status:** ready to execute
**Date:** 2026-05-23
**Source proposal:** `docs/proposals/pause-for-review.md` §"14e + 14f
— 2026-05-23 follow-up" (the resolved 14e bundle).
**Predecessors:** 14a (write endpoint + `artifact_edited` event), 14b
(sentinel `review_path` attribute), 14c (dashboard inline editor),
14d (engteam Phase-2 template emits `review_path`).
**Depends on:** 14a–14d on `main` and verified.
**Sibling sub-phase:** 14f (plural `review_paths` — opens
independently from 14e; either order works).

## Goal

Ship the "audit polish" bundle that surfaces *what was edited* during
a pause-for-review without changing any contract (no grammar, no
event-kind, no MCP, no schema). Four items land together:

1. **OQ-5 — in-editor diff toggle.** Right pane in `PauseAnswerForm`
   gains a `[ Preview | Diff ]` switch. Diff is disabled while the
   textarea is clean; renders dirty-vs-loaded-baseline via the
   existing lazy `diff2html` entry; baseline updates on a successful
   Save.
2. **OQ-6 — timeline-row navigation (reframed from "view diff").**
   Each `artifact_edited` row in `TimelinePane` becomes a click-
   target that opens the artifacts pane at the file's current on-disk
   content. Hashes stay inline as row metadata. **Not** a historical
   diff (impossible without breaking ADR-40 §B1).
3. **OTel scalar attribute** `relay.pause.artifacts_edited_count: int`
   on the **resumed iter's** `relay.iter` span. Count of
   `artifact_edited` events with `iter_id == <paused iter id>` at the
   moment the resumed iter starts. NOOP instrumentation ignores it.
4. **Skill-side fanout-docs janitor.**
   `skills/engineering-team/pi/phases/phase-2-planning.md` gains a
   blockquote cross-link to `../references/fanout.md` (the reference
   doc + cross-links from phase-1 / phase-3 already exist as of
   2026-05-22; phase-2 is the remaining gap). Closes the 9e deferred
   follow-up; the 9e block's "deferred" line in CLAUDE.md is removed.

14e is independently shippable: the diff toggle, the timeline link,
the OTel attr, and the skill-doc cross-link have no inter-dependency.
They are bundled because they share the theme "make the edit visible
without changing the contract".

## Locked decisions

- **OQ-5 baseline = loaded content (dirty-vs-loaded-baseline).**
  Single-user MVP (ADR-12) means there is no other writer, so
  "dirty-vs-server-current" and "dirty-vs-loaded-baseline" collapse
  to one comparison. No refetch on diff-open. Baseline updates on
  Save (the saved content becomes the new baseline; Diff tab disables
  until next dirty).
- **Diff renderer = the existing lazy `diff2html` entry** used by the
  artifacts pane for `.diff`/`.patch` files. Dynamic-imported on first
  Diff click. No new eager bundle weight (ADR-26 preserved).
- **OQ-6 = navigation, not diff.** ADR-40 §B1 deliberately does not
  preserve before-content in event payloads; a historical diff is
  unreconstructable without B3 (rejected). The row link opens the
  artifact's *current* state; hashes are metadata. The proposal's
  original "view diff" wording is honestly named as a missing
  capability.
- **OTel attr placement = the resumed iter's span at iter start.**
  Edits accumulate while the run is paused; the paused iter's span
  has already closed when edits land (count would be 0). The resumed
  iter's span carries `relay.pause.artifacts_edited_count` set at the
  start of the iter from a one-shot count query
  (`SELECT COUNT(*) FROM events WHERE iter_id = :paused_iter_id AND
  kind = 'artifact_edited'`). The attribute name makes the
  *semantically-preceding-pause* meaning explicit.
- **No new ADR.** 14e ships pure UX + observability + skill-doc; the
  contract surface (ADR-40 §A1/B1/§OQ-1) is unchanged.
- **Fanout cross-link rides 14e.** One blockquote in
  `phase-2-planning.md`; janitor work too small for its own sub-phase.

## What 14e does NOT do

- Does **not** change the sentinel grammar (14f opens that for OQ-2).
- Does **not** modify `compose_resume_prompt` (OQ-4 still parked).
- Does **not** change the event taxonomy. `artifact_edited` shape +
  payload + iter scoping are exactly as ADR-40 set them in 14a.
- Does **not** add an MCP tool.
- Does **not** modify the engteam skill's Phase-2 sentinel (already
  shipped in 14d; this plan only adds a *fanout* cross-link to the
  planning instructions, which is unrelated to pause-for-review).
- Does **not** add a per-edit OTel sub-span (rejected as duplicative
  of the event store; a scalar attribute is sufficient).
- Does **not** add B3 (event-payload edit content). The B1 audit gap
  is preserved and honestly named in the OQ-6 row.
- Does **not** modify the 14a write endpoint or its coupling check.

## File-by-file changes

### `frontend/src/components/runs/PauseAnswerForm.vue`

Add a right-pane view-mode toggle alongside today's preview:

- Two radio-style buttons or segmented control `[ Preview | Diff ]`
  rendered just above the right pane.
- Diff button is `disabled` while the textarea content is byte-equal
  to the loaded baseline (a `dirty` computed property). Tooltip when
  disabled: "No unsaved changes — diff is empty".
- When Diff is selected, lazy-load `diff2html` (mirror the artifacts-
  pane import shape; same chunk if possible — verify via
  `npm run build` chunk listing). Render `diff2html.html(unifiedDiff,
  { drawFileList: false, outputFormat: 'side-by-side', matching:
  'lines' })` with the unified diff computed from `loadedBaseline` →
  `textareaContent` (a small `diff` helper — use `jsdiff` if already
  bundled by `diff2html`'s peer; otherwise hand-roll a line-by-line
  unified-diff string, which is straightforward for the markdown
  case). Quick verification step in the implementing session:
  `grep -r "diff2html\|createPatch\|jsdiff" frontend/` to confirm the
  artifacts-pane shape before duplicating logic.
- On successful Save, the response handler (already updates the
  saved-badge) also sets `loadedBaseline = textareaContent` so the
  Diff tab returns to disabled.
- On Discard, reset textarea to `loadedBaseline` (already today's
  behaviour); Diff tab disables.
- The Preview tab is the default selection on mount.

UI invariants to preserve (already locked in 14c):

- Resume button disabled while a Save is in flight (unchanged).
- 404 → "Create at this path" banner unchanged.
- 415 binary case unchanged (the diff toggle should not render at
  all in the binary state — the editor is not editable).
- The question/answer block beneath the editor is byte-identical.

### `frontend/src/components/runs/TimelinePane.vue`

The `artifact_edited` row (rendered at lines ~339-345 today as a
small inline row beside `UsageRow`) gains a click handler:

- The row wraps in a `<RouterLink>` (or programmatic `router.push`)
  to the artifacts-pane route for the file at `payload.path`
  (re-use whatever route param the artifacts pane currently
  consumes; confirm via `grep -n "useRoute\|artifactPath" frontend/
  src/components/runs/` in the implementing session).
- Hover state: subtle cursor pointer + background change to signal
  affordance (match existing TimelinePane hover styles if any).
- Hashes remain inline as today (path · sha-before… → sha-after… ·
  editor). For a create (`sha256_before` null/`∅`), the inline render
  is unchanged.
- For a run whose status is terminal (`done` / `failed` / `cancelled`),
  the link still works — it opens the artifact at the current on-disk
  state, which may differ from the recorded `sha256_after` if the
  artifact was later edited out-of-band or deleted (the artifacts
  pane's existing 404 surface handles deletion honestly).

The row does **not** claim to render a diff. No tooltip text uses the
word "diff". The link affordance is "open this artifact".

### `src/relay_v2/observability/otel.py`

`Instrumentation.iter_span` (active path) gains an optional
`paused_predecessor_iter_id: int | None = None` keyword argument.
When non-`None` at iter-start, the implementation:

- Issues a small synchronous count query
  (`SELECT COUNT(*) FROM events WHERE iter_id = :paused_iter_id AND
  kind = 'artifact_edited'`) via the orchestrator's existing session
  (passed through the same seam the rest of the OTel mirror uses —
  inspect the current `iter_span(...)` signature for the session
  carrier).
- Sets `relay.pause.artifacts_edited_count: <count>` on the span at
  start time as an additional attribute alongside `relay.iter_seq`.

The orchestrator (`src/relay_v2/orchestrator/core.py` or wherever
`_run` invokes `iter_span`) detects a resumed iter via the existing
`pause_resolved` machinery — when an iter starts and the immediately
preceding event on the run is `pause_resolved`, the preceding paused
iter's id is recoverable from `signal_args` or the resume bookkeeping
(`grep -n "pause_resolved\|paused_iter\|resume_run" src/relay_v2/` in
the implementing session locks the precise plumbing).

NOOP path (`NoopInstrumentation.iter_span`): accepts and ignores the
new kwarg. No-op invariant preserved (no provider, no exporter, no
network call — same shape as the 9f `parent_iter_ctx` kwarg).

Attribute namespace: `relay.pause.*` is new but follows the existing
`relay.iter_seq`, `relay.run.*` pattern. Per ADR-29 we do not import
`opentelemetry-semantic-conventions`; this is a custom relay attribute.

### `skills/engineering-team/pi/phases/phase-2-planning.md`

Add a single blockquote near the step where the planner identifies
work units (around line 154's "independent" mention, before the unit-
table is finalised). Shape mirrors the phase-1 and phase-3 fanout
blockquotes already in the skill:

> **Coarse-grained parallelism via fanout.** When the plan identifies
> 2+ genuinely independent units whose results merge cleanly into a
> single decision, consider emitting `[[engteam:fanout]]` from the
> closing step instead of a `pause-for-input` handoff to Phase 3 and
> letting each unit run as its own child run. See
> `../references/fanout.md` for the decision criteria and the
> grammar; this is appropriate when the parallel exploration is
> worth the fixed cost of a fresh pi process per child.

(Exact wording can adapt to surrounding context; the load-bearing
elements are: (a) link to `../references/fanout.md`, (b) name the
decision criterion ("genuinely independent + merges cleanly"), (c)
warn about the fixed cost.)

The blockquote is **purely informational**. No template change to
Step 4 (the pause sentinel) or Step 5 (the handoff sentinel). The
engteam Phase-2 workflow continues to emit `pause-for-input` with
`review_path="improvement-plan.md"` as 14d locked.

### `CLAUDE.md` — "Current state"

Append a **14e paragraph** at the end of the 14d paragraph. Shape:

> **Phase 14e** (2026-05-23,
> [docs/plans/2026-05-23-pause-for-review-14e.md](docs/plans/2026-05-23-pause-for-review-14e.md))
> lands the "audit polish" bundle for the pause-for-review arc with
> no contract change. `PauseAnswerForm.vue`'s right pane gains a
> `[ Preview | Diff ]` toggle (Diff disabled while the textarea is
> clean; renders dirty-vs-loaded-baseline via the existing lazy
> `diff2html` entry; baseline updates on Save). `TimelinePane.vue`'s
> `artifact_edited` rows become click-targets that navigate to the
> artifacts pane at the file's *current* on-disk state — honestly
> framed as navigation, not a historical diff (ADR-40 §B1 deliberately
> doesn't preserve before-content). `relay.pause.artifacts_edited_count`
> lands as a scalar attribute on the **resumed iter's** `relay.iter`
> span (single int, low cardinality; NOOP `Instrumentation` ignores
> it). And the deferred 9e fanout-docs follow-up closes:
> `skills/engineering-team/pi/phases/phase-2-planning.md` gains a
> blockquote cross-link to `../references/fanout.md` (the reference
> doc + the phase-1/phase-3 cross-links already shipped 2026-05-22 —
> phase-2 was the remaining gap). No new ADR; no grammar change; no
> event-kind change; no MCP change. Sibling sub-phase 14f (plural
> `review_paths`) lands ADR-41 and the only contract change in the
> 14e/14f bundle. OQ-4 stays parked pending 14d live-acceptance
> evidence (proposal §"Open questions").

**Also remove** the line in the existing 9e block that reads
"skill-side fanout docs … remain a deliberate small follow-up PR
(deferred from 9e — UI-only, no contract change)" — the follow-up
is no longer deferred (closed in 14e).

### `docs/spec.md`

- §9.1 (Dashboard pause action) — short paragraph noting the
  `[ Preview | Diff ]` toggle and that diff is dirty-vs-loaded-
  baseline. Match the existing 14c "review-pane mode" paragraph's
  voice.
- §3.2 / §7 are **unchanged** (no taxonomy or REST change).

### Tests

- **Frontend (`frontend/tests/`):**
  - `PauseAnswerForm.spec.ts` — new cases:
    - Mount with `review_path="plan.md"`, content loaded → Diff tab
      disabled, Preview tab active.
    - Dirty the textarea → Diff tab enabled. Click Diff → diff2html
      renders (assert on a rendered DOM selector, not exact HTML).
    - Save succeeds → loadedBaseline updates → Diff tab disables again.
    - Discard → Diff tab disables.
    - Binary 415 path → no toggle rendered at all.
  - `TimelinePane.spec.ts` — new cases:
    - `artifact_edited` row renders as a clickable link to the
      artifacts route for `payload.path`.
    - Create-path row (`sha256_before` null) still navigates;
      hash inline shows `∅`.
- **Backend (`tests/observability/`):**
  - `test_otel_export.py` (or a new `test_otel_pause_attr.py`) —
    InMemorySpanExporter: a resumed iter whose preceding pause had
    0 / 1 / 3 `artifact_edited` events sets
    `relay.pause.artifacts_edited_count` to 0 / 1 / 3 on the resumed
    iter's `relay.iter` span. NOOP path: no attribute, no error.
  - Existing tests stay green (no behavioural change to non-paused
    runs).

## ADR — none

14e is purely additive UX + observability + skill-doc. No contract
change. ADR-40 covers the pause-for-review contract; ADR-29 covers
the OTel mirror seam (and the new scalar attribute fits its
"additive, no control-flow change" pattern).

## Verification

Pre-merge gate (required):

- `uv run pytest` — must stay green (no expected behavioural changes
  outside the new OTel attribute path).
- `uv run ruff check .` — clean.
- `uv run mypy src/relay_v2/` — clean (the new `iter_span` kwarg has
  a typed signature; NOOP must satisfy the Protocol).
- `cd frontend && npm run check` — clean (eslint --max-warnings 0,
  vue-tsc, vitest).

Manual smoke (one engteam-style pause is enough; the live
`PI_INTEGRATION=1` acceptance is 14d's responsibility, not 14e's):

- Start a scripted-harness pause with `review_path="plan.md"`.
- Open the run-detail view; toggle Preview/Diff; verify Diff renders
  unified-diff output when dirty.
- Save; verify Diff disables.
- Click the `artifact_edited` timeline row; verify navigation to the
  artifacts pane at `plan.md` with the current content.
- Resume; verify the resumed iter's Langfuse span carries
  `relay.pause.artifacts_edited_count` (or, in a self-contained
  pytest, via `InMemorySpanExporter`).

## Acceptance criteria

- Diff toggle ships and behaves per the locked decisions above
  (vitest cases pass).
- `artifact_edited` timeline rows are click-targets navigating to
  the artifacts pane (vitest cases pass).
- `relay.pause.artifacts_edited_count` lands as a scalar attribute
  on the resumed iter's `relay.iter` span (pytest cases pass).
- `phase-2-planning.md` cross-links to `../references/fanout.md` as
  documented.
- `CLAUDE.md` "Current state" gains a 14e paragraph and the 9e
  block's "deferred fanout-docs" line is removed.
- `docs/spec.md` §9.1 notes the diff toggle.
- `docs/proposals/pause-for-review.md` §"Open questions" OQ-5 and
  OQ-6 annotations + §"14e + 14f" section reflect the as-shipped
  state (already updated in this plan's preceding commit/edit; if
  not, ensure consistency).
- `uv run pytest`, `ruff`, `mypy --strict`, `npm run check` all green.

## Out of scope for 14e (recap)

- Plural `review_paths` — **14f** (sibling sub-phase).
- Per-edit annotation in `compose_resume_prompt` — **deferred until
  14d live-acceptance** records demand (proposal §OQ-4).
- B3 (event-payload edit content) — rejected by ADR-40 §B1; not
  reopened.
- Per-edit OTel sub-spans — rejected as duplicative of the event
  store.
- New MCP tool — not in 14e (frozen surface).
- Editor upgrade beyond `<textarea>` — out of scope; ADR-26 bundle
  budget preserved.

## Commit shape

One commit (or up to three small commits if reviewers prefer; each
shipped state leaves `main` working):

```
feat(14e): diff toggle, timeline navigation, OTel pause attr, fanout phase-2 link

- PauseAnswerForm.vue: right pane gains [Preview|Diff] toggle;
  Diff disabled while clean; renders dirty-vs-loaded-baseline via
  lazy diff2html; baseline updates on Save
- TimelinePane.vue: artifact_edited rows become click-targets
  navigating to the artifacts pane at the file's current state
  (honest navigation, not historical diff — ADR-40 §B1)
- observability/otel.py: relay.pause.artifacts_edited_count on the
  resumed iter's relay.iter span (scalar, set at iter start;
  NOOP path unchanged)
- phase-2-planning.md: blockquote cross-link to references/fanout.md
  (closes the deferred 9e fanout-docs follow-up; reference doc +
  phase-1/phase-3 cross-links already shipped 2026-05-22)
- spec.md §9.1: note the diff toggle
- CLAUDE.md: 14e paragraph; remove the 9e fanout-docs-deferred line

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Notes for the executing session

- **Verify the diff2html import path before touching it.** The
  artifacts pane already has the lazy entry; duplicate the import
  shape exactly, do not introduce a new chunk. Quick verification
  step: `grep -rn "diff2html\|jsdiff" frontend/src/`.
- **The OTel attr query is one count query.** Don't reach for an
  ORM relationship; a direct `SELECT COUNT(*)` is the cheapest path
  and matches how `_aggregate_usage` already reads. If the existing
  `iter_span` signature doesn't currently take a DB session, plumb
  one through via the same seam the rest of the mirror uses (the
  9f `parent_iter_ctx` plumbing is the recent reference example).
- **The TimelinePane row stays a `div` if `RouterLink` adds chrome
  conflicts.** Use `router.push()` programmatically inside a click
  handler if the inline-row layout breaks under `<a>` semantics.
  Don't change the row's visual shape — it should look identical
  to today's row, only become clickable.
- **The fanout cross-link is purely informational.** Do not change
  Step 4's pause sentinel, Step 5's handoff, or any unit-table
  formatting in phase-2-planning.md. The blockquote sits *near* the
  unit-identification step; placement is judgment.
- **CLAUDE.md edit removes the deferred-follow-up line in the 9e
  block.** Don't remove the 9e block itself. The line to delete is
  the literal "skill-side fanout docs (`skills/engineering-team/pi/
  references/fanout.md` + phase-doc cross-links) remain a deliberate
  small follow-up PR (deferred from 9e — UI-only, no contract
  change)" sentence.
- **No new ADR.** If the implementing session feels a decision
  warrants one (e.g. "should we always default Diff when dirty?"),
  resist scope creep — that is a 14g question, not 14e.
- **14e is independently shippable from 14f.** Either can land first.
  If 14f lands first, 14e's diff toggle and timeline row behaviour
  for N>1 should be a no-op (a single-path tab bar collapses to
  today's layout; 14e doesn't touch tabs). If 14e lands first, 14f
  layers on top cleanly (tabs wrap the existing pane, diff toggle
  becomes per-tab).
