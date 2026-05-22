# Plan — Phase 14b (pause-for-review: `review_path` sentinel attribute)

**Status:** ready to execute
**Date:** 2026-05-22
**Source proposal:** `docs/proposals/pause-for-review.md` (sub-phase 14b)
**Predecessor:** 14a (write endpoint + `artifact_edited` event — shipped
2026-05-22, commit `dfefb87`).
**Successor of:** 14c (dashboard inline editor), 14d (skill template +
live acceptance).
**Depends on:** 14a (the endpoint exists; this PR populates the
`signal_args["review_path"]` it has been waiting for).

## Goal

Teach the **sentinel parser** to recognise an optional `review_path`
attribute on `[[engteam:pause-for-input ...]]` and thread it into the
paused iter's `signal_args`. After 14b, a Phase-2 skill template that
emits

```
[[engteam:pause-for-input id="P1" question="Approve?"
                          review_path="improvement-plan.md"]]
```

results in `iters.signal_args = {"next_prompt": "...", "question": "...",
"id": "P1", "review_path": "improvement-plan.md"}` for the paused iter
— and 14a's `PUT /api/runs/:id/artifacts/{path}` endpoint flips from
the documented 409 interim state to a working write path.

This is intentionally the smallest possible sub-phase: pure
sentinel-parser change + harness/orchestrator tests + skill-side
documentation. No new event kinds, no REST/MCP changes, no frontend
work, no `compose_resume_prompt` change.

## Locked decisions (from the proposal + 14a alignment)

- **OQ-2 — scalar `review_path`, not a list.** v1 reviews exactly one
  file per pause. Plural is additive later (`review_paths` array, or
  repeated attribute); not v1.
- **OQ-4 — no resume-prompt annotation.** `compose_resume_prompt` is
  unchanged. The agent's `next_prompt` body already tells it to
  re-read the file; the edit is observed via the re-read, not via a
  trailer block. Adding a per-edit summary to the resume body is
  parked as a follow-up (14e) once the editing workflow has been used
  in practice and we know what the agent actually wants to see.
- **Path validation at parse time.** Empty string, absolute (`/...`),
  any `..` path component, or an embedded NUL byte → `MarkerError`
  with a focused repair recipe. Matches the existing
  `extract_fanout_payload` / marker-error recipe shape (sentinels.py
  lines 265-274). Mirror of the runtime sandbox check in
  `resolve_within_sandbox` so the failure mode is **early at parse
  time**, not after the iter has been persisted as `paused`.
- **`review_path` is relative to `$RELAY_RUN_DIR`.** Documented in
  the skill reference (`references/sentinels.md`); the parser does
  not resolve it against any path — it just validates the syntactic
  shape. The 14a sandbox resolver (ADR-25, ADR-40) is the runtime
  enforcement.
- **Backwards-compatibility: omitting the attribute is byte-identical
  to today.** The `signal_args` dict gains the `"review_path"` key
  **only when the attribute is present**. Skills not updated to 14b's
  grammar keep working unchanged. (This is observable downstream:
  14a's `write_artifact` raises `PauseReviewError("no_review_path")`
  for any paused iter whose `signal_args` lacks the key, exactly as
  the interim 14a behaviour predicts.)

## What 14b does NOT do

- Does not change the Phase-2 skill template (that's 14d).
- Does not change `compose_resume_prompt` (deferred per OQ-4).
- Does not surface anything in `PauseAnswerForm.vue` or the timeline
  (that's 14c).
- Does not add new event kinds (`artifact_edited` already exists,
  added in 14a).
- Does not change the harness layer (`harness/pi.py`, `harness/
  protocol.py`). Sentinel parsing is the orchestrator's signaling
  layer (ADR-04 preserved — pi sees nothing new).
- Does not change `RelayCore.write_artifact` (no contract change —
  14a's "no_review_path" → 409 branch becomes unreachable in
  practice for skills that adopt the attribute, but the branch
  itself stays and is still exercised by tests that synthesise the
  pre-14b world).

## File-by-file changes

### Spec — `docs/spec.md`

**§5 (Signaling) — add a one-paragraph note** on the new optional
attribute. Place after the existing pause-grammar description:

> `pause-for-input` accepts an **optional** `review_path="<relative-path>"`
> attribute (added 14b, ADR-40). When present, the orchestrator
> stores it as `signal_args["review_path"]` on the paused iter; the
> dashboard's `PauseAnswerForm` (14c) reads it to switch to inline-
> editor mode. The path is **relative to `$RELAY_RUN_DIR`**;
> absolute paths, `..` components, empty strings, or NUL bytes are
> rejected at parse time with `MarkerError`. Omitting the attribute
> is byte-identical to the pre-14b grammar — skills emitting plain
> `pause-for-input` continue to work unchanged.

**§12 (Engineering-team skill port) — one bullet** under the existing
sentinel grammar list (only if §12 explicitly enumerates the
pause-sentinel attributes; otherwise no change).

(Quick verification step in the implementing session:
`grep -n 'pause-for-input' docs/spec.md` to confirm where the
attributes are listed.)

### Sentinel parser — `src/relay_v2/harness/signaling/sentinels.py`

**Add `_REVIEW_PATH_RE`.** Mirror of `_ID_RE` / `_Q_RE` (defined
earlier in the file). A simple `review_path="..."` extractor with the
same `\\"`-unescape allowance as the other pause attrs.

**Add `extract_pause_review_path(text: str) -> str | None`.** Returns
`None` when the attribute is absent (so `detect_in_text` can
conditionally omit the key — see decision above). When present,
validates the value and either returns it verbatim or raises
`MarkerError`.

```python
_REVIEW_PATH_RE = re.compile(r'review_path="((?:[^"\\]|\\.)*)"')


_REVIEW_PATH_REPAIR = (
    "\nThe optional review_path attribute on [[engteam:pause-for-input]]"
    " is a path RELATIVE to $RELAY_RUN_DIR (the run's artifacts dir,\n"
    "<project_root>/.relay/runs/<run_id>/). It must not be empty,\n"
    "absolute, contain '..', or carry a NUL byte. Examples:\n\n"
    '    review_path="improvement-plan.md"\n'
    '    review_path="discussions/notes.md"\n\n'
    "Omit the attribute entirely if the pause does not require an\n"
    "editable artifact (the resumed run still re-reads any file your\n"
    "next_prompt body names).\n\n"
    "See: skills/engineering-team/pi/references/sentinels.md\n"
)


def extract_pause_review_path(text: str) -> str | None:
    """First pause sentinel's ``review_path`` attribute, or ``None``
    when absent. Raises :class:`MarkerError` on syntactically invalid
    values (empty / absolute / ``..``-bearing / NUL)."""
    for line in text.split("\n"):
        if _PAUSE_RE.match(line):
            m = _REVIEW_PATH_RE.search(line)
            if m is None:
                return None
            value = m.group(1).replace('\\"', '"')
            _validate_review_path(value)
            return value
    return None


def _validate_review_path(value: str) -> None:
    if value == "":
        raise MarkerError(
            "extract_pause_review_path: review_path is empty",
            _REVIEW_PATH_REPAIR,
        )
    if "\x00" in value:
        raise MarkerError(
            "extract_pause_review_path: review_path contains NUL byte",
            _REVIEW_PATH_REPAIR,
        )
    if value.startswith("/") or PurePosixPath(value).is_absolute():
        raise MarkerError(
            f"extract_pause_review_path: review_path {value!r} is "
            "absolute (must be relative to $RELAY_RUN_DIR)",
            _REVIEW_PATH_REPAIR,
        )
    parts = PurePosixPath(value).parts
    if any(part == ".." for part in parts):
        raise MarkerError(
            f"extract_pause_review_path: review_path {value!r} contains "
            "'..' (path traversal not allowed)",
            _REVIEW_PATH_REPAIR,
        )
```

Add `PurePosixPath` to the existing `pathlib` import line near the
top of the module.

Update `__all__` to export `extract_pause_review_path` alongside the
existing pause extractors.

**Wire it into `detect_in_text`.** In the `if counts["pause"]:` branch
(lines 345-353), conditionally add `review_path` to the `args` dict:

```python
if counts["pause"]:
    args: dict[str, Any] = {
        "next_prompt": extract_pause_prompt(text),
        "question": extract_pause_question(text),
        "id": extract_pause_id(text),
    }
    review_path = extract_pause_review_path(text)
    if review_path is not None:
        args["review_path"] = review_path
    return SignalEmitted(kind="pause", args=args)
```

The `extract_pause_review_path` call propagates `MarkerError` out of
`detect_in_text` exactly like `extract_fanout_payload` does today —
the loop's `_drive_iter` catch clause finalises the iter with
`exit_reason="agent_end_no_signal"` and a marker headline (existing
behaviour, no change needed in `loop.py`).

### Tests — `tests/harness/test_signaling_sentinels.py`

Add a fixture block + a test class (or new top-level tests; match the
existing module's style):

1. **`test_extract_pause_review_path_absent`** — a pause sentinel with
   no `review_path` attribute: `extract_pause_review_path(text) is
   None`.
2. **`test_extract_pause_review_path_present`** — `review_path="plan.md"`
   → returns `"plan.md"`.
3. **`test_extract_pause_review_path_subdir`** — `review_path=
   "discussions/notes.md"` → returns the value verbatim (parser does
   not normalise).
4. **`test_extract_pause_review_path_unescapes_quotes`** — `review_path=
   "a\"b.md"` → returns `"a\"b.md"` (mirrors `extract_pause_question`'s
   unescape rule).
5. **`test_extract_pause_review_path_rejects_empty`** —
   `review_path=""` → `MarkerError`, headline starts with
   `extract_pause_review_path:`.
6. **`test_extract_pause_review_path_rejects_absolute`** —
   `review_path="/etc/passwd"` → `MarkerError`.
7. **`test_extract_pause_review_path_rejects_traversal`** —
   `review_path="../escape.md"` → `MarkerError`.
8. **`test_extract_pause_review_path_rejects_traversal_nested`** —
   `review_path="a/../b.md"` → `MarkerError`.
9. **`test_extract_pause_review_path_rejects_nul`** —
   `review_path="a\x00b"` → `MarkerError`.
10. **`test_detect_in_text_pause_includes_review_path_when_present`** —
    a full pause block with `review_path="plan.md"`: the returned
    `SignalEmitted(kind="pause", args=...)` has `args["review_path"]
    == "plan.md"`.
11. **`test_detect_in_text_pause_omits_review_path_when_absent`** —
    a pause block without the attribute: `"review_path" not in
    args`. (Backwards-compat regression — skills not on 14b grammar
    are unaffected.)

Cases 1, 2, 5, 6, 7, 10, 11 are the minimum acceptance set. 3, 4, 8,
9 are belt-and-braces; ship them.

### Tests — `tests/orchestrator/test_loop.py`

Add **one** end-to-end test that drives the scripted harness through a
pause with the new attribute and asserts the persistence:

12. **`test_pause_signal_args_carries_review_path`** — extend the
    `PAUSE_BLOCK` fixture (or add a sibling `PAUSE_BLOCK_WITH_REVIEW`)
    that includes `review_path="improvement-plan.md"`. Drive the loop
    via `ScriptedHarness`; read back the paused iter via the sync
    engine; assert `iters[0].signal_args["review_path"] ==
    "improvement-plan.md"`. (One assertion + the existing
    `signal_kind == "pause"` and `next_prompt` checks; do not
    re-baseline the other pause tests.)

Coverage target: `extract_pause_review_path` reaches 100% line
coverage including every `MarkerError` branch (the cases enumerated
above hit each branch exactly once).

### Skill docs — `skills/engineering-team/pi/references/sentinels.md`

Add a new sub-section under "How to pause" titled **"Reviewable
pauses (`review_path`)"**, after the existing 5-step recipe. Content
shape (one paragraph + a worked example):

> ### Reviewable pauses (`review_path`)
>
> When the pause asks the user to **read or edit a file** (typically
> the improvement plan or a discussion note), add the optional
> `review_path` attribute to point at the file. The dashboard reads
> this and offers an inline editor; the run's event store records
> each save as an `artifact_edited` event (relay-v2 spec §3.2,
> ADR-40).
>
> `review_path` is **relative to `$RELAY_RUN_DIR`** (the run's
> artifacts dir, `<project_root>/.relay/runs/<run_id>/`). Absolute
> paths, `..` components, empty strings, and NUL bytes are
> rejected at parse time. Omit the attribute when the pause is a
> pure question that does not need an editable artifact — your
> `next_prompt` already tells the resumed session to re-read any
> files it needs, and the rest of the workflow is unchanged.
>
> Example:
>
>     [[engteam:prompt-start]]
>     The improvement plan is at `$RELAY_RUN_DIR/improvement-plan.md`.
>     Re-read it in full — the user may have edited it. Then start
>     Phase 3.
>     [[engteam:prompt-end]]
>
>     [[engteam:pause-for-input id="P1" question="Approve plan?"
>                               review_path="improvement-plan.md"]]
>
> The orchestrator stores `review_path` in the paused iter's
> `signal_args` alongside `id`, `question`, and `next_prompt`. The
> file does **not** need to be present on disk at the moment the
> sentinel is parsed — the dashboard 404s and offers a "Create at
> this path" state if the agent declared a path it never wrote.

Also update the verbs list at the top of the file: add `,
review_path="<path>" (optional, 14b)` to the `pause-for-input`
required-attrs line.

### MCP / REST — no change

No tool or route signature changes. 14a's `PUT /api/runs/:id/artifacts/*`
flips from documented-409 to working as soon as a paused iter's
`signal_args` carries the new key. The MCP `relay__pause_response`
signature is unchanged (still `(run_id, answer)` — edits remain a
separate REST entry point per ADR-40 §"Rejected — MCP
`relay__write_artifact` tool").

### Frontend — no change

14c is the dashboard work. 14b ships a `signal_args` field the
dashboard does not yet read; production behaviour is unchanged for
the operator.

## ADR — none

14b is the *implementation* of the A1 decision already recorded in
ADR-40 (the proposal's "Decision 1" — opt-in via sentinel attribute).
No new ADR; reference ADR-40 in the commit message and the
test/module docstrings.

If anything about the parse-time validation rules turns out to need
its own decision (e.g. "should `review_path` be allowed to point at a
nested-but-non-existent path?"), open an ADR-41 then. Default: no
new ADR.

## Verification

Backend gate (must be green before commit):

- `uv run ruff check .`
- `uv run mypy src/relay_v2/`
- `uv run pytest tests/harness/test_signaling_sentinels.py -v` — the
  11 new cases pass.
- `uv run pytest tests/orchestrator/test_loop.py::test_pause_signal_args_carries_review_path
  -v` — the integration test passes.
- `uv run pytest` — full suite green; backend coverage stays ≥ 94%.
  Expected pass count: current (313) + 12 = **325**. Pi-e2e (3)
  still gated.

No frontend test change; `npm run check` unaffected (run it once to
confirm no collateral).

## Acceptance criteria

- All 12 new cases pass.
- `uv run pytest`, `ruff`, `mypy --strict` all clean.
- `docs/spec.md` §5 mentions the new optional attribute.
- `skills/engineering-team/pi/references/sentinels.md` documents
  "Reviewable pauses".
- `CLAUDE.md` "Current state" walkthrough gains a 14b paragraph in
  the same shape as 14a's: dated, names the parser function +
  validation rules, references ADR-40, gives the pass count delta,
  names what 14b does NOT do (scope fence).
- No frontend file changed.

## Out of scope for 14b (recap)

- `compose_resume_prompt` annotation when edits happened → **14e**
  (deferred / optional).
- `PauseAnswerForm.vue` inline editor → **14c**.
- `KNOWN_EVENT_TYPES` + `INVALIDATING_KINDS` updates → **14c**.
- `TimelinePane.vue` row for `artifact_edited` → **14c**.
- Engineering-team Phase-2 template emits `review_path` → **14d**.
- Live engteam acceptance + journal entry → **14d**.

## Commit shape

One commit:

```
feat(harness): pause-for-input gains optional review_path attribute (14b)

- extract_pause_review_path with empty/absolute/traversal/NUL validation
- detect_in_text adds review_path to pause signal_args when present
- skills/engineering-team/pi/references/sentinels.md docs the new attribute
- spec.md §5 names the optional attribute

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

(CLAUDE.md update can ride the same commit or a follow-up — match
9a/14a cadence.)

## Notes for the executing session

- **The parser is the contract; the skill template (14d) is the
  consumer.** Don't slip 14d's `phase-2-planning.md` edit into 14b —
  that breaks the "each sub-phase independently shippable" property
  (14c is between them; a Phase-2 template emitting `review_path`
  with no dashboard editor in 14c is half a feature).
- **The `MarkerError` repair recipe is the operator-facing UI for
  parse failures.** Make it informative — the agent reads stderr
  during a no-signal close and will use the recipe to fix its next
  iter's output. Mirror the existing `_REPAIR` block in
  `extract_fanout_payload` (multi-line literal example + a pointer to
  `sentinels.md`).
- **The "review_path" key MUST be absent from `signal_args` when the
  attribute is absent.** A present-but-`None` key would be observable
  in the JSON column and break 14a's `if "review_path" not in
  signal_args` check (the no_review_path branch wouldn't fire on
  the pre-14b world).
- **Don't validate file existence at parse time.** The file is
  written by the same iter that emits the sentinel (the Phase-2
  prompt template); the dashboard's `GET /artifacts/<path>` 404s if
  the agent lied, and the editor shows a "Create at this path"
  state. Parse-time validation is **syntactic only**.
- **`PurePosixPath` is the right tool for `..` and absolute checks**
  — it matches the URL/path semantics of `review_path` (relative
  POSIX paths) without doing filesystem-level resolution.
- **One commit. 14b is mechanically small.**
