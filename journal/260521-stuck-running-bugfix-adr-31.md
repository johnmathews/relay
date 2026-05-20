# 2026-05-21 — Stuck-`running` bugfix; ADR-31

## What happened

User reported "I start a run and nothing happens" while driving the
local dashboard. Backend logs showed only a polling cycle: `GET
/api/runs/<id>` and `GET /api/events/<id>` repeating, occasional
`POST /api/runs/<id>/cancel`. No errors, no further events.

Direct DB inspection of `.relay/relay.db` showed the run was alive but
frozen: `status='running'`, `worktree_path=NULL`, exactly two events
(`run_started`, `iter_started`), one iter row with no `exit_reason`.
`ps` showed no `pi` subprocess — so pi had either never spawned or
already exited. The run task itself was simply gone with no trace.

## Root cause (two bugs, one symptom)

1. **Bug A — registration:** the user registered the project via the
   dashboard form with `~/projects/documentation`. The handler called
   `Path(body.root_path).resolve()`, which makes a relative path
   absolute but does **not** expand `~`. So the row that landed in the
   DB was `/Users/john/projects/relay/relay-v2/~/projects/documentation`
   — literal `~`. That path does not exist on disk.
2. **Bug B — orchestrator silent failure:** when `start_run` enqueued
   the run, the loop opened iter 1 (writing `iter_started`) and then
   called `PiHarness.spawn(..., cwd=<bogus path>)`. The subprocess
   spawn raised `FileNotFoundError` because the cwd does not exist.
   That exception unwound through `run_loop` into `RelayCore._run`,
   whose inner `try/except` caught only `asyncio.CancelledError` —
   so the exception propagated past the `finally` (which set
   `state.settled` but made no DB write) and was discarded by the
   supervisor's `task.add_done_callback(self._tasks.discard)`.

Net effect: `runs.status` stayed `running` forever, no `iter_ended`,
no `run_ended`. The dashboard's SSE stream had nothing more to deliver,
and the user's cancel button hit a code path
(`state.session_handle.session is None` because spawn never returned a
session) that did nothing observable. Hence the polling loop in the
backend log.

## Fix

Two surgical edits + one ADR.

### Bug A — `register_project` (lifecycle + API route)

`orchestrator/lifecycle.py:register_project` now normalises at the
boundary:

```python
expanded = Path(root_path).expanduser().resolve()
if not expanded.is_dir():
    raise ValueError(
        f"project root_path does not exist or is not a directory: "
        f"{expanded}"
    )
```

`api/projects.py:create_project` catches that `ValueError` and maps it
to **400** via `http_error(exc, default_status=400)`. Tests in
`tests/api/test_w2_routes.py`:

- `test_project_register_expands_tilde` — `HOME` is monkey-patched to
  a tmp tree; `POST {"root_path": "~/proj", ...}` returns 201 with the
  expanded path.
- `test_project_register_rejects_missing_path` — non-existent path
  returns 400 with `"does not exist"` in the detail.

### Bug B — `RelayCore._run` failsafe

`src/relay_v2/core.py` gains an `except Exception` peer to
`except asyncio.CancelledError`, wrapping both `run_loop` and the
`_apply_result` call (so a DB write failure mid-finalisation is also
captured). On entry it:

1. logs `logger.exception("run %s failed with internal error", ctx.run_id)`
   via a new module logger (so the operator sees a stack trace in
   the uvicorn log, not a silent GC notice);
2. sets `state.result = LoopResult("failed", reason="internal_error",
   summary=str(exc))` so awaiters of `wait_for_run` unblock with a
   terminal verdict;
3. best-effort writes `set_run_status("failed", ended=True)` and
   appends `run_ended` with `{"status": "failed", "summary":
   f"internal_error: {exc!s}"}` — wrapped in
   `contextlib.suppress(Exception)` mirroring the cancellation branch
   (engine may be mid-dispose during `aclose()`);
4. does NOT re-raise — the supervisor already discards the task
   handle, so re-raising only produces "Task exception was never
   retrieved" on GC.

Test `tests/orchestrator/test_loop.py::
test_internal_error_finalises_run_as_failed` uses a `RaisingHarness`
double whose `spawn` raises `FileNotFoundError`. It asserts the run
ends `failed`, `wait_for_run` returns within 5 s, and the closing
event is `run_ended` with an `internal_error:` summary.

### ADR-31

Recorded as `docs/decisions.md` ADR-31 with full alternatives
considered (status-quo / re-raise / new `internal_error` status all
rejected). Pulled into `CLAUDE.md` "Current state" alongside ADR-29
and ADR-30. `docs/orchestrator.md` and `docs/api.md` updated to reflect
the new run-lifecycle behaviour and the 400 registration response.

## Verification

| Gate | Result |
|---|---|
| `uv run ruff check .` | clean |
| `uv run mypy` | clean (38 source files) |
| `uv run pytest` (excluding pi-e2e) | 197 passed, 1 skipped (was 194 + 3 new tests) |
| Backend coverage | 93% |
| `frontend/ npm run check` | 136/136 passed (no frontend changes — sanity) |

## Operator unblock recipe

The user's running instance had stuck data; provided two SQL
statements to fix the project row and finalise the stuck run without
restarting the server:

```sh
sqlite3 .relay/relay.db "UPDATE projects SET root_path='/Users/john/projects/documentation' WHERE id=1;"
sqlite3 .relay/relay.db "UPDATE runs SET status='failed', ended_at=CURRENT_TIMESTAMP WHERE id='20260520-171615-3cd7';"
```

## What this didn't touch

The latent gap ADR-29 / ADR-30 fence off — `agent_end`/`SessionEnded`
not being persisted as an `events` row on the *successful* sentinel-
close path — is unrelated. ADR-31 is about the *exception* close path.
Closing the ADR-29 gap is still owner work with its own ADR.
