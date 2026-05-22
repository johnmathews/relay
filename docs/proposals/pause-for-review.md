# Proposal — pause-for-review (inline document editing on pause)

**Status:** proposal (not yet ADR'd, not yet implemented)
**Date:** 2026-05-22
**Phase slot:** Phase 14 in `docs/plan.md` §Post-MVP phases (sketch).
**Touches:** `docs/spec.md` §3.2 (event taxonomy), §6.2 (pause/resume),
§7 (REST), §12 (sentinels); `src/relay_v2/api/artifacts.py` (add a
write path); `src/relay_v2/harness/signaling/sentinels.py` (one
optional sentinel attribute); `src/relay_v2/orchestrator/lifecycle.py`
(`compose_resume_prompt`'s body shape, maybe); `frontend/src/
components/runs/PauseAnswerForm.vue` (inline editor when review_path
is present); `skills/engineering-team/pi/` (phase-2 emits the new
attribute; `references/sentinels.md` documents it).
**Does not touch:** the pi harness (no protocol change), the MCP tool
surface (additive at most — same `relay__pause_response` signature),
`harness/` package internals (ADR-04 preserved), `RelayCore`'s
`resume_run` contract (still `{answer}`; the file edit is independent
of the answer).

## Background

The engineering-team skill's Phase 2 (planning) ends with an explicit
human gate. The agent emits a `pause-for-input` sentinel, writes the
proposed plan to `$RELAY_RUN_DIR/improvement-plan.md`, and the
operator is expected to:

1. Open the file in their own editor (the dashboard does not know to
   highlight it).
2. Read + optionally edit it.
3. Switch to the dashboard, type "go" (or paste edits) into the pause
   answer textarea.
4. Submit, the run resumes.

This pattern is excellent in shape — fresh-context-per-iter (ADR-20)
means the resumed agent re-reads `improvement-plan.md` from disk and
operates on the edited content automatically — but the UX has rough
edges that hurt the human-in-the-loop story:

- **Discoverability.** The pause question is a one-liner; the operator
  has to know the file lives at `$RELAY_RUN_DIR/improvement-plan.md`,
  open it in a separate tool, and know the path will be picked up on
  resume. Nothing in the dashboard tells them this.
- **No edit audit.** Edits happen outside relay. The event store
  (ADR-10's single source of truth) records the `pause_requested` and
  the `pause_resolved` answer; the *thing the operator actually did to
  the artifact* is invisible. Replays of the run can't reconstruct
  what state the file was in at any moment.
- **Render gap.** The dashboard already has a markdown-it + shiki +
  mermaid + diff2html pipeline (Phase 4, ADR-26) that renders artifacts
  beautifully via `GET /api/runs/:id/artifacts/*`. The pause workflow
  doesn't surface it — the operator has to navigate to the artifacts
  pane manually, then back to the run-detail pane to answer.
- **Friction at exactly the wrong moment.** This is the highest-stakes
  decision point in the loop (the human is approving a multi-iter plan
  that will run for hours and cost real tokens). Asking them to
  context-switch between three tools (dashboard, editor, terminal/jq
  to verify) raises the cost of *doing* the review and quietly biases
  toward rubber-stamping with "go".

Phase 14 in `docs/plan.md` is sketched as:

> Extend `pause` signal: in addition to a question, the agent can ask
> the human to *review and optionally edit a document* (typically a
> generated plan) before continuing. The dashboard renders the
> document, supports inline editing, and resumes the run with the
> edited version becoming the next-iter prompt.

That sketch is correct in spirit but slightly misleading on one point:
**the edited document does not become "the next-iter prompt".** The
next-iter prompt is the `next_prompt` body the agent emitted between
the `prompt-start`/`prompt-end` markers (ADR-20, unchanged). The
edited document is an *artifact on disk* that the agent re-reads when
its `next_prompt` body says "re-read `$RELAY_RUN_DIR/improvement-plan.md`
in full — the user may have edited it" — which is exactly what the
current Phase-2 prompt template already says. The proposal here is to
make the **editing experience inline** and the **edit visible to the
event store**, not to change the prompt-composition contract.

## State of the world (today)

Relevant invariants and existing affordances:

- **Pause/resume (ADR-20):** `pause` closes the iter and the run with
  `status=paused`; `iters.signal_args = {next_prompt, question, id}`;
  a `pause_requested` event is appended. `resume_run(answer)` reads
  the saved `next_prompt`, composes the resumed iter's body as
  `next_prompt + "\n---\nAnswer to the paused question (...)" +
  answer + "\n"` via `compose_resume_prompt`, flips status to
  `running`, emits `pause_resolved`, restores phase, re-enqueues at
  the next `seq`. Loop bound is `max(max_iters, paused_seq + 1)`
  (ADR-22 — a resumed run gets at least one post-answer iter).
- **Artifacts (ADR-25, spec §3.3, §7):** Per-run artifacts live at
  `<project_root>/.relay/runs/<run_id>/`, sibling of the worktree.
  `GET /api/runs/:id/artifacts` lists; `GET /api/runs/:id/artifacts/*`
  reads text content (415 for binary). Sandboxed via the same
  audited resolver as the file browser (ADR-25). **No write
  endpoint exists today.**
- **Sentinel grammar (`skills/engineering-team/pi/references/
  sentinels.md`):** `[[engteam:pause-for-input id="P<n>"
  question="..."]]` with a marker-bracketed prompt body. Two
  attributes today (`id`, `question`); the regex
  `_PAUSE_RE = r'^\[\[engteam:pause-for-input[ \t]'` is open-ended,
  so adding an attribute is line-grammar-compatible.
  `_first_attr(line, name)` extracts attributes by name with
  `\"`-unescape support; trivially reused.
- **Dashboard pause UI (`PauseAnswerForm.vue`, spec §9):** Currently
  renders the question in a `<pre>` + a plain `<textarea>` answer box
  + a single submit button. Intentionally minimal — the comment in the
  file explicitly notes "a real markdown-capable editor/render
  pipeline is W6 (`lib/render.ts` is a stub by mandate); a plain
  textarea + `<pre>` question is the correct minimal contract for
  this unit."  W6 has since landed (the artifacts pane uses the full
  markdown-it/shiki/mermaid pipeline); the inline editor can lean on
  the same machinery.
- **Skill (`phase-2-planning.md`):** The Phase-2 prompt template
  already points the agent at `$RELAY_RUN_DIR/improvement-plan.md`
  and tells it to "Re-read it in full — the user may have edited it".
  The skill is already designed for this workflow; what's missing is
  the dashboard surface and the audit.
- **Event taxonomy (spec §3.2):** No `artifact_edited` (or similar)
  event kind. Edits are not currently representable in the event
  store. SSE `KNOWN_EVENT_TYPES` in `frontend/src/api/sse.ts`
  enumerates the kinds the client subscribes to (the post-9g bug-fix
  sweep added `harness_session_ended` + `child_runs_resolved` to it
  — same list will need a new entry).
- **Event store as single source of truth (ADR-10).** Every
  observable action is an append-only `events` row. An edit that
  happens "out of band" via the filesystem is invisible to replay,
  SSE, and OTel. This proposal closes that gap.

## Design alternatives (a real decision lives here)

Two axes have viable shapes:

### Axis 1 — How does the orchestrator know a pause is reviewable?

#### A1 — New sentinel attribute on `pause-for-input` (`review_path`)

```
[[engteam:pause-for-input id="P1" question="Approve the plan?"
                          review_path="improvement-plan.md"]]
```

The parser extracts `review_path`, stores it in `signal_args` next to
`next_prompt`/`question`/`id`. The dashboard reads `signal_args.review_path`
on the paused iter and switches to inline-editor mode.

**Pros:** Explicit, opt-in, additive (omitting the attribute is
identical to today). The agent declares the file it wants reviewed,
which is the right place for the decision (the agent knows what file
it wrote).

**Cons:** One attribute of sentinel-grammar surface area. A wrong
path (typo, traversal attempt) needs a clear failure mode — proposal:
the path is **a relative-to-`$RELAY_RUN_DIR` artifact path**;
`..`/absolute/empty rejected with `MarkerError` at parse time, same
shape as the existing marker recipe. No file existence check at parse
time (the file is written by the same iter as the sentinel; the
dashboard's `GET /artifacts/<path>` will 404 with a clear message if
the agent lied).

#### A2 — Dashboard infers from `question` content

The dashboard scans the pause question (or the saved `next_prompt`) for
mentions of `$RELAY_RUN_DIR/*.md` and offers an editor if a path is
detected.

**Pros:** Zero sentinel grammar change.

**Cons:** Fragile (regex on free-form text), implicit (agent has no
way to *opt out*), and centralizes intent in the wrong layer. **Rejected.**

#### A3 — Always render any markdown artifacts in the artifacts dir

The dashboard always shows the artifacts pane next to the pause form,
without any explicit signal.

**Pros:** Even more zero-friction.

**Cons:** Wrong information density when there are many artifacts
(logs, phase files, multi-step plans) — the operator has to find the
one they're being asked about. **Rejected** as the primary mechanism;
**kept as a fallback** (the artifacts pane is always reachable from
the run-detail view today; this proposal doesn't remove that).

**Recommendation: A1.** Explicit declaration by the agent is the
right contract.

### Axis 2 — Where do edits land + how are they audited?

#### B1 — Direct in-place write to the artifact + an `artifact_edited` event

`PUT /api/runs/:id/artifacts/*` overwrites the file at the requested
sandboxed path with the new content. The endpoint appends a new event
kind `artifact_edited` to the run with payload `{path, size_before,
size_after, sha256_before, sha256_after, editor: "dashboard"}`. The
event store records *that* an edit happened + integrity hashes; the
*content* of the edit is **not** stored in the event payload (it lives
on disk, which is where the agent reads it from anyway).

**Pros:** Simple. The agent's "re-read the file" instruction picks
up the edit transparently. Replay can reconstruct *that* an edit
happened at a known timestamp; the on-disk file at replay time may
differ, but that's a property of on-disk artifacts in general (true
for all artifacts in the system today). Hashes give an integrity
signal: a replayer can verify whether the file they see matches the
post-edit state the run actually used.

**Cons:** No content-level audit (you can't see what the edit *was*
from the event store alone). Mitigation: the operator's git history
on the project (if the artifact dir is committed) is the canonical
content-audit channel. For relay-v2 the artifacts dir is `.relay/runs/
<run_id>/` and is typically gitignored — so this is a known gap,
honestly named, not papered over.

#### B2 — Versioned snapshots (`improvement-plan.<seq>.md`)

Every edit creates a new sibling file `improvement-plan.0.md`,
`improvement-plan.1.md`, etc.; the canonical name points at the
latest version (symlink or content copy).

**Pros:** Full content audit on disk.

**Cons:** Filesystem-level versioning that the agent has to know
about (it would have to look at the symlink or the highest-numbered
file, not just `improvement-plan.md`). Significantly more complex.
The skill would need a new convention. **Rejected for v1**; can be
added later as a per-project policy if it ever bites.

#### B3 — Edit-content lives in the event payload

`artifact_edited` payload = `{path, content}` (the full new content).
Replay can reconstruct the file at any moment.

**Pros:** Pure event-store-driven replay (true ADR-10 conformance).

**Cons:** Doubles disk usage for every edit (content lives in both
the artifact file *and* the events row). For multi-MB plans this
gets ugly. Probably the right answer eventually if we ever ship a
multi-user audited build — but premature for the single-user MVP.
**Rejected for v1**; design-compatible with v2.

**Recommendation: B1.** Direct write + a hash-bearing event row. The
agent's existing "re-read" workflow is preserved; the audit is honest
about what it does and doesn't cover; future tightening (B3) is
purely additive.

## Proposal (A1 + B1)

### Sentinel grammar

One optional attribute on `pause-for-input`:

```
[[engteam:pause-for-input id="P1"
                          question="Approve the plan?"
                          review_path="improvement-plan.md"]]
```

`review_path` is **relative to `$RELAY_RUN_DIR`** (= the run's
artifacts directory, `<project_root>/.relay/runs/<run_id>/`). Absolute
paths, paths containing `..`, and empty strings raise `MarkerError`
at parse time with the existing recipe shape. Omitting the attribute
is identical to today (no inline editor surfaces).

`signal_args` on the paused iter gains a `review_path` key (only when
the attribute was present). Pi never sees this; it's an
orchestrator-side grammar (ADR-04, harness isolation preserved).

### REST surface

```
PUT  /api/runs/:id/artifacts/{path}   write text content to a
                                       sandboxed artifact file
                                       body: {content: str}
                                       returns: 200 {path, size, sha256}
                                       sandbox = same audited
                                       resolver as the GET endpoint
                                       (ADR-25). Text only; same
                                       415 rule as GET if the body
                                       is non-text-decodable.
                                       Requires the run's status to
                                       be 'paused' AND `signal_args.
                                       review_path` to match the
                                       requested path (close coupling
                                       — see OQ-1 below).
                                       Appends an `artifact_edited`
                                       event with hashes.
```

The MCP tool surface is **unchanged** (no `relay__edit_artifact` is
proposed for v1 — agents do not edit their own artifacts mid-pause;
the human does). If a future MCP write tool is desired, it would be
additive.

### Event taxonomy

One new kind, added to spec §3.2:

| Kind | When | Payload |
|---|---|---|
| `artifact_edited` | dashboard PUT to a run's artifact | `{path, size_before, size_after, sha256_before, sha256_after, editor}` |

Not a terminal kind. Run status is unchanged by the edit (`paused`
stays `paused`). The event is iter-scoped to the **paused iter**
(`events.iter_id = <paused iter id>`) so replay can group edits
under the pause that motivated them.

The frontend `KNOWN_EVENT_TYPES` list (`frontend/src/api/sse.ts`)
adds `'artifact_edited'`. The events store's `INVALIDATING_KINDS`
adds it too (an edit should refresh the artifacts list / file
viewer cache).

### Resume composition

`compose_resume_prompt` is **unchanged in its primary shape** — the
resumed body is still `next_prompt + answer block`. However, **if any
`artifact_edited` events landed between `pause_requested` and the
resume**, an additional one-line annotation is appended to the
answer block:

```
{next_prompt}

---
Answer to the paused question ("..."):

{answer}

Note: the operator edited 1 artifact during this pause:
  - improvement-plan.md (sha256 a3f2... → 9b1e...)
```

This is **informational only** — the agent's `next_prompt` already
tells it to re-read the file, so the edit is picked up regardless.
The note exists so the agent has *evidence* the file changed (it
might choose to re-validate harder, log a different summary, etc.).
It's a one-line YAML-ish list, same hand-rendered style as
`compose_join_prompt`'s `RELAY_CHILD_RESULTS`.

**This is the only orchestrator-layer change** beyond the new event
kind and the new write endpoint.

### Dashboard (`PauseAnswerForm.vue`)

When `signal_args.review_path` is present on the paused iter, the
form gains a top section *above* the question/answer block:

- A **file path** label showing the relative path.
- A **two-column or stacked editor**: a `<textarea>` containing the
  current file content (fetched once via `GET /artifacts/<path>`,
  loaded on form mount), and a live-rendered preview pane using the
  existing markdown-it/shiki/mermaid pipeline (the same one the
  artifacts pane already uses).
- A **Save** button that fires `PUT /artifacts/<path>` with the
  textarea content. Disabled if the textarea is unchanged from the
  loaded content. On success: shows a small "Edited at <time>" badge
  and the dashboard refetches the file content (so the SHA-after
  badge can update).
- A **Discard local changes** button that reloads the file from the
  server.
- The existing question / answer textarea / "Resume run" button
  stay below, **unchanged**. The Resume button is **independent** of
  the editor's saved state: the operator can save zero, one, or many
  times before pressing Resume; pressing Resume submits the answer
  with whatever the *current* (already-saved) file state is.

UX choices made explicit:

- **Editor is plain `<textarea>`, not Monaco / CodeMirror.** The W6
  rendering pipeline lazily-loads heavy renderers; the editor
  intentionally stays a `<textarea>` to keep the eager bundle small
  (the Phase-4 mandate of ~41 KB gz eager bundle is load-bearing per
  ADR-26). A richer editor is a future enhancement, not v1.
- **Markdown preview is render-only.** Diff-vs-saved is **not** in
  v1 (we have diff2html available, but the comparison surface gets
  complex — last-saved vs. dirty vs. server-current — and the
  operator can always re-render to check; adding diff is a small
  follow-up if it bites).
- **Conflict policy: last-write-wins** for the single-user MVP per
  ADR-12. Two browser tabs editing the same file = the later PUT
  wins; both `artifact_edited` events land; the loser sees a stale
  view until they refresh. This is acceptable for single-user;
  flag for the multi-user phase.

### Skill-side update (engineering-team Phase 2)

`skills/engineering-team/pi/phases/phase-2-planning.md` template
gains the `review_path` attribute on its pause sentinel:

```
[[engteam:pause-for-input id="P1"
                          question="Approve the improvement plan?"
                          review_path="improvement-plan.md"]]
```

`skills/engineering-team/pi/references/sentinels.md` "How to pause"
section gains a sub-section "Reviewable pauses (`review_path`)"
explaining when to add the attribute: when the next-iter prompt
asks the agent to re-read a specific file the operator might have
edited.

These skill changes are independent from the orchestrator change —
the orchestrator/dashboard work in 14a–14c is **complete and
shippable** even if the skill is still on the old template; the
skill update in 14d is what *activates* the new UX for the primary
caller. A non-engineering-team caller could opt into review-mode
without skill changes.

## What stays unchanged (load-bearing invariants)

These cannot break, and this proposal preserves all of them:

- **Fresh context per iter (`spec.md` §6).** The resumed iter is
  still a fresh pi process; the next_prompt body still travels
  verbatim; the edit is observed by the agent re-reading from disk,
  not via pi session resume.
- **`resume_from=None` always.** No change to `RunContext.start_seq`
  or the loop's resume mechanism. ADR-22's "+1 past max_iters on
  resume" guarantee is preserved.
- **Event store as single source of truth (ADR-10).** The new
  `artifact_edited` event is the audit trail; the file content is
  on disk (same as every other artifact today).
- **Harness isolation (ADR-04).** Pi sees nothing new. The
  `review_path` attribute is parsed by the orchestrator's sentinel
  layer, not pi.
- **All writes through `RelayCore` (ADR-07/15).** The new write
  endpoint is a thin adapter over a new `RelayCore.write_artifact(
  run_id, path, content)` method (which validates pause+review_path
  coupling, writes the file, and appends the event).
- **Sandboxing (ADR-25).** The write endpoint reuses the audited
  resolver; `..`/absolute/symlink-escape attempts return 400.
- **MCP surface frozen.** No new MCP tool in v1; the `relay__
  pause_response` signature is unchanged (it still takes only
  `answer`; edits are a separate API).
- **Pause sentinel grammar is backwards-compatible.** Omitting
  `review_path` is byte-identical to today.

## Tradeoffs and risks

- **Concurrent edit races.** Single-user, single-process MVP
  (ADR-12) makes this trivial — last write wins, both `artifact_edited`
  events land in order. Flag for multi-user.
- **Edit content is on disk, not in the event store.** Honest
  about the audit gap (see B1 vs B3 above). For relay-v2's
  single-user use case this is the right tradeoff; B3 is the
  forward-compatible escape hatch.
- **Path-validation coverage.** The write endpoint's sandbox MUST
  match the read endpoint's exactly. The read resolver is shared
  with the file-browser endpoint (ADR-25 — both reuse the same
  audited resolver); a focused unit test that traversal/absolute/
  symlink attacks reject on the write path is essential.
- **PUT-during-resume race.** Operator clicks Save, then Resume
  before the PUT completes. Two reasonable behaviours: (a) the
  Resume button disables while a PUT is in flight; (b) Resume is
  always enabled, and a tail-end PUT after `pause_resolved`
  succeeds anyway (the event lands against the now-running run;
  the file changes; the agent's re-read picks it up). Lean toward
  (a) — disable Resume during in-flight PUT — because it gives
  the operator a clear "your edit was saved before resume"
  guarantee. The orchestrator side allows (b) regardless (the
  endpoint requires `paused` status; once `running`, the PUT
  returns 409 — and the operator sees that inline).

  *Refinement:* allow PUT to succeed in a small window after
  `pause_resolved` (e.g. within 5 seconds) to absorb the round-trip;
  log a `late_edit` flag on the event. **Rejected for v1** —
  needlessly complex; the 409 surface is honest.

- **Editor bundle size.** Plain textarea + the existing markdown
  pipeline → no new heavy dep. ADR-26's eager bundle budget
  remains intact.
- **Replay fidelity.** A replay can show *that* an edit happened
  + integrity hashes but not the content. This is consistent with
  how every other artifact works in the system today; not a
  regression. Document it on the replay page when one exists.

## Open questions to resolve before implementation

**OQ-1 — Coupling of `PUT /artifacts/*` to pause state.**
Lean: the endpoint requires `run.status == 'paused'` AND the path
matches the paused iter's `signal_args.review_path` exactly. This
keeps edits tied to the deliberate review moment. Alternative:
allow writes any time (the operator could pre-fill an artifact for
the agent to find). Reject the alternative for v1 — the
"pause-for-review" contract is what we want; ad-hoc writes are a
different feature.

**OQ-2 — Multiple reviewable paths in one pause.**
Skill workflow today reviews one file. Should `review_path` be a
list? Lean: scalar in v1 (matches today's use). Plural is additive
later (`review_paths` array attribute, or repeat the attribute).

**OQ-3 — What if the path doesn't exist when the dashboard mounts?**
The agent wrote the file before emitting the sentinel; missing means
the agent lied or the file was deleted out-of-band. Lean: the editor
panel shows a clear "file not found" state with a "Create at this
path" button that does a `PUT` with empty content. Operator can fill
it in and save. This handles the agent-lied case without crashing
the UI.

**OQ-4 — Should the resume-prompt annotation list *every* edit, or
just say "N edits"?**
Lean: list each edit's path + hash transition, one line per file, as
sketched above. The agent may want per-file evidence. Cap at first
N (say 10) with a `... (M more)` line if there are many — though for
single-user, single-file pauses this never triggers.

**OQ-5 — Diff view in the editor (v1 or v2)?**
The Phase-4 pipeline already includes `diff2html`. Showing a diff
between server-current and dirty-buffer in the editor is feasible
but adds UX surface (three states: saved-clean / dirty / saved-after-
edit). Lean: **defer to a follow-up**. v1 ships textarea + render-
preview; v2 adds diff if it proves valuable.

**OQ-6 — Should `artifact_edited` events surface in the run timeline
(`TimelinePane.vue`)?**
Lean: yes, as a small inline row (similar to UsageRow's
`harness_session_ended` rendering) with a "view diff" link that
opens the artifact at the recorded hash. v1: the row exists; the
"view diff" link is a stretch goal. The minimum is that the event
is in the timeline so SSE/replay show it.

**OQ-7 — File-type policy.**
Lean: text only (same rule as GET — 415 for binary). The Phase-2
workflow only ever edits markdown; broader text support (`.txt`,
`.json`) is free and trivial. Binary artifacts (PDFs, images) cannot
be edited inline; if the agent declares one as `review_path`, the
dashboard renders a "this artifact is binary; not editable inline"
state alongside a download link.

**OQ-8 — Phase numbering.**
The fanout-join arc absorbed slot 13 ("subagent dispatch") and
landed as 9a–9f to keep work visibly sequential. This proposal
slots into the still-open Phase 14 ("pause-for-review") in
`docs/plan.md` §Post-MVP phases. Sub-phases below are named 14a–14d
to match the cadence of 9a–9f. If the next fanout-style absorption
shifts the numbering again, rename freely — the sub-phase identity
is the proposal + plan documents, not the digit.

## Phasing

Sub-phases (each independently shippable, deterministic-testable,
reversible, mirroring the 9a-9f cadence):

### 14a — backend: write endpoint + `artifact_edited` event (~1.5 days)

`RelayCore.write_artifact(run_id, path, content)`:
- validates `run.status == 'paused'`
- validates `signal_args.review_path == path` (str-equal after
  normalization)
- writes the file under the existing audited resolver
- appends `artifact_edited` event with `{path, size_before,
  size_after, sha256_before, sha256_after, editor}`; iter-scoped to
  the paused iter.

`PUT /api/runs/:id/artifacts/{path}` route: thin adapter over
`write_artifact`; 400 for traversal/absolute, 404 for unknown run,
409 for non-paused status / path mismatch, 415 for non-text body,
200 with `{path, size, sha256}` on success.

Spec updates: §3.2 (event taxonomy row), §7 (REST surface +
endpoint description).

**Acceptance (14a):**
- Scripted-harness pauses with `review_path="plan.md"`.
- `PUT` to `plan.md` with text content succeeds; file on disk
  matches; `artifact_edited` event appears with both hashes.
- `PUT` to a different path → 409.
- `PUT` to `../escape` → 400.
- `PUT` while running (not paused) → 409.
- `PUT` to a binary path → 415.

### 14b — sentinel grammar: `review_path` attribute (~0.5 day)

`harness/signaling/sentinels.py`:
- `extract_pause_review_path(text) -> str | None` (mirrors
  `extract_pause_id`).
- `detect_in_text` includes `review_path` in pause `signal_args`
  when present.
- Path validation: empty / absolute / `..`-bearing → `MarkerError`
  with a focused repair recipe.

`skills/engineering-team/pi/references/sentinels.md` documents the
new attribute (one new sub-section + an updated example).

**Acceptance (14b):**
- Sentinels test suite gains 4 cases: present-and-valid, absent,
  invalid (absolute), invalid (traversal). All four green.
- A paused iter's `signal_args` carries `review_path` end-to-end
  through the loop into the DB (one orchestrator integration test).

### 14c — dashboard: inline editor (~2 days)

`PauseAnswerForm.vue` (or a sibling `PauseReviewPane.vue` if the
file gets unwieldy):
- When `signal_args.review_path` is present, fetch
  `GET /artifacts/<review_path>` on mount.
- Render the editor section (textarea + preview pane) above the
  existing question/answer block.
- Save button wires `PUT /artifacts/<review_path>` with the
  current textarea content; success shows "Saved at HH:MM:SS";
  disabled while clean.
- Discard local changes button reloads from server.
- Resume button disables while a PUT is in flight; otherwise
  unchanged.
- 404 → "Create at this path" state.
- 415 → "this artifact is binary; not editable inline" + download link.

`frontend/src/api/sse.ts::KNOWN_EVENT_TYPES` includes
`'artifact_edited'`. `frontend/src/stores/events.ts::INVALIDATING_KINDS`
includes it.

`TimelinePane.vue` renders `artifact_edited` events as a small
inline row with `path · sha256-before → sha256-after`.

**Acceptance (14c):**
- Vitest suite: paused-with-review_path renders editor; paused-
  without-review_path renders the existing minimal form; save
  fires the PUT; resume blocks while PUT in flight.
- Manual smoke (journal-attested per ADR-30): edit a real Phase-2
  plan inline, save, resume; the run picks up the edit on the next
  iter.

### 14d — skill update + live acceptance (~0.5 day + acceptance)

`skills/engineering-team/pi/phases/phase-2-planning.md` template
emits `review_path="improvement-plan.md"`.

The accompanying journal entry (live `PI_INTEGRATION=1` engteam run)
attests the full loop: agent emits the sentinel; dashboard renders
the editor; operator edits, saves, resumes; agent re-reads and
proceeds. This is the journal-attested acceptance equivalent to 9f's
Langfuse-UI gate.

**Acceptance (14d):**
- Live engteam run shows the new editor end-to-end.
- Journal entry recording the same.

### 14e (deferred / optional)

If 14a–14d ship cleanly and the workflow proves valuable:

- Diff view (OQ-5)
- `review_paths` plural (OQ-2)
- "view diff" links on `artifact_edited` timeline rows (OQ-6 stretch)
- OTel span attribute carrying `artifact_edited` count per pause

None of these are blockers; all are purely additive.

## Effort estimate

Order-of-magnitude:

- 14a (backend write + event): 1.5 days.
- 14b (sentinel grammar): 0.5 day.
- 14c (dashboard editor): 2 days.
- 14d (skill update + journal-attested acceptance): 0.5 day +
  acceptance time.

~4.5 working days total, plus the journal-attested live demo
(gated like the other `PI_INTEGRATION=1` acceptances per ADR-24/30).

Each sub-phase is independently mergeable; 14a alone gives a
working write-endpoint that an ad-hoc operator could already use
via curl; 14a+14b makes the contract complete; 14c lights up the UX;
14d activates it for the primary caller.

## Rejected alternatives

### Make the edited file content the literal next-iter prompt

The original sketch in `docs/plan.md` reads "resumes the run with
the edited version becoming the next-iter prompt". **Rejected** as
the implementation contract: the next-iter prompt is the `next_prompt`
body in the sentinel — that's what the agent designed for itself
(ADR-20). The edited file is *evidence* the agent reads when its
prompt tells it to. Conflating the two would force the agent to
restructure its workflow ("treat the user's edited plan as
literally my next prompt") and break the existing skill design where
the agent first re-reads + then *acts on* the plan.

### A first-class `pause-for-review` sentinel verb (parallel to `pause-for-input`)

A separate verb makes the intent explicit at the grammar level
(`[[engteam:pause-for-review file="..."]]`). **Rejected** for v1:
the existing `pause-for-input` semantics (close iter, store
next_prompt + question, await operator) is exactly what we want;
adding a verb duplicates the wiring (loop, signaling, status
projection, dashboard PauseAnswerForm). A single optional attribute
is strictly less surface area.

### Versioned artifact files (B2)

Snapshot each edit as `<name>.<seq>.md` with a canonical symlink.
**Rejected** for v1 (see B2 cons above). Tractable as a per-project
policy follow-up.

### Edit content embedded in event payloads (B3)

Full content in the event store for pure event-driven replay.
**Rejected** for v1 (disk doubling). Forward-compatible — v2
audited build can opt in.

### Server-side conflict detection (ETags / If-Match)

Reject PUT if the file's hash on disk doesn't match the operator's
loaded baseline. **Rejected for v1** (single-user/single-process —
ADR-12 — makes this unnecessary). Designed-in headroom: the response
already returns the new `sha256`, so adding `If-Match: <sha256>`
later is purely additive.

## Related

- ADR-04 — harness isolation (pi sees nothing new).
- ADR-07 / ADR-15 — all writes through `RelayCore`.
- ADR-10 — event store as single source of truth.
- ADR-12 — single-user, single-process MVP (drives last-write-wins).
- ADR-20 — pause/resume persistence (the foundation; `signal_args`
  shape extended additively).
- ADR-22 — resume guarantees forward progress past `max_iters`
  (unchanged; still applies to a resumed-after-edit run).
- ADR-25 — sandboxed artifact-browser resolver (reused for writes).
- ADR-26 — frontend toolchain mandates (preserved; no new heavy dep).
- ADR-30 — automated CI gate + manual journal-attested acceptances
  (14d follows this pattern).
- ADR-39 — `harness_session_ended` persistence (the most recent
  example of an additive event taxonomy change — `artifact_edited`
  mirrors that shape).
