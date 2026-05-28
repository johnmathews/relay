# Proposal — Run-detail view layout

**Status:** proposed (2026-05-28). Frontend-only; no backend, REST, SSE,
OTel, sentinel, or schema change. Targets `RunDetailView.vue` and the
panes under `frontend/src/components/runs/`.

## Problem

The current run-detail view (`frontend/src/views/RunDetailView.vue`,
633 lines) stacks every concern vertically: status header → prompt
disclosure → timeline → iter list → artifacts → worktree → children →
pause form. With even a modest run (≈40 events, 2 iters, 3 artifacts)
the page scrolls past two viewports and the visual hierarchy
collapses:

- **Top-level objects are not visually distinct.** The prompt that
  started the run, the iters that ran it, and the artifacts it
  produced are conceptually peers but render as three
  arbitrarily-ordered blocks separated only by an `h2`.
- **The timeline blends into the page.** It has no demarcated
  container; events appear to belong to whatever happens to be
  rendered above them.
- **The event-kind filter is hidden.** The "Display" gear (top-right
  of the timeline) is small, easy to miss, and its current state is
  not visible at a glance.
- **No clue what kind of event you're looking at.** Assistant text,
  thinking, tool calls, and signals all render as plain rows; the
  reader has to parse content to identify type.

## Goals

- Establish clear hierarchy: top-level objects (Overview, Iters,
  Artifacts, Children) in a persistent rail; details in a dedicated
  pane.
- Make the timeline a distinct, demarcated container that lives
  *inside* an iter view, not loose on the page.
- Surface the event-kind filter as a persistent, color-coded chip row
  that doubles as a legend.
- Preserve every operator-critical affordance currently in the view:
  Cancel, Resume (paused), `review_path` editing, follow-live
  behaviour, child-run cascade copy, artifact navigation from
  `artifact_edited` rows.
- Match established master-detail conventions (VS Code, Mail.app,
  Linear, Sentry) so the layout requires no learning curve.

## Non-goals

- Backend, REST, SSE, OTel, sentinel, or schema change. This is a
  frontend re-arrangement that reuses existing panes.
- Multi-pane editing, drag/drop, custom layouts. Master-detail only.
- Mobile-first. Localhost developer tool (ADR-12 single-user MVP) —
  desktop is the primary target; narrow viewports degrade gracefully.
- Feature-flagged rollout or A/B comparison. Single-user MVP — land
  the change directly.

## Layout

Two-column master-detail on desktop (≥900px). App-wide nav bar above.
Run-scoped header lives at the top of the right pane.

```
┌─────────────────────────────────────────────────────────────────┐
│  relay  ·  projects / <project-name>                            │  nav bar
├─────────────────┬───────────────────────────────────────────────┤
│ left rail       │ right pane                                    │
│ (≈ 280px fixed) │ (flex 1)                                      │
│                 │                                               │
│ • Overview      │ ┌─ Run header ──────────────────────────────┐ │
│                 │ │ status · #id · started · phase · iters · │ │
│ ITERS (n)       │ │ [Cancel] [⏵ Follow live]                  │ │
│   #1 · done     │ ├─ Pause banner (sticky, when paused) ──────┤ │
│ ▸ #2 · running  │ ├─ Filter chips (when body = timeline) ─────┤ │
│                 │ │  [Asst] [Think] [Tool] [Signal] [Other]   │ │
│ ARTIFACTS       │ │                                            │ │
│ ▸ eval-report…  │ │  Body (routed on selection)                │ │
│   plan.md       │ │                                            │ │
│ ▼ discussions/  │ │                                            │ │
│     260528-…    │ │                                            │ │
│                 │ │                                            │ │
│ CHILDREN (k)    │ │                                            │ │
│ (hidden if 0)   │ └────────────────────────────────────────────┘ │
└─────────────────┴───────────────────────────────────────────────┘
```

Below ~900px the rail collapses to a top selector dropdown (selected
item label + caret); tapping reveals the full rail as a slide-down.
Right pane fills the viewport. This is the standard responsive
master-detail pattern (Mail.app on iPad in portrait).

## Left rail

Sections in fixed order:

1. **Overview** — pinned single entry. Always present. Selected
   shows: prompt block + cross-iter "live" timeline of all events.
2. **Iters (n)** — one row per iter: `#seq · phase · status-badge`.
   Live indicator (●) on the currently-running iter. Selection scopes
   the right pane to that iter's events.
3. **Artifacts** — wraps the existing `FileTree.vue` /
   `FileTreeNode.vue` against the run-artifacts directory. Files
   collapse under folders (`discussions/`, etc.). Selection routes
   the right pane to a file viewer.
4. **Children (k)** — hidden when empty; otherwise one row per child
   run (`status · short-id · role`). Selection navigates to
   `/runs/<child-id>` rather than rendering in the right pane (a
   child run is its own top-level object).

Section headers are uppercase small-caps with a count chip. Each row
has a 24px left indicator strip carrying status color (matches
`StatusBadge` palette).

## Right pane

### Run header

Top of pane, sticky on scroll. Contains:

- Run id (`Run YYYYMMDD-HHMMSS-XXXX`, copy-on-click).
- `StatusBadge` + `RunHealthBadge` (the existing 14e badge — keeps
  live/slow/stalled affordance from ADR-45 heartbeat).
- Started (local time, via `formatStarted`).
- Iter count (`n / m`).
- Phase.
- Parent run chip (`ParentRunChip.vue`) when `parent_run_id != null`.
- **Cancel button** — visible whenever `status ∈ {running,
  awaiting_children}`. Cascade copy from 9e preserved: parent in
  `awaiting_children` reads "Cancel run and N children".
- **Follow-live pin (⏵)** — visible whenever `status ∈ {running,
  awaiting_children}`. Pinned by default on entry to a live run.

### Pause banner (sticky)

Whenever `status == 'paused'`, a banner sits between the run header
and the body, wrapping `PauseAnswerForm.vue` unchanged. The banner:

- Is amber-bordered (`#e0b341`) per the existing pause convention
  (`yellow-pause-borders-validated.md`).
- Is sticky in the right-pane scroll container so Resume is always
  reachable.
- On paused entry, the rail auto-selects the first `review_path`
  artifact (so the file the user is being asked to review is open
  next to the form). User can navigate away; the banner stays.

### Body — routed on selection

| Selection            | Body component               | Notes                                   |
|----------------------|------------------------------|-----------------------------------------|
| Overview             | `OverviewPanel`              | Prompt block + cross-iter live timeline |
| Iter `#n`            | `IterTimelinePanel`          | Existing `TimelinePane` scoped to iter  |
| Artifact `path`      | `ArtifactPanel`              | `FileViewer` (md/code/diff) + edit if `path ∈ signal_args.review_paths` |
| Child `<id>`         | (navigates to `/runs/<id>`)  | No in-place render                      |

`OverviewPanel` and `IterTimelinePanel` both render the
`EventKindFilter` chip row above the timeline body.

### Filter chips — `EventKindFilter`

Replaces the current "Display" menu. Persistent chip row, five
toggles, colored per the palette below. State is reflected in the
URL (`&kinds=tool,signal`); absent param = all on. Each chip shows
`<kind-color-dot> <label> · <count-in-current-scope>`.

The 5 kinds and color assignments (validated against the dark
palette; avoid amber per memory):

| Kind        | Hex       | Rationale                              |
|-------------|-----------|----------------------------------------|
| Assistant   | `#a78bfa` | Violet — distinct "agent voice"        |
| Thinking    | `#64748b` | Slate — internal monologue, recedes    |
| Tool calls  | `#38bdf8` | Sky — action; matches "running" family |
| Signals     | `#34d399` | Emerald — terminal / structural events |
| Other       | `#a1a1aa` | Zinc — neutral fallback                |

Color appears as: (a) the chip dot, (b) a 4px left border on each
timeline row, (c) a small kind label next to the seq. WCAG AA
contrast verified against `--surface-2` (current run-detail card bg)
in CSS pass.

Mapping of existing event kinds → chip:

- `assistant_text`, `assistant_delta` (pending) → **Assistant**
- `thinking` (if/when surfaced) → **Thinking**
- `tool_call`, `tool_result` → **Tool calls**
- `iter_started`, `iter_ended`, `run_started`, `run_ended`,
  `subagent_dispatch`, `subagent_return`, `child_runs_resolved`,
  `harness_session_ended` → **Signals**
- Everything else (`artifact_edited`, `usage`, etc.) → **Other**

### Tool-call detail drawer

`ToolCallCard.vue` is unchanged for the inline summary. A new
`ToolCallDetailDrawer.vue` slides in from the right (50% width) when
a tool-call row is clicked. The drawer renders the full
arguments/result with `CodeRender` / `MarkdownRender` /
`DiffRender`. Esc or backdrop click closes. Drawer state is **not**
URL-reflected (transient inspection state).

## URL contract

Selection and filter state live in the URL so refresh preserves
context and links are shareable:

- `/runs/:id` → smart default (see below)
- `/runs/:id?view=overview`
- `/runs/:id?view=iter:2`
- `/runs/:id?view=artifact:improvement-plan.md` (path
  URL-encoded; nested paths preserved: `discussions%2F260528-foo.md`)
- `/runs/:id?view=iter:2&kinds=tool,signal`

The `view` param is parsed in `RunDetailView`'s `setup`; the rail
emits `update:view` events that push to the router. Browser back /
forward works.

### Smart default selection

When `view` is absent from the URL:

- `status == 'paused'` → `artifact:<first review_path>` if present,
  else `overview`.
- `status ∈ {running, awaiting_children}` → `iter:<latest>` and pin
  Follow-live.
- `status ∈ {done, failed, cancelled}` → `overview`.

User-initiated rail selection always overrides smart default and
populates the URL.

## Follow-live behaviour

A pin button (⏵) in the run header. Behavior:

- For live statuses (`running`, `awaiting_children`), pinned by
  default on entry.
- While pinned, when a new iter appears (the events store sees
  `iter_started` for an iter not yet in the rail), the rail selection
  auto-promotes to the new iter.
- Manually clicking a specific iter row turns the pin off (the
  button visually un-pins).
- Clicking the pin again restores tailing — selection jumps to the
  latest iter immediately.
- Terminal statuses: pin button hidden.

This matches Datadog Live Tail and `kubectl logs -f` behaviour.

## Keyboard navigation

Power-user shortcuts on the run-detail view:

| Key       | Action                                         |
|-----------|------------------------------------------------|
| `j` / `↓` | Next item in rail                              |
| `k` / `↑` | Previous item in rail                          |
| `l` / `→` | Focus right pane                               |
| `h` / `←` | Focus rail                                     |
| `g o`     | Jump to Overview                               |
| `g i`     | Jump to first iter                             |
| `g a`     | Jump to first artifact                         |
| `/`       | Focus event-kind filter (chip row)             |
| `Esc`     | Clear filter / close drawer / unfocus rail     |
| `f`       | Toggle Follow-live pin                         |
| `c`       | Focus Cancel button (does not trigger)         |

Shortcuts use `@vueuse/core`'s `onKeyStroke` (already a transitive
dependency via Pinia Colada — verify in implementation phase or pull
it in directly). All shortcuts are no-op when focus is in a text
input / textarea / contenteditable.

## Empty states

| Context                          | Empty state copy                                            |
|----------------------------------|-------------------------------------------------------------|
| Iters section, no iters yet      | "Waiting for first iter…" + spinner if `status == 'running'`|
| Artifacts section, no files      | "No artifacts yet" (just-started run) or "—" (terminal)     |
| Children section, no children    | Section hidden entirely                                     |
| Overview timeline, no events     | "Run hasn't emitted any events yet" + live dot if running   |
| Iter timeline, iter has 0 events | "Iter started — no events yet" + live dot if iter is `running` |
| Artifact selected, file missing  | Existing 404 handling from 14c                              |
| Filter excludes all events       | "All events hidden by filter" + "Clear filter" button       |

## Accessibility

- Left rail is `role="listbox"` with `aria-orientation="vertical"`.
  Each row is `role="option"` with `aria-selected`. Sections are
  `role="group"` with an `aria-labelledby` pointing at the section
  header.
- Filter chip row is `role="toolbar"`; each chip is `role="button"`
  `aria-pressed`.
- Run header status badges carry `aria-label` ("Run status: running",
  "Live, last activity 2 seconds ago") so screen readers don't read
  the color dot in isolation.
- Tool-call drawer is `role="dialog"` `aria-modal="true"` with focus
  trapped while open; restores focus to the originating row on close.
- Color is never the sole information channel: kind chips carry text
  labels, status badges carry text status, timeline-row left borders
  duplicate the chip-dot color but the row also shows a kind label
  next to the seq.
- Contrast spot-check: each kind color passes WCAG AA (4.5:1) against
  the row background; verified in CSS pass with a contrast tool
  before merge.

## Component tree — delta from today

**New:**

- `frontend/src/components/runs/layout/RunSidebar.vue` — left rail.
  Sections: Overview / Iters / Artifacts (wrapping `FileTree`) /
  Children. Emits `update:view`.
- `frontend/src/components/runs/layout/RunRightPane.vue` — header +
  pause banner + routed body. Receives `view` + run state as props.
- `frontend/src/components/runs/layout/OverviewPanel.vue` — prompt
  block + cross-iter live `TimelinePane`.
- `frontend/src/components/runs/layout/IterTimelinePanel.vue` — thin
  wrapper around `TimelinePane` scoped to a single iter.
- `frontend/src/components/runs/layout/ArtifactPanel.vue` — wraps
  `FileViewer`; conditionally renders edit affordance when the file's
  path is in the paused iter's `signal_args.review_paths`.
- `frontend/src/components/runs/layout/PauseBanner.vue` — sticky
  amber-bordered container wrapping `PauseAnswerForm`.
- `frontend/src/components/runs/EventKindFilter.vue` — chip row.
  Two-way binding via URL query param.
- `frontend/src/components/runs/ToolCallDetailDrawer.vue` —
  slide-over for long tool-call results.

**Modified:**

- `frontend/src/views/RunDetailView.vue` — becomes thin layout
  orchestrator: parses `?view=` and `?kinds=`, owns smart-default
  resolution, hosts the nav bar + `RunSidebar` + `RunRightPane`,
  threads Follow-live pin state. Loses inline timeline / iters /
  artifacts markup; loses the current Cancel button slot (moves into
  `RunRightPane`'s header).
- `frontend/src/components/runs/TimelinePane.vue` — accept a
  `kindsFilter` prop (set of allowed chip categories) and apply it
  alongside the existing iter-scope filter. Each row gains a 4px
  left border colored by kind + a small kind label. The current
  internal "Display" menu is removed (state moves out to
  `EventKindFilter`).
- `frontend/src/components/runs/ToolCallCard.vue` — gain a "View
  full" affordance that opens `ToolCallDetailDrawer`.
- `frontend/src/components/runs/ArtifactsPane.vue` — becomes a
  smaller wrapper inside `RunSidebar`'s Artifacts section, delegating
  to `FileTree`. Or: pane is deleted entirely and `RunSidebar`
  consumes `FileTree` directly. Decided at implementation time based
  on what's reusable.

**Unchanged (load-bearing — wrapped, not rewritten):**

- `PauseAnswerForm.vue` (review_paths tabs, diff toggle, ApiError
  handling — 14c/14e/14f contract preserved).
- `StatusBadge.vue`, `RunHealthBadge.vue`, `ParentRunChip.vue`,
  `ChildrenPane.vue`, `SignalCard.vue`, `UsageRow.vue`.
- `FileTree.vue`, `FileTreeNode.vue`, `FileViewer.vue`,
  `MarkdownRender.vue`, `CodeRender.vue`, `DiffRender.vue`,
  `MermaidRender.vue`.
- The events store (`stores/events.ts`) and its dual-list contract
  (`KNOWN_EVENT_TYPES` × `INVALIDATING_KINDS`).
- The current-run file-browser store keyed by `run:<runId>`
  (`14e`).

## State management

- Selection (`view`) — derived from `useRoute().query.view`. No new
  store; the URL is the source of truth. `RunDetailView` exposes a
  computed `currentView` and an `setView(v)` helper that pushes to
  the router.
- Filter (`kinds`) — same shape: URL-derived computed + setter.
- Follow-live pin — local ref in `RunDetailView`; pin auto-engages on
  mount for live runs and auto-disengages on manual iter selection
  via a watcher on `currentView`.
- Tool-call drawer open/closed + payload — local ref in
  `RunRightPane`. Transient; not URL-reflected.

## What does NOT change

- Backend, REST API surface, SSE wire shape (envelope unwrap rules
  from 9f bug-fix sweep preserved), OTel pipeline, sentinel grammar.
- Event store invariants (single source of truth, append-only,
  envelope shape, `Last-Event-ID` replay).
- Dual-list contract: `KNOWN_EVENT_TYPES` (`sse.ts`) ×
  `INVALIDATING_KINDS` (`stores/events.ts`).
- `PauseAnswerForm.vue` internals — review_paths tabs, per-tab dirty
  state, diff toggle, `ApiError` handling. The form is wrapped, not
  rewritten.
- Workspaces / artifacts resolver (`get_run_artifacts_dir` under
  `project_root/.relay`, not `settings.data_dir` — 9f cross-cutting
  trap).
- Children pane logic (`useRunChildrenQuery` invalidation key
  `['runs','children',runId]` — 9e contract).

## Build sequence (high-level)

Detailed plan to be produced by `writing-plans`. Anticipated phases:

1. **Layout shell** — `RunDetailView` shape, `RunSidebar`,
   `RunRightPane`, URL plumbing, smart-default selection. No filter
   chips, no drawer, no new behaviour. Existing panes render inside
   the new shape unchanged. The view ships a working two-column
   layout end-to-end.
2. **Filter chips + color coding** — `EventKindFilter`,
   `TimelinePane` kind borders + labels, URL `&kinds=` reflection.
3. **Follow-live pin + smart default + keyboard nav** — `f` toggle,
   pin auto-engage/disengage rules, `j`/`k`/`g o`/etc.
4. **Pause banner** — extract `PauseAnswerForm` wrapping into
   `PauseBanner`, sticky behavior, paused-default artifact
   selection.
5. **Tool-call drawer** — `ToolCallDetailDrawer`, `ToolCallCard`
   trigger, focus trap, drawer animation.
6. **Responsive collapse** — narrow-viewport top-selector behavior
   below 900px breakpoint.
7. **Accessibility + empty-state polish** — ARIA pass, empty-state
   copy, contrast spot-check, screen-reader smoke test.

Each phase is independently shippable; the green gate
(`ruff`/`mypy`/`pytest` + `npm run check`) must pass at every phase
boundary. CLAUDE.md "MVP-acceptance-testing phase" pause applies —
this proposal lands as a documented proposal; build sequence runs
only when gates close.

## Open questions

- **Drawer vs. inline-expand for tool-call detail.** The decision
  was "drawer" but a future user-testing pass might show inline
  expand is preferred. Re-litigate after live use.
- **Artifact deletion / staleness.** If an artifact is deleted from
  disk between selection and refetch, the artifact panel should
  surface a clean "file no longer exists" — the 14c 404 handling
  covers this but the rail itself doesn't yet know to drop the
  entry. Probably an `artifact_removed` event kind eventually;
  out of scope here.
- **Cross-run navigation from a child rail row.** When clicking
  a child run, do we navigate fully (`router.push`) or preserve the
  parent's filter / pin state across the jump? Preserving makes
  comparisons across the tree easier but adds URL complexity.
  Default to plain `router.push` for v1.
- **Search / find within events.** Not in this proposal. If event
  volume grows past current expectations, a `?q=…` text search over
  the visible scope would be a natural addition.
