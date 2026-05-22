# Plan — Phase 14d (pause-for-review: skill template + live acceptance)

**Status:** ready to execute
**Date:** 2026-05-22
**Source proposal:** `docs/proposals/pause-for-review.md` (sub-phase 14d)
**Predecessors:** 14a (write endpoint + event — shipped commit
`dfefb87`), 14b (sentinel `review_path` attribute), 14c (dashboard
inline editor).
**Depends on:** all of 14a/14b/14c being on `main` and verified.

## Goal

Activate the pause-for-review UX for the **primary caller** — the
engineering-team skill's Phase 2 — and journal-attest a live
end-to-end run.

After 14d, a stock `relay install-skill` + a real `PI_INTEGRATION=1`
engteam run shows the full loop:

1. Phase 1 (evaluation) → Phase 2 (planning) — agent writes
   `$RELAY_RUN_DIR/improvement-plan.md` and emits the new
   `[[engteam:pause-for-input id="P1" question="Approve plan?"
                              review_path="improvement-plan.md"]]`
2. Dashboard's run-detail view renders the inline editor for the
   `improvement-plan.md`, populated with the agent's draft.
3. Operator edits, clicks Save — `artifact_edited` event lands in
   the run's event store + timeline; the file on disk reflects the
   edit.
4. Operator types "go" (or whatever) and clicks Resume —
   `pause_resolved` lands; the parent run flips `paused → running`.
5. The resumed iter (Phase 3 / development) re-reads the edited
   `improvement-plan.md` via the existing skill instruction
   ("Re-read it in full — the user may have edited it") and proceeds.

14d is the *journal-attested* acceptance equivalent of 9f's live
Langfuse-UI gate — automated CI passed, but the human-loop UX
gets eyes on a real engteam cycle, per ADR-30.

## Locked decisions

- **One skill template change, one line.** Only
  `skills/engineering-team/pi/phases/phase-2-planning.md` is touched.
  The closing-sentinel template gains `review_path="improvement-
  plan.md"`. Everything else in the skill is byte-identical.
- **Reference-docs already documented in 14b.** No change to
  `skills/engineering-team/pi/references/sentinels.md` in 14d (14b
  added the "Reviewable pauses" sub-section + the verbs-list
  annotation).
- **Live acceptance is journal-attested per ADR-30.** No
  `tests/orchestrator/test_pi_e2e.py` change — that gated test
  exercises a vanilla pi pass; this is a manual narrative
  attestation (mirrors 9f's Langfuse-UI gate, 5/6's bundled-SDK live
  test, etc.).
- **No regression on the no-edit path.** An operator who edits
  nothing and just types "go" + Resume sees byte-identical behaviour
  to today: the saved `next_prompt` already says "Re-read it in
  full", so the resumed iter reads the unmodified file.

## What 14d does NOT do

- Does not change parsing (14b), backend (14a), or frontend (14c).
- Does not add an automated pi-e2e test for the inline-editor path
  (would require staging dashboard UI interaction into the test, which
  is out of scope for `PI_INTEGRATION=1` — those tests drive pi
  directly).
- Does not change the wrap-up phase template (which doesn't pause).
- Does not change any other skill phase doc (Phase 1, 3, 4 keep
  emitting plain `pause-for-input` if they pause at all — only
  Phase 2 has a reviewable artifact).
- Does not add a journal entry for anything other than this live
  acceptance.

## File-by-file changes

### Skill template — `skills/engineering-team/pi/phases/phase-2-planning.md`

**One template change**, at line 230 (the closing-sentinel example
inside the "Step 4: Pause gate" recipe). Replace:

```
[[engteam:pause-for-input id="P1" question="Approve plan in $RELAY_RUN_DIR/improvement-plan.md and proceed to Phase 3 (development)?"]]
```

with:

```
[[engteam:pause-for-input id="P1" question="Approve plan in $RELAY_RUN_DIR/improvement-plan.md and proceed to Phase 3 (development)?" review_path="improvement-plan.md"]]
```

…and add **one short paragraph** under the existing "Notes:" block
(line 233-242 region):

> - The `review_path="improvement-plan.md"` attribute (14b grammar)
>   tells relay's dashboard which file the operator should review
>   inline. The dashboard renders an editor for that file alongside
>   the answer textarea; the operator may edit and save (each save
>   lands an `artifact_edited` event in the run's audit log, ADR-40)
>   before resuming. Omitting the attribute keeps the pre-14b
>   behaviour (no inline editor). Path is **relative to
>   `$RELAY_RUN_DIR`**; see `../references/sentinels.md` §
>   "Reviewable pauses" for the grammar.

(Quick verification step in the implementing session: re-emit the
template through pi's marker validator if a test fixture exists; the
sentence shape with the new attribute is line-grammar-compatible —
`_PAUSE_RE` matches `^\[\[engteam:pause-for-input[ \t]` and is
agnostic to attr ordering.)

The Step-5 handoff template at line 264 is **unchanged** — handoff
sentinels do not take `review_path` (14b restricts the attribute to
`pause-for-input` only; this is the intended design — handoffs are
unattended).

### Acceptance journal — new file `journal/<yymmdd>-pause-for-review-live.md`

Filename pattern from `CLAUDE.md`: `yymmdd-descriptive-name.md`. Use
the actual landing date. Content shape (mirrors
`journal/260523-9f-bug-fixes.md` and earlier 9f acceptance journals):

```markdown
# Pause-for-review live acceptance (Phase 14d)

**Date:** <YYYY-MM-DD>
**Phase:** 14d — pause-for-review skill activation + live engteam acceptance
**ADRs:** ADR-40 (pause-for-review contract).

## Setup

- `relay install-skill` (bundled engineering-team variant).
- A small target project (`scratch/` derivative or any small repo).
- `PI_INTEGRATION=1` enabled; pi v0.74.0 pinned.
- `relay serve` running locally on 127.0.0.1:7800; dashboard at /;
  Langfuse not required for this acceptance.

## Run

1. `relay start <project> "Evaluate, plan, then pause for review."`
2. Phase 1 runs — evaluation report lands at
   `$RELAY_RUN_DIR/evaluation-report.md`. Iter 1 handoff → iter 2.
3. Phase 2 runs — improvement plan lands at
   `$RELAY_RUN_DIR/improvement-plan.md`. Iter 2 emits
   `pause-for-input id="P1" question="..." review_path="improvement-plan.md"`
   and closes the run with `status=paused`.

## Observations

- **Sentinel parsing (14b):** the paused iter's `signal_args`
  carries `review_path: "improvement-plan.md"` (verified via the
  DB or the run-detail JSON).
- **Dashboard editor (14c):** opened the dashboard at the run-detail
  view; the `PauseAnswerForm` rendered the review pane with the
  agent's draft `improvement-plan.md` content; the markdown preview
  on the right rendered the YAML frontmatter + unit list correctly.
- **Save (14a + 14c):** edited one line of the plan, clicked Save.
  - `200` response, `sha256` returned.
  - `artifact_edited` event appeared in the timeline within ~1s
    (live SSE — no refresh needed); single-line row showing the path
    + the sha-before → sha-after transition + `editor=dashboard`.
  - The Resume button was momentarily disabled during the in-flight
    PUT, then re-enabled.
- **Resume:** typed "go" in the answer textarea, clicked Resume;
  `pause_resolved` event landed; run flipped `paused → running`;
  Phase 3 started.
- **Re-read on resume:** the Phase-3 iter's prompt body contained the
  saved `next_prompt` from Phase 2 ("Re-read it in full — the user may
  have edited it"); the agent's first action was to read
  `$RELAY_RUN_DIR/improvement-plan.md`, and its assistant text quoted
  the edited line, confirming the operator's edit was carried forward
  via the on-disk re-read (ADR-20 flow preserved).

## Verification of invariants

- **Event store as single source of truth (ADR-10):** every
  observable action (pause_requested, artifact_edited × N,
  pause_resolved, iter_started for the resumed iter) landed as
  events in order. SSE live tail showed the same sequence as a
  post-hoc REST replay.
- **Fresh context per iter (ADR-20):** the resumed iter spawned a
  fresh pi session (`pi_session_id` distinct from the paused iter's);
  `resume_from` was `None`. The edit travelled via the on-disk
  re-read, not via session resume.
- **Harness isolation (ADR-04):** pi saw nothing new — no protocol
  change, no new tool, no new env var. The `review_path` attribute
  was parsed entirely by the orchestrator's signaling layer.
- **Sandboxing (ADR-25, ADR-40):** confirmed that PUTting to
  `../escape.md` (manual curl test, outside the dashboard) returned
  400; PUTting to the canonical `improvement-plan.md` succeeded;
  PUTting while the run was running (after resume) returned 409
  `not_paused`.

## Negative checks

- **No regression on no-edit path:** repeated the same engteam cycle
  on a second project; opened the dashboard, typed "go" without
  editing, clicked Resume. The run continued correctly. No
  `artifact_edited` events appeared (no PUTs were made).
- **Pre-14b skill compatibility:** confirmed by running an earlier
  engteam variant (or skipping the 14d template change locally) that
  pauses without the `review_path` attribute. The dashboard rendered
  the minimal pre-14c form; Save controls were absent; Resume
  worked as before.

## Notes / quirks

<list any UX rough edges discovered, dashboard glitches, etc. — to
be filed as follow-ups in a future 14e or as separate bug-fix
commits.>

## Outcome

Pause-for-review arc (14a→14d) verified live. The
human-in-the-loop story now: declare → edit → audit → resume → re-
read, all visible in the dashboard and the event log.
```

### Spec — `docs/spec.md`

**§12 (Engineering-team skill port)** — if the section enumerates
phase-2's closing-sentinel form, update the example to include
`review_path="improvement-plan.md"`. If the section is descriptive
(no literal template), no change.

(Quick verification step:
`grep -n 'improvement-plan' docs/spec.md` to see whether the literal
template is reproduced anywhere in the spec.)

### CLAUDE.md — "Current state" walkthrough

Add a **14d paragraph** at the end of the existing 14a paragraph
sequence. Shape:

> **Phase 14d** (`<YYYY-MM-DD>`,
> [skills/engineering-team/pi/phases/phase-2-planning.md], the
> journal at [journal/<file>](...)) activates pause-for-review for
> the primary caller. One template change — the Phase-2 closing
> sentinel now emits `review_path="improvement-plan.md"` alongside
> the existing `id`/`question` attrs (14b grammar). One new
> paragraph in the Step-4 "Notes:" block names the new attribute,
> points at `references/sentinels.md` §"Reviewable pauses", and
> notes that omitting it keeps pre-14b behaviour. The wrap-up
> phase, the handoff template, and every other skill phase doc are
> byte-identical. Live `PI_INTEGRATION=1` engteam acceptance is
> journal-attested per ADR-30 — the full
> declare → edit → audit → resume → re-read cycle verified end-to-
> end (sentinel parse / dashboard editor / event store /
> `compose_resume_prompt`'s ADR-20 flow). No backend / frontend /
> ADR changes. The pause-for-review arc (14a → 14d) is now fully
> shipped.

### MCP / REST / backend — no change

14d touches the skill template + a journal entry + CLAUDE.md +
optionally one spec line. No code change.

### Frontend — no change

No `frontend/` file is touched in 14d.

## ADR — none

14d is purely an *activation* of decisions already in ADR-40 (A1 —
opt-in via sentinel attribute). No new ADR.

## Verification

Pre-merge automated gate (sanity, not blocking):

- `uv run pytest` — should pass (no code touched, but run it once to
  confirm the skill-structure tests still pass after the template
  edit).
- `uv run ruff check .` — clean.
- `uv run mypy src/relay_v2/` — clean.
- `npm run check` — clean (no frontend touched).

Manual gate (the load-bearing one):

- A real `PI_INTEGRATION=1` engteam run, end-to-end, recorded in
  the new journal entry. The journal entry is the acceptance
  artefact; without it the phase is not considered shipped.

Re-run with a second project to confirm reproducibility (mirrors
the 9f acceptance pattern: one canonical run + one regression run).

## Acceptance criteria

- `skills/engineering-team/pi/phases/phase-2-planning.md` template
  emits `review_path="improvement-plan.md"` on the pause sentinel.
- A new `journal/<yymmdd>-pause-for-review-live.md` entry
  documenting a live `PI_INTEGRATION=1` engteam run end-to-end,
  with positive + negative observations and invariant checks.
- `CLAUDE.md` "Current state" gains a 14d paragraph.
- Optional: `docs/spec.md` §12 example updated if it reproduces the
  literal sentinel.
- No regression in `uv run pytest` / `ruff` / `mypy --strict` /
  `npm run check`.
- The pre-14b skill-emit path still works (regression-checked in
  the journal's "Negative checks" section).

## Out of scope for 14d (recap)

- Sentinel grammar / parser → **14b** (predecessor).
- Dashboard editor / timeline row → **14c** (predecessor).
- Backend write endpoint / event → **14a** (predecessor, shipped).
- Diff view / per-edit annotation in the resume body → **14e**
  (deferred / optional follow-up).
- Plural `review_paths` → **14e** (proposal §OQ-2 deferred).
- Other skill phase templates (1/3/4) → not touched; only Phase 2
  has a reviewable artifact in v1.

## Commit shape

One commit:

```
feat(skill): engineering-team Phase 2 emits review_path (14d)

- phase-2-planning.md template: add review_path="improvement-plan.md"
  to the pause closing sentinel; Notes block names the new attribute
  and points at references/sentinels.md §"Reviewable pauses"
- journal/<yymmdd>-pause-for-review-live.md: live PI_INTEGRATION=1
  engteam acceptance (declare → edit → audit → resume → re-read)
- CLAUDE.md: 14d paragraph closes the pause-for-review arc

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

(The journal entry can ride the same commit as the template change
— matches the 9f acceptance commit which folded the live demo notes
+ the journal entry into one. If the template change lands first
followed by the journal entry separately, that's also fine; both
shipped commits leave `main` in a working state.)

## Notes for the executing session

- **The skill template change is the *only* code-adjacent edit.**
  Don't touch parsing, dashboard, or backend in 14d. If a bug
  surfaces during the live acceptance, file it as a fix commit (or
  a 14e ADR + plan if it's a contract change), not as a 14d scope
  extension.
- **`PI_INTEGRATION=1` requires the Claude Max subscription and
  pi v0.74.0.** If pi is not authenticated, the run will fail at
  spawn (`PI_AGENT_SDK=1` is set by `PiHarness` per the de-risking
  findings); fix the auth and re-run. Don't fall back to a
  scripted-harness double — the acceptance value is in the real
  pi loop.
- **The journal entry is the deliverable.** Treat it like the
  9f Langfuse-UI journal — narrative, dated, names what was
  verified, calls out any rough edges as future-14e follow-ups.
  Without the journal, 14d isn't shipped.
- **The "Re-read it in full" instruction in the existing
  `next_prompt` body is load-bearing.** Don't remove it from the
  Phase-2 template — that's the mechanism by which the resumed
  agent picks up the operator's edits. ADR-40 §B1 *deliberately*
  does not change `compose_resume_prompt` (deferred per OQ-4);
  the skill's prompt body is what carries the re-read instruction.
- **If the live run surfaces a UX rough edge** (e.g. "the editor
  should support tab-key indent", "the timeline row needs a
  click-through to artifacts pane", "the 'Edited at' badge should
  show the SHA prefix instead of HH:MM:SS"), file it as a 14e
  candidate in the journal's "Notes / quirks" section. Don't fix
  it in 14d.
- **No new ADR unless the live run reveals an invariant violation.**
  An ADR-41 would be appropriate only if a decision needs revisiting
  (e.g. "the resume annotation per OQ-4 is genuinely needed —
  open it now"). Default: no new ADR.
