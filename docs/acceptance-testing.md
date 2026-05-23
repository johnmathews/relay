# Acceptance testing — MVP gate

**Status:** in progress (started 2026-05-23).
**Scope owner:** John.
**Last code shipped before this phase:** 14f (commit `1752b8a`, ADR-41).

This document is the **single source of truth** for the acceptance-
testing phase that follows the 14f ship. Print it, tick items off as
you go, log bugs in §3, and commit updates as gates close. It is
*not* dated — it is the live tracker, not a journal entry. Per-gate
journal entries (date-stamped) capture what actually happened during
a session and link back to this document's checklist items.

We are deliberately **NOT** building new features during this phase.
The post-MVP sketches in `docs/plan.md` (Phases 9 remote-access, 10
container-per-run, 11 multi-user, 12 scheduled-runs, 13 subagent-
dispatch [superseded by 9a–9f], 15 prompt-library-UI) are parked.
The exit criterion is in §4.

The justification is in `CLAUDE.md` "Current state": ADR-31, the
post-9g bug-fix sweep (worktree-under-wrong-project,
`KNOWN_EVENT_TYPES` silently dropping SSE, pi-vs-Anthropic token-name
mismatch), and the 14c unhandled-rejection regression all came from
real use, not from review of the code. The pace of shipping has run
ahead of the pace of exercise. This document closes that gap.

---

## 1. Acceptance gates

These are the named, journal-attested gates carried forward from
earlier phases per ADR-30 (automated CI for the deterministic half;
manual journal-attested for the real-pi / live-Langfuse half). Both
gates must close before the MVP-acceptance phase ends (§4).

### 1.1 — 9f live Langfuse trace-tree

**What:** Verify that a real fanout-join cycle renders as **one
connected tree** in the Langfuse UI (not three disconnected
`relay.run` roots). The 9f InMemorySpanExporter assertion already
runs in CI; this gate is the eyeballs-on-the-UI confirmation.

**Status: PASSED 2026-05-22** — see
[`journal/260522-9f-langfuse-acceptance.md`](../journal/260522-9f-langfuse-acceptance.md)
for the original attestation. That entry's narrative is rich: the
"What was verified" section confirms both ADR-38 promises, and the
journal lists three regressions the live acceptance surfaced — all
three were addressed in the post-9g bug-fix sweep (`UsageRow` token
names, worktree under wrong project, `KNOWN_EVENT_TYPES` silently
dropping live SSE). **Read that entry first** before deciding
whether to re-run this gate.

**Recommended action for this phase:** **re-exercise as a regression
check.** The 9f attestation predates 9g + the post-9g sweep + 14a–14f;
this run confirms the post-MVP polish didn't break the trace-tree
shape. Treat this section's checklist as the protocol; if a regression
appears, log it in §3 and link the original entry as context.

**Prerequisites:**
- [ ] Self-hosted Langfuse running (see `docs/langfuse-compose.example.yml`).
- [ ] `RELAY_OTEL_EXPORT=langfuse` set; `RELAY_LANGFUSE_HOST` /
      public+secret keys exported.
- [ ] `PI_AGENT_SDK=1` exported.
- [ ] A real project registered (not the demo fixture — engteam
      Phase 1 must actually have work to do).

**Steps:**
- [ ] Start a run that the engteam skill will fanout at the end of
      Phase 1 (the audit phase). Drive it until the parent emits the
      `fanout` sentinel.
- [ ] Let the children dispatch, run, and complete.
- [ ] Let the synthesizer (parent resume) run and the run reach a
      terminal state.
- [ ] Open Langfuse UI; locate the trace.

**Acceptance:**
- [ ] Trace has **one root** (`relay.run` for the parent).
- [ ] Children's `relay.run` spans appear **under** the parent's
      closing `fanout` iter span (not as siblings of the root).
- [ ] Parent's synth-phase `relay.run` span appears **under the
      same closing fanout iter** as the children (not as a sibling
      of the parent's pre-fanout iters).
- [ ] `relay.tool_call` spans appear under their owning iter.
- [ ] Token / cost attributes populated on iter spans (from the
      `SessionEnded.messages[].usage` path — ADR-18 keys
      `input` / `output` / `cacheRead` / `cacheWrite` / `totalTokens`
      and `cost.total`).
- [ ] No orphan / detached spans in the trace.

**Close-out:**
- [ ] Update `journal/260522-9f-langfuse-acceptance.md` with screenshots
      (or a description of the trace tree) and the attestation date.
- [ ] Commit the journal update.

---

### 1.2 — 14d engteam `PI_INTEGRATION=1` end-to-end

**What:** Drive the engteam skill against a real project through
the full Phase-1 → Phase-2 → resume cycle. Confirm the
pause-for-review arc (14a → 14f) works for the operator: the
dashboard renders the inline editor, edits are saved, resume picks
up the edits, OTel attributes populate.

**Status: PENDING.** See
[`journal/260523-14d-live-acceptance.md`](../journal/260523-14d-live-acceptance.md)
for the protocol the prior session worked out and context on what's
already been attested (the template edit + automated gate landed in
that session; only the live `PI_INTEGRATION=1` run is outstanding —
deliberately deferred to the operator). The "Status" and "What this
session attested" sections of that entry are the right narrative
context if a step below feels ambiguous.

**Prerequisites:**
- [ ] Pi v0.74.0 installed; `PI_AGENT_SDK=1` exported.
- [ ] Engteam skill installed in target project (`relay install-skill
      --project <path>`).
- [ ] Self-hosted Langfuse running (optional, but recommended — both
      gates can be attested in one run).
- [ ] A real project with at least one concrete improvement target.

**Steps:**
- [ ] Start a run via the dashboard's New-Run wizard with the
      engteam Phase-1 prompt.
- [ ] Let Phase 1 complete; verify the `handoff` lands and a fresh
      iter begins Phase 2.
- [ ] Phase 2 produces `improvement-plan.md` and emits
      `pause-for-input` with `review_path="improvement-plan.md"`.
- [ ] Dashboard shows the paused run with the inline review pane.

**Acceptance — review pane:**
- [ ] Pane fetches and renders `improvement-plan.md`.
- [ ] Single-pane layout (no tab bar — 14f preserves byte-identical
      14c behaviour for N == 1).
- [ ] Markdown preview on the right pane renders correctly.
- [ ] Dirty the textarea; the Diff tab enables.
- [ ] Click Diff; verify the unified diff renders dirty-vs-loaded.
- [ ] Save the edit; "Edited at HH:MM:SS" badge appears.
- [ ] An `artifact_edited` event row appears in the timeline with
      the path, pre/post sha-short, and editor.
- [ ] Click the timeline row; verify the artifacts pane navigates
      to `improvement-plan.md` and shows the current on-disk content.
- [ ] Enter a non-empty answer and click Resume.

**Acceptance — resume:**
- [ ] Run transitions paused → running.
- [ ] Resumed iter re-reads the edited `improvement-plan.md` and
      proceeds (the engteam Step-4 "Re-read it in full — the user
      may have edited it" instruction is load-bearing — verify the
      next agent output references the edited content, not the
      original).
- [ ] Run completes with `done` (or proceeds to another phase
      transition).

**Acceptance — OTel pause attribute (14e):**
- [ ] In Langfuse (or via captured trace), the resumed iter's
      `relay.iter` span carries `relay.pause.artifacts_edited_count`
      = number of saves during the pause window (1 in the simple
      case; >1 if you saved multiple times before resuming).

**Close-out:**
- [ ] Update `journal/260523-14d-live-acceptance.md` with the
      attestation date, a per-section pass/fail summary, and any
      observations.
- [ ] Commit the journal update.

---

## 2. Exercise sweep — feature-by-feature

Beyond the named gates, drive every MVP-shipped feature against a
real project (the same project as §1 is fine — re-use is encouraged).
The point is to surface bugs that no unit test catches. Each item
below should be exercised **interactively via the dashboard or MCP**,
not via scripted tests.

For each item: tick when exercised, log any bug surfaced into §3.

### 2.1 — Run lifecycle (terminal sentinels)

- [ ] Simple single-iter `done` — start a run with a prompt that
      finishes in one iter; verify `run_ended` lands, dashboard
      shows the terminal status.
- [ ] Multi-iter `handoff` chain — start a run that needs ≥ 2 iters
      via the engteam phase chain.
- [ ] `pause` without `review_path` — the existing pre-14b minimal
      form renders (no review pane).
- [ ] `pause` with single `review_path` (14b/14c) — see §1.2.
- [ ] `pause` with plural `review_paths` (14f) — requires a custom
      skill OR manual sentinel emission. If no real skill emits
      plural yet, drive this via a scripted-harness or via emitting
      the sentinel manually in a test session.

### 2.2 — Cancel paths

- [ ] Cancel a running run (mid-iter) — verify `run_ended` lands
      with `summary: "supervisor shutdown"` or similar; the loop
      task exits cleanly.
- [ ] Cancel a paused run — verify the row transitions paused →
      cancelled without re-entering the loop.
- [ ] Cancel an `awaiting_children` parent — verify the cascade
      finalises every descendant (depth-first); the dashboard
      Cancel button label reads "Cancel run and N children"; no
      stray `iter_started` events from queued-but-not-started
      descendants.

### 2.3 — Fanout-join

- [ ] Fanout with 2 children, both `done` — verify
      `subagent_dispatch` events on the parent, `run_started` on
      each child, `subagent_return` + `child_runs_resolved` when
      the last child settles, synthesizer iter on the parent.
- [ ] Fanout with one child failing — verify the synthesizer still
      runs (orchestrator does not auto-fail the parent); the trailer
      includes the failed child's status; agent decides via the
      trailer how to respond.
- [ ] Restart with paused run on disk — kill the server, restart,
      verify the paused row is preserved and resume still works.
- [ ] Restart with `awaiting_children` parent — verify orphan
      recovery cancels the parent AND cascades to descendants
      (ADR-34 V1 non-goal: in-flight fanout is not recovered).

### 2.4 — Dashboard navigation

- [ ] Hub → Project → Run detail navigation works; Back / forward
      doesn't drop SSE.
- [ ] Children pane (9e) renders for a parent run; click a child
      navigates to that run's detail view; Parent chip on the child
      navigates back.
- [ ] "Show child runs" toggle on the Project Runs pane hides /
      shows children (default hidden via `include_children=false`).
- [ ] Artifacts pane lists `.relay/runs/<id>/` contents; markdown
      / code render with syntax highlighting; binaries offer a
      download link.
- [ ] Diff toggle on artifacts pane renders a unified diff (when
      applicable).
- [ ] Timeline `artifact_edited` row click navigates to the file
      in the artifacts pane (14e).
- [ ] Worktree pane shows `worktree_path` / `branch` (Phase-4
      degraded mode is acceptable).
- [ ] Prompts CRUD — create / list / update (bumps version) /
      delete a prompt; verify versions list shows history.
- [ ] Project register / unregister — verify unregister leaves
      files on disk (spec §7).

### 2.5 — MCP

- [ ] Register relay MCP server in Claude Code (or other MCP
      client) per `docs/mcp-config.example.json`.
- [ ] `relay__list_runs` — verify it returns runs (and includes
      children by default — `include_children=True` per the 9e
      decision).
- [ ] `relay__get_run` — verify it returns the run with the
      expected status / counts.
- [ ] `relay__start_run` — start a run via MCP; verify it appears
      in the dashboard.
- [ ] `relay__pause_response` — answer a paused run via MCP;
      verify resume.
- [ ] `relay__cancel_run` — cancel from MCP.
- [ ] `relay__tail_events` — stream events from MCP; compare
      against the dashboard timeline.
- [ ] `relay__read_artifact` — read a file from MCP; verify it
      matches the dashboard's artifacts pane.

### 2.6 — Observability

- [ ] `RELAY_OTEL_EXPORT=none` (default) — verify zero network
      calls, zero provider/exporter construction (ADR-29 strict
      no-op).
- [ ] `RELAY_OTEL_EXPORT=langfuse` — verify spans flow to Langfuse;
      see §1.1 for the trace-tree assertion.
- [ ] `harness_session_ended` event (9g) appears in the timeline
      with `stop_reason` + summed token usage (UsageRow rendering;
      post-9g bug 1 fix).
- [ ] Token usage in the UsageRow is non-zero and matches Langfuse.

### 2.7 — CLI

- [ ] `relay serve` starts the daemon on `127.0.0.1:7800`.
- [ ] `relay --version` reports the right version.
- [ ] `relay install-skill --project <path>` installs the engteam
      skill into `<path>/.claude/skills/`.

---

## 3. Bug log

Log every bug surfaced during this phase. If a bug is shipped as
its own commit, link the commit hash. If a bug is parked, note why.

Severity:
- **High** — blocks acceptance (data loss, crash, security, wrong
  behavior in a documented path).
- **Medium** — workaround exists; should be fixed before next ship.
- **Low** — cosmetic / non-blocking.

| Date | Severity | Area | Symptom | Fix commit / status |
|---|---|---|---|---|
| | | | | |

---

## 4. Definition of done

The acceptance-testing phase closes when **all** of the following
are true:

- [ ] §1.1 (9f Langfuse) gate re-exercised as a regression check;
      the original 2026-05-22 PASS still holds, or any new
      regression has been fixed and re-confirmed.
- [ ] §1.2 (14d engteam) gate journal-attested for the first time.
- [ ] §2 exercise-sweep complete; every checkbox ticked.
- [ ] §3 bugs of severity **High** all closed (fixed and merged).
- [ ] §3 bugs of severity **Medium** triaged — either closed or
      explicitly deferred to a named follow-up.
- [ ] §3 bugs of severity **Low** accepted-or-deferred.

When all six are true:

- [ ] Tag the release: `git tag v0.1.0 -m "MVP acceptance complete"`.
- [ ] Update `docs/motivation.md` "What 'done with MVP' looks like"
      section with the attestation date.
- [ ] Add a closing journal entry summarising the phase
      (`journal/YYMMDD-mvp-acceptance-complete.md`).
- [ ] Resume feature work. Pick from post-MVP phases in
      `docs/plan.md` (remote access / container-per-run /
      multi-user / scheduled runs / prompt library UI) per
      priority discussion.

---

## Known doc gaps (closed)

All four items from the 2026-05-23 audit are closed. Recorded here
as a forward-reference for future readers; entering the
MVP-acceptance phase with no open doc gaps.

- ~~No standalone `docs/fanout.md` operational runbook.~~ Closed —
  `docs/fanout.md` written: lifecycle, sentinel grammar, dashboard,
  cancellation, restart, OTel trace tree, limits, troubleshooting.
  Cross-linked from `README.md` and `docs/getting-started.md`.
- ~~`frontend/README.md` not refreshed.~~ Closed — added
  post-MVP component overview (PauseAnswerForm review mode,
  UsageRow, ParentRunChip, ChildrenPane, DiffRender),
  load-bearing invariants section (dual-list, vitest swallower,
  pi-flavoured token names), and a "verify before commit" note
  with the `tail` exit-code hazard.
- ~~ADR-30 carries the pre-rename ghcr image name.~~ Closed —
  ADR-42 appended documenting the GHCR repo + image rename
  `relay-v2 → relay` under the append-only convention; ADR-30 is
  left as-is per CLAUDE.md.
- ~~`docs/motivation.md` Goal 3 doesn't reflect inline-editor
  workflow.~~ Closed — Goal 3 rephrased to name the 14a–14f review
  pane explicitly and change the cycle from
  "browse-render-review-start" to "browse-render-review-edit-resume".

## Appendix — quick refs

- **Start the daemon:** `uv run relay serve` (binds `127.0.0.1:7800`).
- **Frontend dev:** `cd frontend && npm run dev` (proxies `/api` →
  `:7800`).
- **Run the gate locally:** `uv run pytest && uv run ruff check . &&
  uv run mypy && (cd frontend && npm run check > /tmp/check.log 2>&1;
  echo $?)` — never pipe `npm run check` to `tail`; redirect to a
  file and inspect (the pipe-tail footgun in `~/.claude/projects/.../
  memory/exit-code-via-pipe-tail.md`).
- **PI integration tests:** `PI_INTEGRATION=1 uv run pytest tests/
  -k pi_e2e`. Without the env var, the three pi-e2e tests are
  skipped; this is the gate for the engteam end-to-end runs.
- **Langfuse self-host:** `docs/langfuse-compose.example.yml`.
- **MCP client config:** `docs/mcp-config.example.json`.
- **Operational refs:** `docs/harness.md`, `docs/orchestrator.md`,
  `docs/api.md`, `docs/dashboard.md`, `docs/mcp.md`, `docs/skills.md`,
  `docs/observability.md`.
- **Canonical design:** `docs/spec.md` (current contract);
  `docs/decisions.md` (ADR-1 through ADR-41).
- **The plan (post-MVP roadmap):** `docs/plan.md` Phases 9 onward
  (parked during this phase).
