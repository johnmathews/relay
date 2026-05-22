# Pause-for-review live acceptance protocol (Phase 14d)

**Date:** 2026-05-23
**Phase:** 14d — pause-for-review skill activation
**ADRs:** ADR-40 (pause-for-review contract), ADR-30 (manual journal-attested
acceptance pattern, mirrors 9f's Langfuse-UI gate and the 14c §"Manual smoke"
note).
**Predecessors on `main`:** 14a (write endpoint + `artifact_edited` event,
`dfefb87`), 14b (sentinel `review_path` attribute, `94c1cc0`), 14c (dashboard
inline editor, `62a9f5c`).

## Status

**Template edit + automated gate: shipped this session.** Live
`PI_INTEGRATION=1` engteam attestation: **pending operator** — to be run
by the user (this session deliberately does not spawn pi; per the 14d
scope, the activation IS the template change, the live demo is the
ADR-30 manual gate).

## What this session attested

- `skills/engineering-team/pi/phases/phase-2-planning.md` Step-4 closing
  sentinel now emits `review_path="improvement-plan.md"` alongside the
  existing `id`/`question` attributes, on a single line as required by
  the line-anchored parser (`harness/signaling/sentinels.py:45` —
  `_PAUSE_RE` matches `^\[\[engteam:pause-for-input[ \t]` per-line, and
  `_REVIEW_PATH_RE` searches the same line; multi-line attribute
  formatting would silently drop the attribute).
- The Step-4 "Notes:" block gains one paragraph naming the new
  attribute, pointing at `../references/sentinels.md` §"Reviewable
  pauses", noting `$RELAY_RUN_DIR`-relative semantics, and noting that
  omitting the attribute keeps pre-14b behaviour.
- The Step-5 handoff template (line 264 region) is byte-identical
  — handoffs do not take `review_path` by design (unattended path; no
  human review moment).
- The Phase-2 prompt body inside the `prompt-start`/`prompt-end` pair
  is byte-identical — the load-bearing "Re-read it in full — the user
  may have edited it" instruction (lines 222-228) is preserved
  verbatim. This is the mechanism by which the resumed iter picks up
  the operator's edits, given ADR-20's fresh-context-per-iter (a
  resumed pi process only sees the edit because it re-reads from disk;
  there is no session-state carry-over).
- No other skill file touched. No backend, frontend, MCP, sentinel,
  or test file touched. The 14d scope fence held.

## Automated gate

- `uv run pytest`: 325 passed, 3 pi-e2e gated (unchanged from 14c).
- `cd frontend && npm run check`: 173 passed (unchanged from 14c).
- `uv run ruff check .` / `uv run mypy --strict .`: clean.

Numbers identical to 14c, as expected for a template-text-only change.

## Live attestation protocol (operator action)

To complete the ADR-30 gate, run the following end-to-end against a
real pi v0.74.0 process with `PI_INTEGRATION=1`. The acceptance value
is in seeing the **full declare → edit → audit → resume → re-read
cycle** in a real pi loop; do not substitute a scripted-harness
double.

### Setup

1. Confirm pi v0.74.0 is installed and authenticated (Claude Max
   subscription path; `PI_AGENT_SDK=1` is set by `PiHarness`
   automatically per the de-risking findings).
2. Pick a small target project — any registered project will do; a
   small scratch repo is fine. If the engteam skill isn't installed
   there: `relay install-skill --project <project>` (omit `--project`
   for the default `~/.claude/skills/...` install).
3. Start the server: `relay serve` (binds `127.0.0.1:7800` by default).
   Open the dashboard at `http://127.0.0.1:7800/`.
4. Langfuse is **not required** for this acceptance — Phase 9f's
   trace-tree gate is independent.

### Run

1. From the dashboard's "Hub" or via `relay start`, kick off an
   engteam run on the target project. Prompt example: *"Evaluate the
   project, draft an improvement plan, then pause for my review."*
2. Wait for Phase 1 (evaluation) to write
   `$RELAY_RUN_DIR/evaluation-report.md` and hand off to Phase 2.
3. Wait for Phase 2 (planning) to write
   `$RELAY_RUN_DIR/improvement-plan.md` and emit the new closing
   sentinel:
   ```
   [[engteam:pause-for-input id="P1" question="Approve plan in $RELAY_RUN_DIR/improvement-plan.md and proceed to Phase 3 (development)?" review_path="improvement-plan.md"]]
   ```
4. The run flips `running → paused`. Open the run-detail view.

### Observations to capture

- **Sentinel parsing (14b):** the paused iter's `signal_args` should
  carry `review_path: "improvement-plan.md"`. Verify either by the
  REST replay (`GET /api/runs/<id>` → iters → latest paused iter's
  `signal_args`) or by querying SQLite directly.
- **Dashboard editor (14c):** the run-detail view should render the
  `PauseAnswerForm` in **review mode** — a top pane with the file
  path label, a left textarea populated with the agent's draft, and a
  right markdown-preview pane (markdown-it + shiki for fenced code +
  mermaid if any). The existing question / answer textarea / Resume
  button sit below, unchanged.
- **Save (14a + 14c):** edit a line of the plan, click Save.
  - Expect `200` response with `{path, size, sha256}`.
  - Expect a new `artifact_edited` row to appear in the timeline
    within ~1s via live SSE (no refresh needed) — single-line row
    showing `✎ improvement-plan.md · <sha-before> → <sha-after> ·
    dashboard`.
  - Expect the Resume button to be momentarily disabled during the
    in-flight PUT, then re-enabled.
- **Resume:** type "go" in the answer textarea, click Resume.
  - Expect `pause_resolved` event and run flips `paused → running`.
  - Expect Phase 3 to begin (`phase-start phase="development"` row).
- **Re-read on resume (ADR-20 load-bearing):** the Phase-3 iter's
  prompt body should contain the Phase-2 `next_prompt` verbatim,
  including the "Re-read it in full — the user may have edited it"
  instruction. The agent's first assistant message should read the
  plan file. If the operator made a meaningful edit, the agent's
  subsequent actions should reflect it.

### Negative checks

- **No-edit path regression:** repeat the cycle on a second project;
  type "go" without editing; click Resume. Expect no `artifact_edited`
  events, byte-identical resume behaviour to pre-14d.
- **Sandbox (ADR-25, ADR-40):** outside the dashboard, `curl -X PUT
  http://127.0.0.1:7800/api/runs/<id>/artifacts/../escape.md` should
  return 400 (sandbox rejection).
- **Status coupling (ADR-40 OQ-1):** after resume, repeating the PUT
  against `improvement-plan.md` should return 409 `not_paused`.

### What to record in this journal entry

Once the live run is done, append an "## Attestation" section here
with: the run id, date/time, what was actually seen for each checkpoint
above, any UX rough edges (file them as 14e candidates in a "Notes /
quirks" subsection), and a closing line confirming the 14a → 14d arc is
fully shipped.

## Outcome (pending live attestation)

Pending the operator's live run, **the pause-for-review arc (14a →
14d) is structurally complete:**

- Backend write endpoint + `artifact_edited` event (14a, ADR-40 §B1).
- Sentinel `review_path` attribute (14b, ADR-40 §A1).
- Dashboard inline editor + timeline row (14c).
- Skill template emits the attribute for the primary caller (14d,
  this session).

14e (deferred): diff view (proposal §OQ-5), per-edit annotation in
`compose_resume_prompt` (proposal §OQ-4 / ADR-40 deferred), plural
`review_paths` (§OQ-2), OTel span attribute carrying per-pause
`artifact_edited` count.

## Attestation

_To be filled in by the operator after the live run._
