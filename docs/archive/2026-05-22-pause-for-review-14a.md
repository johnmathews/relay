# Plan — Phase 14a (pause-for-review backend: write endpoint + event)

**Status:** ready to execute
**Date:** 2026-05-22
**Source proposal:** `docs/proposals/pause-for-review.md` (sub-phase 14a)
**Predecessor of:** 14b (sentinel grammar), 14c (dashboard inline editor),
14d (skill update + live acceptance)
**Independent of:** anything else in flight.

## Goal

Land the **backend half** of pause-for-review: a sandboxed write
endpoint for run artifacts, a new `artifact_edited` event kind, and the
`RelayCore.write_artifact` adapter that route + (later) MCP can call —
all gated on the run being `paused` AND the requested path matching the
paused iter's `signal_args.review_path`.

After 14a, an operator using `curl` (or any HTTP client) can already
edit an artifact during a pause and the event store records it
correctly. No sentinel grammar change yet (14b), no UI yet (14c), no
skill change yet (14d).

This is intentionally the smallest shippable slice — pure REST + a
RelayCore method + an event kind + tests. No frontend, no MCP, no
harness/sentinel touch.

## Locked decisions (from proposal alignment — 2026-05-22)

These match the proposal's recommendations on each OQ. Cited inline so
the executing session does not have to re-resolve them:

- **OQ-1 — strict coupling.** `PUT /artifacts/{path}` requires
  `run.status == "paused"` AND `signal_args.review_path` on the latest
  paused iter to equal the requested path *after normalisation*.
  Mismatch / not-paused / running / unknown-run all return 409 with a
  precise `detail` string. **No** allowing ad-hoc writes any time.
- **OQ-3 — missing target file.** If the resolved path does not exist
  on disk yet, the PUT **creates** it (parents are not created — the
  artifacts dir itself is auto-created at `start_run`; intermediate
  subdirectories are rejected with 409 because `review_path` is a
  single path component or a sub-path under the artifacts dir, and
  14a does not need to support agent-declared subdirs that don't
  exist).

  Operationally this means: the agent must have already written the
  file (or its parent directory) at sentinel-emit time. The 14a code
  is tolerant of a file that doesn't exist *yet at the artifact path*
  but is strict about intermediate dirs.
- **OQ-4 — resume annotation.** Defer to 14b or later. 14a does NOT
  touch `compose_resume_prompt`. The event kind exists; whether and
  how to render it in the resume body is a question for 14b once the
  sentinel grammar carries the `review_path`.
- **OQ-7 — text only.** Body must be UTF-8 text. Binary bodies → 415,
  same rule as the GET endpoint. NUL-byte rejection comes for free
  via the encoding check.

## What 14a does NOT do

- Does not parse a `review_path` attribute (that's 14b).
- Does not change `compose_resume_prompt` (deferred per OQ-4).
- Does not surface anything in `PauseAnswerForm.vue` (that's 14c).
- Does not update `frontend/src/api/sse.ts::KNOWN_EVENT_TYPES` or
  `frontend/src/stores/events.ts::INVALIDATING_KINDS` — the event kind
  exists in the backend taxonomy but no frontend consumer references
  it until 14c. (Adding it to those lists *now* would be dead code; a
  small focused frontend change in 14c is cleaner.)
- Does not add an MCP tool (no `relay__write_artifact`).
- Does not add a content-versioning scheme (proposal §B2 rejected).
- Does not store edit content in event payloads (proposal §B3
  rejected for v1).
- Does not touch the spec §6.2 pause/resume narrative — that section
  is correct; the edit is *adjacent* to pause/resume, not part of it.

After 14a, the only callers of the new endpoint are: tests, and an
operator with `curl`. The dashboard wiring is 14c.

## File-by-file changes

### Spec — `docs/spec.md`

**§3.2 (event taxonomy) — add one row.** Place between
`harness_session_ended` and `iter_ended` (alphabetical-ish within
the iter-lifecycle group is fine; the established table is not
strictly alphabetical):

| kind | when emitted | payload shape |
|---|---|---|
| `artifact_edited` | dashboard (or other client) writes content to a run's artifact during a `paused` review (spec §6.2). Iter-scoped to the paused iter so replay can group edits under the pause that motivated them. | `{path, size_before, size_after, sha256_before, sha256_after, editor}` — `path` relative to the run artifacts dir; hashes are hex SHA-256; `editor` is a free-form string identifying the writer (default `"dashboard"` for the REST PUT). |

**§7 (REST API surface) — add one route.** Under the existing
"Run artifacts browser" group:

```
PUT    /api/runs/:id/artifacts/*  write text content to a sandboxed
                                  artifact file (pause-for-review,
                                  spec §6.2)
                                  body: {content: str, editor?: str}
                                  returns 200 {path, size, sha256}
                                  Requires run.status == 'paused' AND
                                  the requested path to match the
                                  latest paused iter's signal_args.
                                  review_path (set by 14b).
                                  In 14a, the review_path check is
                                  best-effort: if no review_path is
                                  present on the paused iter the
                                  endpoint returns 409 — see Notes.
                                  Reuses the §7 file-browser audited
                                  resolver (ADR-25, ADR-40); 400 on
                                  sandbox violation, 415 on non-text
                                  body, 413 on body > 5 MiB.
```

Add a one-paragraph note under the routes block (mirroring the
existing "The file browser is read-only and sandboxed…" paragraph):

> `PUT /api/runs/:id/artifacts/*` is the **single write entry point**
> on the artifacts dir. It is coupled to `runs.status == 'paused'` +
> `iters.signal_args.review_path` (set when the agent declares the
> file for review, 14b). The event store records every write as an
> `artifact_edited` event with content hashes (§3.2). Replay can
> verify *that* an edit happened; the file content lives on disk
> (per ADR-25 the artifacts dir is the authoritative artifact store,
> not the event store).

**Notes on the 14a interim state.** Until 14b ships the sentinel
grammar, no production code path writes `review_path` into
`signal_args`. The 14a endpoint therefore returns 409 for every
real-world paused run (because `signal_args.review_path` is missing).
This is the *correct* interim behaviour: no client should be calling
the endpoint yet, and the 409 is informative. Tests synthesise a
paused iter with `signal_args.review_path` directly to exercise the
happy path. The spec wording above accurately reflects this.

### Backend — `src/relay_v2/core.py`

**Add `write_artifact` method.** Place near `get_run_artifacts_dir`
(they're a natural pair). Signature:

```python
async def write_artifact(
    self,
    run_id: str,
    rel_path: str,
    content: str,
    *,
    editor: str = "dashboard",
) -> dict[str, Any]:
    """Write text content to a sandboxed artifact file during a paused
    review (spec §6.2, §7).

    Preconditions:
    - run exists, status == 'paused';
    - the latest paused iter has signal_args["review_path"] equal to
      `rel_path` (after normalisation — see _normalise_review_path).
    - `rel_path` resolves cleanly inside the run's artifacts dir
      under the audited :func:`resolve_within_sandbox`.

    Errors raised (mapped to HTTP by the route adapter):
    - PauseReviewError("not_paused"): status != 'paused'.
    - PauseReviewError("no_review_path"): paused iter has no
      review_path in signal_args.
    - PauseReviewError("path_mismatch"): rel_path != review_path.
    - SandboxViolation: traversal/absolute/NUL/symlink-escape.
    - UnknownRun: run row not found.

    On success: writes the file (creating it if absent — parents are
    NOT created), appends an `artifact_edited` event iter-scoped to
    the paused iter, returns `{path, size, sha256}`.
    """
```

The error class `PauseReviewError` carries a string `code` so the
route handler can map to the right HTTP status without string-matching
the message:

```python
class PauseReviewError(Exception):
    """Raised when write_artifact's preconditions are not met."""
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
```

`UnknownRun` already exists if a similar class is in `core.py`; if
not, raise a plain `ValueError` with a recognizable message and let
the route translate. (Check `core.py` for an existing exception
class for unknown runs — `get_run_artifacts_dir` returns `None`
for unknown runs, which is the established pattern. `write_artifact`
should also signal unknown runs by returning early via a
`PauseReviewError("unknown_run", …)` for uniform handling.)

Implementation outline (the executing session writes the body —
this is the contract, not the code):

1. Look up the run + project (same shape as `get_run_artifacts_dir`).
   `None` → `PauseReviewError("unknown_run", f"unknown run {run_id}")`.
2. Reject if `run.status != "paused"` →
   `PauseReviewError("not_paused", ...)`.
3. Load the latest paused iter via the existing
   `latest_paused_iter(self._sm, run_id)` helper (already in
   `orchestrator/lifecycle.py`).
   - `paused is None` → `PauseReviewError("no_review_path", ...)`.
   - `paused.signal_args is None or "review_path" not in
     paused.signal_args` → same.
   - `_normalise_review_path(paused.signal_args["review_path"]) !=
     _normalise_review_path(rel_path)` →
     `PauseReviewError("path_mismatch", ...)`.
4. Resolve the artifacts dir
   (`await self.get_run_artifacts_dir(run_id)`); if `None`, same
   "unknown_run" path.
5. `resolve_within_sandbox(artifacts_root, rel_path)` — propagates
   `SandboxViolation` to the caller. The resolver tolerates a
   non-existent target (strict=False in step 4 of its docstring), so
   we get a real path even for a brand-new file.
6. Pre-write integrity capture:
   - `existed = target.exists()`
   - if existed: `pre_bytes = target.read_bytes()`,
     `sha256_before = sha256(pre_bytes).hexdigest()`,
     `size_before = len(pre_bytes)`
   - else: `sha256_before = None`, `size_before = 0`
7. Reject non-text content: if `"\x00" in content[:8192]` →
   `PauseReviewError("binary", "content contains NUL byte")`.
   (The route additionally rejects non-UTF-8 request bodies upstream.)
   Reject oversize: `if len(content.encode("utf-8")) > MAX_FILE_BYTES`
   → `PauseReviewError("too_large", ...)` (mirror the GET 413 limit
   exactly — reuse the constant from `api/files.py`).
8. Reject intermediate-dir-creation: if `target.parent !=
   artifacts_root`, the path lies in a subdir; only allow if that
   subdir already exists. Otherwise →
   `PauseReviewError("missing_parent_dir", ...)`. This keeps 14a
   focused on edit-existing-or-flat-create; nested-dir review paths
   are out of scope for v1 (proposal §OQ-3 footnote).
9. Write atomically via tempfile-in-same-dir then rename (so a
   crash mid-write doesn't leave a half-written plan). Standard
   pattern: `(target.parent / f".{target.name}.tmp.{os.urandom(4)
   .hex()}").write_text(content, encoding="utf-8")` then `replace`.
10. Post-write capture: `sha256_after`, `size_after`.
11. Append the event:

    ```python
    await self._store.append(
        run_id,
        "artifact_edited",
        {
            "path": str(rel_path_normalised),
            "size_before": size_before,
            "size_after": size_after,
            "sha256_before": sha256_before,  # None if create
            "sha256_after": sha256_after,
            "editor": editor,
        },
        iter_id=paused.id,
    )
    ```
12. Return `{"path": str(rel_path_normalised), "size": size_after,
    "sha256": sha256_after}`.

**`_normalise_review_path` helper** (module-private):

```python
def _normalise_review_path(p: str) -> str:
    """Normalise a review_path for comparison: strip leading './',
    collapse `/./`, but do NOT resolve symlinks (that's the sandbox
    resolver's job)."""
    return str(PurePosixPath(p))
```

Two paths are equal iff their normalised forms are equal. The sandbox
resolver still runs against the unnormalised user input — this helper
is *only* for the `signal_args["review_path"]` vs request-path
equality check.

### Backend — `src/relay_v2/api/artifacts.py`

**Add the PUT route.** Place after `get_artifact`:

```python
@router.put("/runs/{run_id}/artifacts/{file_path:path}")
async def put_artifact(
    run_id: str,
    file_path: str,
    request: Request,
) -> JSONResponse:
    """Write text content to a sandboxed artifact file during a paused
    review (spec §6.2). Thin adapter over
    :meth:`RelayCore.write_artifact`.

    Body: ``{"content": str, "editor"?: str}``. 415 if the body cannot
    be parsed as JSON or `content` is not a UTF-8 string. 400 on
    sandbox violation. 409 on pause/precondition errors. 413 on
    oversize. 200 with ``{path, size, sha256}`` on success.
    """
    core = get_core(request)
    try:
        body = await request.json()
    except Exception:  # pragma: no cover — FastAPI normalises this
        return _err(415, "body must be application/json")
    content = body.get("content")
    if not isinstance(content, str):
        return _err(415, "body.content must be a UTF-8 string")
    editor = body.get("editor", "dashboard")
    if not isinstance(editor, str):
        return _err(415, "body.editor must be a string")

    try:
        result = await core.write_artifact(
            run_id, file_path, content, editor=editor
        )
    except PauseReviewError as exc:
        # All PauseReviewError variants except too_large/binary are 409.
        # too_large → 413; binary → 415; missing_parent_dir → 409.
        if exc.code == "too_large":
            return _err(413, exc.detail)
        if exc.code == "binary":
            return _err(415, exc.detail)
        if exc.code == "unknown_run":
            return _err(404, exc.detail)
        return _err(409, exc.detail)
    except SandboxViolation as exc:
        return _err(400, str(exc))

    return JSONResponse(status_code=200, content=result)
```

(Import `PauseReviewError` from `relay_v2.core`; `SandboxViolation`
from `relay_v2.api.files`.)

### Tests — `tests/api/test_artifacts.py`

Extend the existing module (same stub-core pattern). Add a small
helper class hierarchy so the stub can answer `write_artifact` calls
without a full RelayCore. Or: add a second test file
`tests/api/test_artifacts_write.py` that spins up a real
`create_app(settings, harness=ScriptedHarness(…))` (the established
pattern from `tests/api/test_runs.py`) so the endpoint exercises the
real `RelayCore.write_artifact` against an in-memory aiosqlite DB.

**Strong preference: integration-style tests against a real
`RelayCore`** (the pattern matches `test_runs.py` and gives end-to-end
coverage of the precondition checks + event store append). The
existing `test_artifacts.py` uses a stub; that file stays as-is, and
14a adds `test_artifacts_write.py` next to it.

Cases to cover (one assert-tight test each):

1. **Happy path — edit existing file.** Seed a paused run with
   `signal_args["review_path"] = "plan.md"` and a `plan.md` on disk.
   `PUT /api/runs/<id>/artifacts/plan.md` with new content → 200.
   Returned `sha256` matches the new content. The file on disk
   matches. An `artifact_edited` event landed against the paused iter
   with correct hashes (before != after, both non-None).
2. **Happy path — create file (didn't exist yet).** Same setup but
   `plan.md` absent. PUT succeeds. `sha256_before` is `null` in the
   event payload, `size_before` is `0`.
3. **409 — not paused.** Same setup but run.status == "running".
   PUT returns 409 with `detail` containing `"not_paused"` or similar
   recognisable substring.
4. **409 — no review_path.** Paused run, but signal_args has no
   `review_path` key. PUT returns 409.
5. **409 — path mismatch.** Paused run, `review_path = "plan.md"`,
   PUT to `evil.md` → 409.
6. **404 — unknown run.** PUT to a nonexistent run id → 404.
7. **400 — traversal.** PUT to `../escape.md` → 400.
8. **400 — absolute.** PUT to `/etc/passwd` → 400.
9. **415 — non-string content.** Body `{"content": 42}` → 415.
10. **415 — content with NUL byte.** Body `{"content": "a\x00b"}`
    → 415.
11. **413 — oversize.** Body with content > `MAX_FILE_BYTES` → 413.
12. **409 — missing parent dir.** `review_path =
    "discussions/notes.md"` but `discussions/` not yet created → 409
    (`missing_parent_dir`). Then create `discussions/` and the same
    PUT succeeds — verifies the gate flips correctly.
13. **`signal_args.review_path` normalisation.** `review_path =
    "./plan.md"` and PUT to `plan.md` → 200. (Equality is
    `_normalise_review_path`-equal, not string-equal.)
14. **Atomic write — no partial file on simulated failure.** Skip
    if it requires monkeypatching `Path.replace`; otherwise verify
    that the temp-file pattern leaves no `.plan.md.tmp.*` siblings
    after a successful write.
15. **Event iter-scoping.** The appended event's `iter_id` matches
    the paused iter's id (not run-level).

Cases 1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 15 are the minimum acceptance
set. 8, 11, 13, 14 are belt-and-braces; ship them.

**Coverage target:** `RelayCore.write_artifact` reaches 100% line
coverage including every `PauseReviewError` branch.

### MCP — no change

`src/relay_v2/mcp/server.py` is intentionally untouched. A
`relay__write_artifact` MCP tool is out of scope (proposal §"REST
surface"). If a future iteration wants it, the adapter is trivial
(same `RelayCore.write_artifact` call) but the *use case* for an
agent-driven artifact edit during its own pause is unclear, so v1
ships REST-only.

### Frontend — no change

No file in `frontend/` is touched in 14a. `KNOWN_EVENT_TYPES` /
`INVALIDATING_KINDS` updates are 14c work, alongside the editor UI
that consumes them.

### Docs (operational) — `docs/api.md`

If `docs/api.md` documents the artifacts routes, add the PUT route
to that section (one paragraph mirroring the GET descriptions). If
it just points at the OpenAPI doc, no change needed beyond the
auto-generated schema picking up the new route.

(Quick verification step in the implementing session: `grep -n
"artifacts" docs/api.md` to see whether prose documents the routes
explicitly.)

## ADR-40 (open it as part of 14a)

This is the first PR in the pause-for-review arc that changes a
contract (spec §3.2, §7). The proposal explicitly recommended
deferring ADR-40 until "a decision genuinely changes a contract" —
that point is now.

**ADR-40 — pause-for-review: write endpoint coupled to paused +
review_path, content on disk + hash-bearing event.**

Captures three decisions:

1. **A1 vs A2/A3** — opt-in via sentinel attribute (A1). A2 and A3
   rejected per proposal.
2. **B1 vs B2/B3** — direct in-place write + `artifact_edited` event
   with hashes (B1). B2 and B3 rejected for v1, B3 forward-compatible.
3. **OQ-1 strict coupling** — PUT requires `paused` status AND a
   matching `review_path`. Honestly named "edits only during a
   declared review moment" — not a general write API.

ADR-40's body mirrors the structure of ADR-39 (the most recent
event-taxonomy-extension ADR): Context → Decision → Rationale →
Consequences → Implementation notes. ~150–250 lines, well under the
9c/9f-era ADRs which carried much heavier architectural weight.

Implementation note in ADR-40: the 14a release ships the endpoint
returning 409 for all real-world callers because `review_path` isn't
populated until 14b. That is correct interim behaviour; the test
suite synthesises the post-14b world to exercise the happy path.

## Verification

Backend gate (must be green before commit):

- `uv run ruff check .`
- `uv run mypy src/relay_v2/`
- `uv run pytest tests/api/test_artifacts_write.py -v` — every new
  case passes.
- `uv run pytest` — full suite remains green. Existing tests
  (`test_artifacts.py`, `test_files.py`, `test_runs.py`,
  `test_sse.py`, `test_openapi.py`) should be **unaffected**: 14a
  adds surface, removes nothing. Expected backend pass count is
  current + (number of new cases in test_artifacts_write.py).
  Coverage stays >= 94% (the established 9-series floor).
- `uv run pytest tests/api/test_openapi.py` — the OpenAPI validator
  picks up the new PUT route automatically; verify the spec is still
  v3.1-valid (this test should pass without any change to the
  validator).
- Manual: `curl -X PUT
  http://127.0.0.1:7800/api/runs/<id>/artifacts/plan.md -H
  "Content-Type: application/json" -d '{"content": "# Edited\n"}'`
  against a locally seeded paused run with `review_path` populated —
  should return 200 + a JSON body with hashes.

Frontend gate: untouched. `npm run check` should pass without
modification (no frontend files changed); run it to confirm no
collateral breakage.

## Acceptance criteria

- All 12+ cases in `tests/api/test_artifacts_write.py` pass.
- `uv run pytest`, `ruff`, `mypy --strict` all clean.
- `docs/spec.md` §3.2 + §7 updated; the OpenAPI spec validates.
- `docs/decisions.md` gains ADR-40.
- `CLAUDE.md` "Current state" walkthrough gains a 14a paragraph in
  the same shape as the post-9g bug-fix sweep paragraph: dated
  (2026-05-22 or whatever the actual landing date), names the new
  event kind + endpoint + RelayCore method, names the ADR, gives the
  pass count delta, names what 14a does NOT do (deliberate scope
  fence).
- No frontend file changed.

## Out of scope for 14a (recap)

- Sentinel grammar `review_path` attribute → **14b**.
- `compose_resume_prompt` annotation when edits happened → **14b**
  (probably) or deferred entirely per OQ-4.
- `PauseAnswerForm.vue` inline editor → **14c**.
- `KNOWN_EVENT_TYPES` + `INVALIDATING_KINDS` updates → **14c**.
- `TimelinePane.vue` row for `artifact_edited` → **14c**.
- engineering-team Phase-2 template change → **14d**.
- Live engteam acceptance + journal entry → **14d**.

## Commit shape

One commit (per your standard cadence):

```
feat(api): pause-for-review write endpoint + artifact_edited event (14a)

- RelayCore.write_artifact with paused+review_path strict coupling
- PUT /api/runs/:id/artifacts/* (sandboxed, hashes the write)
- New event kind artifact_edited (iter-scoped to the paused iter)
- spec.md §3.2 + §7; ADR-40

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

(Pre-commit hooks run; do not skip them. CLAUDE.md update can ride
the same commit or a follow-up — match the cadence of 9a–9g, which
varied.)

## Notes for the executing session

- **The proposal is the contract; this plan is the recipe.** If a
  decision needs revisiting (e.g. "PUT should also accept query
  param X"), pause and update the proposal + this plan first; do
  not extend scope inline.
- **ADR-40 is part of 14a, not optional.** Adding a new event kind +
  endpoint is a contract change and the established cadence (ADR-34
  / 35 / 36 / 37 / 38 / 39) is one ADR per contract-changing
  sub-phase. ADR-40's three decisions are pre-resolved in the
  proposal; the ADR just records them in the canonical place.
- **The strict-coupling 409 will look strange in a manual curl
  test until 14b ships.** That's correct. Test cases synthesise
  `signal_args.review_path` directly to exercise the happy path.
- **The artifacts dir already exists when a run starts** (per the
  Bug 1 fix on 2026-05-23 — `provision_workspace` now puts it under
  the project's `.relay/`, not the relay-global `.relay/`). 14a
  inherits that; no workspace plumbing change is needed.
- **Don't add the frontend constants yet.** The proposal's 14c step
  adds `'artifact_edited'` to `KNOWN_EVENT_TYPES` and
  `INVALIDATING_KINDS`. Adding them in 14a is dead code — nothing
  consumes the event until the editor UI lands.
- **Atomic write matters.** Use the temp-file-rename pattern; a
  crash mid-write should not leave the operator's plan in a
  half-saved state. The pattern is `(parent / f".{name}.tmp.{rand}").
  write_text(...)` then `.replace(target)`. `os.replace` is atomic
  on POSIX same-filesystem; the temp file lives in the same dir as
  the target so it's always same-filesystem.
- **Run the full test suite after the last change**, not just the
  new file. Adding a route to FastAPI implicitly changes the OpenAPI
  document; `test_openapi.py` will catch any malformed schema.
