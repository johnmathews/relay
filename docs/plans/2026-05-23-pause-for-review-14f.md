# Plan — Phase 14f (pause-for-review: plural `review_paths`)

**Status:** ready to execute
**Date:** 2026-05-23
**Source proposal:** `docs/proposals/pause-for-review.md` §"OQ-2"
resolution + §"14e + 14f — 2026-05-23 follow-up".
**Predecessors:** 14a (write endpoint + `artifact_edited` event), 14b
(sentinel `review_path` attribute, single), 14c (dashboard inline
editor for the single-path case), 14d (engteam Phase-2 emits a single
`review_path`).
**Depends on:** 14a–14d on `main` and verified.
**Sibling sub-phase:** 14e (audit polish — diff toggle, timeline
navigation, OTel scalar attr, fanout phase-2 cross-link). Either
order works; if 14e lands first, 14f layers tabs over the existing
single-pane layout.

## Goal

Extend the pause-for-review contract from a single review path per
pause to **N review paths**, while preserving 14b/14c/14d behaviour
byte-for-byte for the common single-path case.

The agent declares plural review paths by repeating the existing
`review_path="..."` attribute on the `pause-for-input` sentinel:

```
[[engteam:pause-for-input id="P1"
                          question="Approve both audits?"
                          review_path="frontend-audit.md"
                          review_path="backend-audit.md"]]
```

The orchestrator's sentinel parser collects all values into
`signal_args.review_paths: list[str]`. The 14a write endpoint's
coupling check generalises from exact-match to set-membership. The
dashboard renders tabs when N > 1 (per-tab dirty state, one Save
button operating on the active tab), and is byte-identical to 14c
when N == 1 or absent.

ADR-41 records the storage-shape change (scalar key →
list key), the repeated-attribute grammar choice, and the membership
coupling.

## Locked decisions

- **Grammar shape: repeated attribute.** Rejected alternatives:
  JSON-array-in-attribute-value (novel for line attrs; escaping
  pain; fanout's marker-bracketed JSON exists precisely to avoid
  this), and CSV (`review_paths="a,b"` — hidden filename-comma
  footgun). Repeated attribute is the cleanest grammar extension:
  `_PAUSE_RE` is line-anchored and attribute-order-agnostic;
  collecting all matches via `re.finditer` is a one-line change.
- **Storage shape:** `signal_args.review_paths: list[str]`
  (always a list; len 1 for the single-path case; len 0 means no
  attribute was present and the key is absent — load-bearing for
  the 14a write endpoint's `no_review_path` 409 branch).
- **Migration / back-compat:**
  - The parser **stops writing** the scalar `signal_args.review_path`
    key. New paused iters land with `signal_args.review_paths`.
  - Readers (write endpoint, dashboard) fall back to the scalar key
    iff `review_paths` is absent. This handles iters paused under
    14a–14d that survive a process restart into the 14f code.
  - 14b's `extract_pause_review_path` (single-value extractor) stays
    as a one-line shim returning the first element or `None`, until
    no caller remains. The new extractor
    `extract_pause_review_paths` returns the list.
- **Coupling check generalisation:** `RelayCore.write_artifact` —
  request path is sandbox-normalised, then required to be a member
  of the normalised set of `signal_args.review_paths` (falling back
  to `[signal_args.review_path]` for migration). Mismatch → 409
  with the same `detail` shape as today's exact-mismatch branch.
- **Dashboard UI:** tab bar appears only when N > 1. N == 1 renders
  the existing single-pane layout (no tab bar, no chrome diff). Per-
  tab dirty state and per-tab Save mutation. One Save in flight at a
  time (across tabs). Resume button disabled while any tab has unsaved
  *or* in-flight changes.
- **Engteam Phase-2 template is NOT modified by 14f.** It continues
  to emit exactly one `review_path="improvement-plan.md"`. Plural is
  opt-in for future skills / variant phases / non-engteam callers.
- **MCP tool surface unchanged.** No new tool; the `relay__pause_
  response` signature is unchanged.
- **ADR-41 lands with 14f.** It records the three contract-level
  decisions above (grammar / storage / coupling). Mirrors ADR-40's
  shape; ~1 page.

## What 14f does NOT do

- Does not modify the engteam Phase-2 sentinel template (still
  one path).
- Does not change the diff toggle, timeline navigation, or OTel attr
  (all 14e). 14f's tab UI integrates with whichever of those have
  landed: per-tab diff toggle, per-tab timeline link is unchanged
  (the row already names a single `payload.path`), per-tab OTel-attr
  is unchanged (the count is still over the paused iter as a whole;
  the attribute is not per-path).
- Does not add per-path coupling to `compose_resume_prompt` (OQ-4
  remains deferred).
- Does not add B3 (event-payload edit content).
- Does not change the 14a `artifact_edited` event payload shape
  (still `{path, size_before, size_after, sha256_before,
  sha256_after, editor}` — one event per save, scoped to a single
  path; multiple paths in one pause produce multiple events, one
  per save).

## File-by-file changes

### `src/relay_v2/harness/signaling/sentinels.py`

- New extractor `extract_pause_review_paths(text: str) -> list[str]`:
  - Mirror `extract_pause_review_path` (the 14b single-extractor):
    line-anchored `_PAUSE_RE.match(line)` to locate the pause line.
  - On match, use `re.finditer(_REVIEW_PATH_RE, line)` (where
    `_REVIEW_PATH_RE` is the existing pattern) to collect **all**
    `review_path=` values on that line.
  - For each value, call the existing `_validate_review_path` (empty
    / NUL / absolute / traversal → `MarkerError` with the
    `_REVIEW_PATH_REPAIR` recipe, naming which value failed).
  - Return the list (`[]` if no `review_path=` attr present).
- `detect_in_text` pause branch:
  - Calls `extract_pause_review_paths`.
  - If `len(paths) > 0`, set `args["review_paths"] = paths`.
  - **Do NOT** also set `args["review_path"]` (the singular key is
    no longer written; readers handle the migration fallback).
- `extract_pause_review_path` (14b) stays as a one-line back-compat
  shim:
  `def extract_pause_review_path(text): paths =
  extract_pause_review_paths(text); return paths[0] if paths else
  None`.
  Or delete it after confirming no caller remains (grep first).

Tests (`tests/harness/test_signaling_sentinels.py` or wherever 14b's
landed):

- 0/1/2/3 repeated attributes parse correctly.
- Per-element validation: `review_path="ok.md" review_path="/abs.md"`
  → `MarkerError` naming `/abs.md`.
- `extract_pause_review_paths` returns the right list for each case.
- `detect_in_text` yields `args["review_paths"]` (list) and **no**
  `args["review_path"]` (singular key absent).
- A line with `review_path="a.md" review_path="b.md"` and the existing
  `id`/`question` attrs still parses cleanly (regex order-agnostic).

### `src/relay_v2/core.py` — `RelayCore.write_artifact`

The coupling check in `_normalise_review_path` (or the inline check
in `write_artifact`) generalises:

```python
# pseudocode — adapt to current shape
review_paths = signal_args.get("review_paths")
if review_paths is None:
    legacy = signal_args.get("review_path")  # 14a–14d migration
    review_paths = [legacy] if legacy is not None else []
if not review_paths:
    raise PauseReviewError(code="no_review_path", ...)
norm_request = _normalise(rel_path)
norm_allowed = {_normalise(p) for p in review_paths}
if norm_request not in norm_allowed:
    raise PauseReviewError(code="path_mismatch", ...)
```

The `PauseReviewError.code` values are unchanged (`no_review_path` /
`path_mismatch` / etc.); HTTP-status mapping is unchanged.

Tests (`tests/api/test_artifacts_write.py`):

- A pause with two review paths accepts PUT to either; PUT to a
  third → 409 `path_mismatch`.
- A pause with the legacy single `signal_args.review_path` key still
  accepts PUT to that path (migration fallback path covered).
- A pause with `review_paths = []` → 409 `no_review_path` (matches
  14a behaviour for the no-attribute case).
- Atomic write, sandbox, oversize, binary tests inherited (no
  change).

### `frontend/src/components/runs/PauseAnswerForm.vue`

- Props change: `pauseReviewPath: string | null` → `pauseReviewPaths:
  string[]` (always an array; empty = no review pane).
- Internal state becomes per-path:
  - `loadedBaselines: Record<string, string>` keyed by path.
  - `textareaContents: Record<string, string>` keyed by path.
  - `dirtyByPath: Record<string, boolean>` derived.
  - `inFlightByPath: Record<string, boolean>` derived from active
    mutations.
- Render shape:
  - `pauseReviewPaths.length === 0` — no review pane (today's
    pre-14c behaviour).
  - `pauseReviewPaths.length === 1` — **single-pane layout** (no
    tab bar, no chrome diff vs 14c). The pane operates on the only
    path. Byte-identical to today.
  - `pauseReviewPaths.length > 1` — tab bar across the top: one
    tab per path, label is the path, `*` suffix when dirty,
    in-flight spinner inline. Click a tab → active tab switches;
    pane shows that tab's textarea + preview (+ 14e diff toggle
    if 14e has landed).
- Save mutation operates on the **active tab's** path + content.
  Saved-badge is per-path (visible only on the tab where Save just
  succeeded).
- Discard local changes is per-active-tab.
- Resume button: disabled iff **any** tab has `dirtyByPath[p] === true`
  AND `inFlightByPath[p] === true` (the in-flight rule); also
  disabled while the Save mutation is in flight on the active tab
  (same as today). Optional: surface "you have unsaved changes on N
  tabs" inline near the Resume button as a soft warning (not
  blocking).
- Lazy artifact-content load: each tab's content is fetched on first
  visit (mount eagerly-fetches the active tab; switching to a fresh
  tab triggers its `useArtifactContentQuery`). Avoid eagerly fetching
  all tabs to keep N-large mount cheap.
- 404 "Create at this path" banner: per-tab (a missing path is a
  per-path state; other tabs can still load and edit independently).
- 415 binary: per-tab; the binary tab shows "not editable inline"
  + download link; non-binary tabs are unaffected.

### `frontend/src/views/RunDetailView.vue`

- `pauseReviewPath` (singular, 14c) → `pauseReviewPaths: string[]`.
- Computation: walk iters newest-first (today's pattern), read
  `signal_args.review_paths` as `string[]` if present; fall back
  to `[signal_args.review_path]` if only the legacy key is set
  (migration window); return `[]` otherwise.
- Pass `pauseReviewPaths` as a prop to `PauseAnswerForm`.

### `frontend/src/api/schema.d.ts`

Regenerated from the running backend's `/openapi.json` after the
backend side lands. No hand-edits.

### `docs/spec.md`

- §5 (sentinel grammar) — the pause grammar paragraph notes
  `review_path` may repeat; cite the same line-anchored constraint
  as the singular attribute.
- §7 (REST) — the PUT description updates to "the requested path
  must be a member of the latest paused iter's
  `signal_args.review_paths`" (or fallback to scalar
  `signal_args.review_path` during migration).
- §9.1 (dashboard pause action) — short paragraph about the tab
  layout when N > 1.

### `skills/engineering-team/pi/references/sentinels.md`

The 14b "Reviewable pauses (`review_path`)" sub-section gains a
short paragraph:

> The `review_path=` attribute may be repeated on the same pause
> line to declare multiple files for review (e.g.
> `review_path="a.md" review_path="b.md"`). The dashboard renders
> a tab per path with independent dirty state and per-tab Save.
> Each path is validated independently; a single invalid path
> raises `MarkerError` naming the offender. Storage:
> `signal_args.review_paths: list[str]` (the singular `review_path`
> key from earlier docs is migration-fallback only — new emit paths
> use the plural key).

### `docs/decisions.md` — ADR-41

Append (decisions.md is append-only per CLAUDE.md). Draft below; the
implementing session copies this into the file at the position after
ADR-40, with status line updated to "accepted" on the merge date.

```markdown
## ADR-41 — Pause-for-review: plural `review_paths` via repeated attribute, `signal_args` shape change to list

**Status:** accepted (2026-05-23). Lands with Phase 14f
(`docs/plans/2026-05-23-pause-for-review-14f.md`). Extends ADR-40
along the previously-deferred OQ-2 axis.

**Context.** ADR-40 §A1 introduced the `review_path` sentinel attribute
on `pause-for-input` as a scalar, with OQ-2 ("Multiple reviewable
paths in one pause") deferred: "scalar in v1 (matches today's use);
plural is additive later (`review_paths` array attribute, or repeat
the attribute)". This ADR records the plural extension.

**Decision 1 — Grammar: repeat the attribute, do not introduce
JSON-in-attribute-value.** A pause line may carry `review_path=`
multiple times on the same line:

```
[[engteam:pause-for-input id="P1"
                          question="Approve both?"
                          review_path="a.md"
                          review_path="b.md"]]
```

The line-anchored `_PAUSE_RE` is unchanged; collection moves from
`re.search` (first match) to `re.finditer` (all matches). Per-value
validation reuses the 14b `_validate_review_path` helper unchanged
(empty / NUL / absolute / traversal → `MarkerError` with the
existing `_REVIEW_PATH_REPAIR` recipe, naming the offending value).

Rejected alternatives:

- **JSON-array attribute value** (`review_paths=["a","b"]`) — novel
  for line attrs; the existing parser uses simple `key="value"` with
  `\\"`-unescape. Introducing JSON parsing inside attribute values
  brings escaping complexity (esc-quote inside esc-quote, multi-line
  values) precisely the kind of pain the marker-bracketed fanout
  payload was designed to avoid (per ADR-35 / `sentinels.md` §"Pairing
  rules"). The repeated-attribute form sidesteps this entirely.
- **CSV** (`review_paths="a.md,b.md"`) — simplest split, but commas
  are valid filename characters on every relay-supported platform.
  The dashboard's only current callers use markdown filenames where
  commas are unusual, but encoding the assumption in the grammar
  is a hidden footgun for non-engteam callers. Rejected.

**Decision 2 — Storage shape: `signal_args.review_paths: list[str]`
replaces the scalar `signal_args.review_path` key.** New paused iters
land with the plural key only. Readers (write endpoint, dashboard)
fall back to the scalar key iff `review_paths` is absent, handling
iters paused under 14a–14d that survive a process restart into the
14f code. The fallback is a migration-window concern only; future
audits / cleanup can drop the fallback once the database contains
no rows with the singular key.

**Decision 3 — Coupling generalises from exact-match to
set-membership.** `RelayCore.write_artifact` normalises the requested
path and checks set-membership against the normalised
`signal_args.review_paths`. Mismatch / not-paused / unknown-run
return the same `PauseReviewError` codes ADR-40 §Decision-3 named;
HTTP status mapping is unchanged. The strict-coupling intent of
ADR-40 §OQ-1 is preserved: writes are allowed only to paths the
agent declared on the paused iter.

**Decision 4 — Engteam Phase-2 template is not modified by 14f.**
The skill continues to emit exactly one `review_path` (the lone
`improvement-plan.md`). Plural is opt-in for future skills, variants,
or non-engteam callers. This keeps 14d's live-acceptance baseline
stable.

**Decision 5 — MCP tool surface stays frozen.** No new tool;
`relay__pause_response` signature is unchanged. Operators using MCP
to drive a paused run cannot edit artifacts via MCP (this matches
ADR-40's choice — agents do not edit their own artifacts mid-pause).

**Consequences.** Sentinel grammar extends along a previously-
anticipated axis; storage shape changes (one breaking-ish detail in
the orchestrator's internal contract, mitigated by the migration
fallback); dashboard gains a tab layout for N > 1, byte-identical
behaviour for N == 1 or absent. ADR-40 §B1 (content on disk + hash-
bearing event) is unchanged; per-edit events still scope to a single
path. ADR-29's OTel mirror is unchanged (the 14e scalar attribute
counts events across paths in the pause window, which is the right
shape — "how much editing happened during this pause").

**Forward-compatibility.** The plural shape leaves room for OQ-4
(`compose_resume_prompt` annotation) to attach per-path entries iff
that question reopens, without further grammar change.

**Related ADRs:** ADR-20 (pause/resume; `signal_args` shape), ADR-25
(sandbox resolver), ADR-29 (OTel mirror), ADR-40 (the pause-for-review
contract this ADR extends).
```

### `CLAUDE.md` — "Current state"

Append a **14f paragraph** at the end of the existing 14e paragraph
(or after the 14d paragraph if 14f lands before 14e). Shape:

> **Phase 14f** (2026-05-23,
> [docs/plans/2026-05-23-pause-for-review-14f.md](docs/plans/2026-05-23-pause-for-review-14f.md),
> ADR-41) extends pause-for-review from a single `review_path` to a
> list via the repeated-attribute grammar
> (`review_path="a.md" review_path="b.md"` on the same `pause-for-
> input` line). Storage shape changes: `signal_args.review_paths:
> list[str]` replaces the scalar `review_path` key; readers fall
> back to the scalar key during the migration window so iters
> paused under 14a–14d survive a process restart cleanly. The 14a
> write endpoint's coupling check generalises from exact-match to
> set-membership; `PauseReviewError` codes and HTTP statuses are
> unchanged. `PauseAnswerForm.vue` renders a tab per path when
> N > 1 (per-tab dirty state, one Save in flight at a time;
> Resume disabled while any tab has unsaved or in-flight changes);
> N == 1 (or absent) is byte-identical to 14c. Engteam Phase-2
> template **not** modified — still emits exactly one
> `review_path`; plural is opt-in for future skills or non-engteam
> callers. ADR-41 records the storage shape change, the grammar
> choice (rejected JSON-in-attribute and CSV), and the membership
> coupling.

## Verification

Pre-merge gate (required):

- `uv run pytest` — green (existing + new sentinel / write-endpoint /
  dashboard tests).
- `uv run ruff check .` — clean.
- `uv run mypy src/relay_v2/` — clean (the `signal_args` reader sites
  in `write_artifact` and `RunDetailView`'s typed equivalent must
  satisfy the migration-fallback shape).
- `cd frontend && npm run check` — clean (eslint --max-warnings 0,
  vue-tsc, vitest).

Manual smoke (scripted-harness sufficient; live engteam acceptance
is 14d's responsibility and not affected by 14f because the engteam
template still emits a single path):

- Drive a scripted-harness pause with **two** `review_path` attrs.
- Open run-detail; verify the tab bar renders with two tabs; switch
  between them; each loads content independently.
- Dirty + Save tab A; dirty tab B but do not save; verify Resume is
  disabled (unsaved on B); save B; verify Resume re-enables; resume.
- PUT to a third path (via curl) → 409 `path_mismatch`. PUT to either
  declared path → 200.
- A second scripted-harness pause with **one** `review_path` —
  verify the dashboard renders the single-pane layout (no tab bar),
  byte-identical to 14c.

## Acceptance criteria

- Sentinel parser collects 0/1/N repeated `review_path` attributes;
  per-element validation works; `signal_args.review_paths` is the
  written key.
- `write_artifact` accepts PUT to any declared path and rejects
  non-declared paths with 409; legacy `review_path` (singular) key
  is read as a one-element list (migration fallback).
- Dashboard renders tabs when N > 1 and is byte-identical when
  N == 1 / N == 0.
- ADR-41 is appended to `docs/decisions.md` with the content above.
- `docs/spec.md` §5 / §7 / §9.1 reflect the as-shipped state.
- `skills/engineering-team/pi/references/sentinels.md` documents the
  plural form.
- `CLAUDE.md` "Current state" gains a 14f paragraph.
- All four gate commands green.
- Engteam Phase-2 template still emits a single `review_path`
  (regression-checked).

## Out of scope for 14f (recap)

- Engteam Phase-2 plural emission — explicitly **not** in 14f. Open
  later if the engteam workflow develops a real two-file plan-review
  pattern.
- Per-edit annotation in `compose_resume_prompt` — OQ-4, parked.
- B3 (event-payload edit content) — rejected by ADR-40 §B1.
- MCP write tool — frozen surface, ADR-40.
- Per-path OTel sub-spans — rejected in 14e; scalar attribute counts
  edits across all paths in the pause window (semantically correct
  per the "how much editing during this pause" question).

## Commit shape

One commit (preferred — the surface area is small enough; reviewers
can decompose into parser / backend / frontend / ADR commits if the
diff is loud):

```
feat(14f): plural review_paths via repeated attribute (ADR-41)

- sentinels.py: extract_pause_review_paths (re.finditer over the
  pause line); detect_in_text writes signal_args.review_paths
  (list); per-element validation reuses 14b's _validate_review_path
- core.py write_artifact: coupling check generalises from exact-
  match to set-membership; reads signal_args.review_paths with
  fallback to scalar signal_args.review_path (migration window)
- PauseAnswerForm.vue: tab layout when len(paths) > 1, byte-
  identical single-pane when len == 1; per-tab dirty state, one
  Save in flight at a time; Resume disabled while any tab has
  unsaved or in-flight changes
- RunDetailView.vue: pauseReviewPaths (string[]) replaces
  pauseReviewPath (string|null) with migration fallback
- spec.md §5/§7/§9.1; references/sentinels.md plural paragraph
- decisions.md: ADR-41 records the storage-shape change, grammar
  choice, and membership coupling
- CLAUDE.md: 14f paragraph

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Notes for the executing session

- **The grammar change is one line in `detect_in_text` + one new
  helper.** Don't refactor the regex; reuse `_REVIEW_PATH_RE` and
  swap `search` for `finditer` on the pause line. 14b's regex
  shape is the reference.
- **The storage-key swap (`review_path` → `review_paths`) is
  load-bearing.** Make sure no caller still writes the singular
  key — grep `signal_args\["review_path"\]` after the change. The
  read-side fallback is one-way (read singular, write plural).
- **The single-path UX must not regress.** Add a vitest case that
  mounts `PauseAnswerForm` with `pauseReviewPaths=["only.md"]` and
  asserts there is **no** tab bar in the rendered DOM. Don't let
  N == 1 silently render a 1-tab bar.
- **Resume-disable logic is the touchy bit.** Run through the truth
  table in tests: clean / dirty / in-flight × per-tab. The rule is
  "Resume disabled iff (any tab is dirty AND in-flight) OR (active
  tab Save is in flight)". The 'unsaved-but-not-saved' state on a
  non-active tab should soft-warn but **not block Resume** —
  otherwise the operator can be stuck on a tab they intentionally
  abandoned.
- **ADR-41 is the only ADR. Don't introduce ADR-42 for the dashboard
  tabs** — tab layout is a UI choice, not a contract decision.
- **OTel attr (14e) is unchanged.** The scalar count is over all
  `artifact_edited` events in the pause window regardless of
  per-path distribution. Don't add per-path counters in 14f.
- **The legacy `extract_pause_review_path` shim is allowed to live**
  for one release cycle. Grep callers (`grep -rn
  "extract_pause_review_path[^s]" src/ tests/`) before deleting;
  prefer to delete in a separate follow-up if callers exist.
- **MCP / fanout / `compose_resume_prompt` are out of scope.**
  Resist any temptation to thread plural into them in this sub-
  phase. If a need surfaces during implementation, file it as a
  14g candidate or a comment on ADR-41 (decisions.md is append-only
  — comments go in a follow-up ADR, not as edits to ADR-41).
