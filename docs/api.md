# REST API — operational reference

> Phase 3 deliverable. Implementation reference for `src/relay_v2/api/`
> and `src/relay_v2/sse.py`. **Canonical design is `spec.md` §7**;
> ADR-07/15 (single-RelayCore write path), ADR-10 (event store is the
> source of truth), ADR-12 (single-user localhost), ADR-23 (SSE
> broadcaster), ADR-24 (test toolchain) carry the rationale. When this
> doc disagrees with `spec.md`, `spec.md` wins.

## Shape

FastAPI app (`relay_v2.app:create_app`). One process, bound to
`127.0.0.1:7800` by default (ADR-12 — no auth, no multi-user;
`user_id` stays the sentinel). `relay serve` runs it via uvicorn.
Auto-generated OpenAPI 3.1 at `GET /openapi.json`, operations grouped by
resource tag (`runs`, `projects`, `prompts`, `files`, `events`,
`artifacts`).

**Every route is a thin adapter over the single shared `RelayCore`**
(`app.state.core`, owned by the lifespan — ADR-07/15). Handlers resolve
the core via the `get_core` dependency (`relay_v2.api.deps`) and call a
`RelayCore` service method; they never touch the DB, an engine, a
sessionmaker, or ORM models directly. New read/write capability is a new
`RelayCore` method, not route logic. `relay_v2.api.include_api_routers`
is the single place all routers are mounted (called from `create_app`).

## Endpoints

All paths are prefixed `/api`. Request/response bodies are Pydantic v2
models in `relay_v2.api.schemas` (response models use
`from_attributes=True` over ORM rows).

### Runs

| Method | Path | RelayCore method | Notes |
|---|---|---|---|
| POST | `/runs` | `start_run` (+`get_prompt` to resolve `prompt_id`) | body `{project_id, prompt_body \| prompt_id, max_iters?, iter_timeout?}` — exactly one prompt source; 201 |
| GET | `/runs` | `list_runs` | query `project_id?, status?, limit=50, offset=0` (status filter + pagination applied in the handler — presentation only); query also accepts `include_children=false` (default — top-level runs only; pass `true` to include child runs from fanout, 9e) |
| GET | `/runs/{run_id}` | `get_run` + `list_iters` | includes `iters[]` + current status; 404 if absent |
| GET | `/runs/{run_id}/children` | `list_children` | direct children of a parent run (no recursion); `[]` when no fanout; 404 if `run_id` unknown (9e) |
| POST | `/runs/{run_id}/cancel` | `cancel_run` | returns the updated run; 404 if `run_id` unknown |
| DELETE | `/runs/{run_id}` | `delete_run` | cascade-deletes the run + all events / iters / descendants. DB-only — **never deletes files on disk** (worktree + run artifacts remain); mirror of `DELETE /projects/{id}`. 204; 404 if unknown; 409 if `running` / `awaiting_children` (cancel first) |
| POST | `/runs/{run_id}/resume` | `resume_run` | body `{answer}`; not-paused / already-running → 409; unknown run or deleted project → 404 |
| GET | `/runs/{run_id}/events` | `list_events` | paginated replay; query `after_seq=0, limit=100, offset=0` |
| GET | `/runs/{run_id}/preview` | `preview_run` | **no side effects** — no run row, event, or dir. Path segment is the **project id** (the New-Run wizard previews a prospective run; no run exists yet — see "Preview" below). query `prompt_body? \| prompt_id?, phase?` |

### Live event stream (SSE)

| Method | Path | Notes |
|---|---|---|
| GET | `/events/{run_id}` | `text/event-stream`. SSE `id:` = event `seq`. `Last-Event-ID` header (or `?last_event_id=` fallback) drives replay. Contract: **ADR-23**. |

Connect flow (ADR-23): for a **live** run the route subscribes to the
in-process broadcaster *first*, replays DB history with
`seq > Last-Event-ID` (paginated), then drains the live subscription
forwarding only `seq > max_replayed_seq` — gap-free and duplicate-free
across reconnects. `run_ended` closes the stream immediately. For a
**finished** run (`done`/`failed`/`cancelled`; `paused` is *not*
finished — it can resume) it streams paginated history then EOF, and
returns a real `204` only when there are no events at/after
`Last-Event-ID`. The `204` carries `Content-Type: text/event-stream`
(not the FastAPI default `text/plain`) so browsers' `EventSource`
treats it as a clean end-of-stream instead of aborting with a MIME
mismatch — load-bearing for short-running runs whose reconnect lands
on the empty tail (Phase 9e smoke fix, 2026-05-22). Headers include
`X-Accel-Buffering: no` so an nginx reverse proxy does not buffer
the stream. The broadcaster is a passive
post-commit observer on `EventStore.append` (ADR-10 — SSE never writes
events, never assigns seq); a slow subscriber hits a bounded queue and
gets a clean close (it reconnects and replay backfills).

#### Heartbeat frame (ADR-45 Plan A)

On an idle live stream the generator emits a named `heartbeat` SSE
frame at `_KEEPALIVE_S` cadence (default 5s) — a successor to the
former bare `: keepalive` comment. The frame:

* uses `event: heartbeat`, payload `{run_id, server_ts, last_event_ts}`;
* **omits** `id:` so the browser's `Last-Event-ID` cursor is unchanged
  (heartbeats are not persisted; bumping the cursor would point at a
  phantom DB row on reconnect — per WHATWG SSE, a message without `id`
  leaves the last-event-id buffer alone);
* is consumed by the dashboard's `RunHealthBadge` to render a live
  "alive · last activity Xs ago" indicator without polling the DB.

`last_event_ts` is the ts of the most recently forwarded event on
*this* connection (or `null` if none yet — a brand-new connection
that reconnected past the tail). Heartbeats never enter `events`,
never bump `lastSeq`, and never trigger Colada invalidations.

#### Assistant delta frame (ADR-46 Plan B)

In addition to persisted events the SSE stream may carry
`assistant_delta` frames — the harness mapper yields one
`AssistantTextDelta` per pi `text_delta` / `thinking_delta`, and
`EventStore.store_harness_event` routes them to
`Broadcaster.publish_ephemeral` instead of appending. The wire frame:

* `event: assistant_delta`, payload
  `{iter_id, turn_seq, delta_seq, text, kind}` (kind ∈ `"text"`,
  `"thinking"`);
* **omits** `id:` — same shape as heartbeat; the browser's
  Last-Event-ID cursor is unchanged across deltas (a dropped delta
  is recoverable because the canonical `AssistantText` is persisted
  and replay backfills it on reconnect).

Deltas are consumed by the dashboard's pending-turn pseudo-rows
(`TimelinePane.vue`'s `pendingTurns` prop); the persisted
`AssistantText` flushed at `turn_end` replaces them automatically.
The replay path returns no delta rows — deltas are never persisted
(spec.md §3.2 carries no event kind for them; ADR-46).

### Projects

| Method | Path | RelayCore method | Notes |
|---|---|---|---|
| GET | `/projects` | `list_projects` | |
| POST | `/projects` | `register_project` | body `{root_path, name}`; `root_path` is `expanduser`-ed then `resolve`d and must be an existing directory (else **400**); idempotent on the normalised path; 201 |
| GET | `/projects/{id}` | `get_project` | 404 if absent |
| DELETE | `/projects/{id}` | `delete_project` | cascade-delete the project: every run (and its events / iters / descendants) + every project-scoped prompt (`Prompt.project_id == id`; global prompts with `project_id is None` are unaffected) + the project row itself. **DB-only — never deletes files on disk** (worktrees / `.relay/runs/`). 204; 404 if unknown; 409 if any run is currently active (`running`/`awaiting_children`) — cancel first. |

### System

| Method | Path | Notes |
|---|---|---|
| GET | `/system/browse` | Read-only directory listing for the dashboard's register-project directory picker. Query `path=` (default `~`). Returns `{path, parent, entries:[{name,path}]}` — entries are subdirectories only, sorted case-insensitive by name; `parent` is `null` at the filesystem root. **NOT sandboxed** (single-user, localhost MVP per ADR-12 — picking a project root inherently needs full-FS access; never expose by changing `RELAY_HOST` from `127.0.0.1`). `path` is `expanduser`-ed then `resolve`d; missing path or non-directory → **404**; permission denied → **403**. |

### File browser (read-only, sandboxed)

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/files` | dir listing; query `path=` (default = project root); `{path, entries:[{name,is_dir,size,modified}]}`, dirs-first then name-asc |
| GET | `/projects/{id}/files/{file_path:path}` | file content `{path, content, size, modified}`; binary → 415; >5 MiB → 413 |

### Run artifacts (read + single write entry — ADR-25, ADR-40/41)

| Method | Path | RelayCore method | Notes |
|---|---|---|---|
| GET | `/runs/{id}/artifacts` | sandboxed list under `<project_root>/.relay/runs/<run_id>/`; same shape as the file-browser dir listing; 404 if the run has no artifacts dir |
| GET | `/runs/{id}/artifacts/{file_path:path}` | sandboxed file read; same 400 / 404 / 413 / 415 mapping as the project file-browser GET (404 here also covers a run with no artifacts dir) |
| PUT | `/runs/{id}/artifacts/{file_path:path}` | `write_artifact` | body `{content: str, editor?: str}`. **Single write entry on the run artifacts dir.** Coupled to `run.status == 'paused'` AND set-membership in the paused iter's `signal_args.review_paths` (14b/14f; legacy scalar `review_path` is read as a one-element list during the migration window). 200 `{path, size, sha256}`; 400 sandbox violation, 404 unknown run, 409 `not_paused` / `no_review_path` / `path_mismatch` / `missing_parent_dir`, 413 oversize, 415 binary or non-JSON / malformed body (also covers `content` not a string and `editor` not a string). Every success appends one `artifact_edited` event iter-scoped to the paused iter (§3.2) |

**Sandbox.** All confinement is in one audited function,
`relay_v2.api.files.resolve_within_sandbox(root, rel)`. It rejects (→
HTTP **400**, `SandboxViolation`): a NUL byte, an absolute path, any
`..` component (lexical, before touching the filesystem), and any
symlink whose real target escapes the project root (`root.resolve()` +
`(root/rel).resolve()` + `is_relative_to`). Non-existence is a separate
**404** so a probe cannot distinguish "blocked" from "absent". Binary =
a NUL in the first 8 KiB → **415**. There is no write or delete file
route. URL-encoded traversal (`%2e%2e%2f`) is decoded by Starlette
before the handler, so the decoded value is what gets validated.

### Prompts (versioned)

| Method | Path | RelayCore method | Notes |
|---|---|---|---|
| GET | `/prompts` | `list_prompts` | query `project_id?`; latest version of each `(project_id, name)` |
| GET | `/prompts/{id}` | `get_prompt` | a specific version row; 404 if absent |
| POST | `/prompts` | `create_prompt` | body `{project_id?, name, body}` → version 1; duplicate `(project_id,name)` → 409; unknown project → 404 |
| PUT | `/prompts/{id}` | `update_prompt` | snapshot bump: inserts a NEW row at `max(version)+1`, old rows untouched |
| DELETE | `/prompts/{id}` | `delete_prompt` | deletes **all** versions of that `(project_id,name)`; 204, or 404 |
| GET | `/prompts/{id}/versions` | `list_prompt_versions` | all versions asc |

A `NULL` `project_id` (global prompt) is its own namespace —
`is_not_distinct_from` is used so `NULL` project prompts version and
delete correctly rather than mismatching on `NULL = NULL`.

## Preview semantics

`spec.md` §7 lists preview under `/api/runs/:id/preview`, but the
dashboard's New-Run wizard previews a run that does **not exist yet** —
there is no run id. The path segment is therefore interpreted as the
**project id**, and the prospective prompt is supplied via
`prompt_body` / `prompt_id` query params. `preview_run` resolves the
body, builds the `RELAY_*` preamble with a placeholder `<preview>` run
dir (never created), and returns `{preamble, body, prompt, run_dir}`
with zero DB or filesystem writes. (Recorded here as the Phase 3
resolution of this spec ambiguity; no contract change to RelayCore.)

## Error mapping

`RelayCore` signals domain failures as `ValueError`.
`relay_v2.api.deps.http_error` maps them: state conflicts ("is not
paused", "is already running") → **409**; preview bad-request
("must be provided") → **400** at the call site; everything else
(unknown entity, "no saved pause prompt", resume of a run whose project
was deleted) → **404**.

`RelayCore.write_artifact` (14a) uses its own
`PauseReviewError(code, detail)` rather than `ValueError` so the
artifacts PUT route can map each code without string-matching.
`unknown_run → 404`, `too_large → 413`, `binary → 415`, every other
code (`not_paused` / `no_review_path` / `path_mismatch` /
`missing_parent_dir`) → **409**. Sandbox violations from the shared
`resolve_within_sandbox` resolver propagate as `SandboxViolation` →
**400**.

## Testing (ADR-24)

`tests/api/` uses `pytest-asyncio` (`asyncio_mode="auto"`) +
`httpx.AsyncClient` over `ASGITransport`, entering the real lifespan via
`app.router.lifespan_context` so the shared `RelayCore` exists. A
scripted-harness injection seam on `create_app(settings, *, harness=)`
(mirrors the existing `settings` seam — production passes nothing) lets
`POST /api/runs` drive a `ScriptedHarness` so the suite never spawns pi.
`tests/orchestrator/` and `tests/harness/` keep the `asyncio.run()`
pattern; both coexist under one config. `openapi-spec-validator` asserts
the generated schema is valid OpenAPI v3. Pi e2e stays gated behind
`PI_INTEGRATION=1`.
