# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**MVP is done — Phases 0–8 complete (2026-05-19, ADR-30).** Per-phase
plans live in `docs/plans/`; ADR rationale in `docs/decisions.md`.

- **0** — scaffold (uv/ruff/mypy --strict/pytest/py.typed).
- **1** — `harness/`: pi event mapping (ADR-04/18) + text-sentinel signaling (ADR-05).
- **2** — `orchestrator/` + `core.py`: `RelayCore` + EventStore + chained-iter loop + RELAY_* preamble + pause/resume (ADR-07/10/15/17/19–22).
- **3** — `api/` + `sse.py`: spec §7 REST + SSE broadcaster (post-commit fan-out + `Last-Event-ID` replay) — ADR-23/24/25.
- **4** — `frontend/`: Vue 3 dashboard, primary control plane (ADR-15/26).
- **5** — `mcp/`: FastMCP at `/mcp`, seven `relay__*` tools as `RelayCore` adapters; #1367 lifespan footgun handled — ADR-27.
- **6** — `skills/engineering-team/pi/` bundled into the package; ADR-44 superseded the original `relay install-skill` delivery with `pi --skill <bundled-path>` injection at spawn time (ADR-28/33/44).
- **7** — `observability/`: opt-in OTel → Langfuse, strict no-op when off; Option-D `AssistantText` lookahead for terminal-sentinel usage — ADR-29.
- **8** — README + `api/static.py` (prod SPA mount) + Dockerfile + CI (Python+frontend gate + GHCR publish to `ghcr.io/johnmathews/relay`); ADR-30 = automated-vs-manual split.

**ADR-31 (still load-bearing)** — non-`Cancelled` exception out of the
loop / `_apply_result` finalises as `failed` + `run_ended
internal_error: …` (no permanently-running rows); `register_project`
`expanduser`+exists check; startup orphan sweep + `cancel_run` safety
net for stuck 'running' rows (single-user/-process per ADR-12).

**Two post-MVP arcs then shipped on top, both complete:** fanout-join
(9a–9g, ADRs 34–39) and pause-for-review (14a–14f, ADRs 40–41). The
codebase is now in an **MVP-acceptance-testing phase** — feature work
is parked until the gates close (`docs/acceptance-testing.md`). Counts:
**371 backend + 3 pi-e2e gated**, **234 frontend**, **47 ADRs**, **40
source files**, **`ruff`/`mypy --strict` clean**, **95% backend
coverage**.

Operational refs:
`docs/{harness,orchestrator,api,dashboard,mcp,skills,observability,fanout,acceptance-testing}.md`,
`frontend/README.md`. Canonical context: design docs in `docs/` + pi
de-risking `scratch/`.

**Phase 9a** adds defensive plumbing for the post-MVP fanout-join
feature (`docs/archive/parallel-iters-fanout-join.md`,
`docs/archive/2026-05-21-fanout-join-9a.md`): a new `awaiting_children`
run status (NOT terminal — can transition back to `running` once
children settle in 9c), a reserved `child_runs_resolved` event kind,
and a depth-first cascade-cancel helper threaded through
`_recover_orphans` so a parent in `awaiting_children` at startup is
finalised together with its descendants (ADR-34: recovering an
in-flight fanout across a restart is a V1 non-goal; the helper is
reused by 9d for runtime cancel-cascade). The `_TERMINAL` constants in
`api/events.py`, `frontend/src/stores/events.ts`, and
`frontend/src/views/RunDetailView.vue` already exclude
`awaiting_children` correctly — the change is comments + a regression
test that an event appended after the SSE generator subscribes still
reaches the consumer
(`tests/api/test_sse.py::test_sse_treats_awaiting_children_as_live`).
`StatusBadge.vue` gains an amber-tinted variant. No production code
path creates an `awaiting_children` row yet — 9b/9c land the dispatch
+ join.

**Phase 9b** lands the fanout dispatch
(`docs/archive/2026-05-21-fanout-join-9b.md`): a new closing sentinel
verb `[[engteam:fanout]]` paired with a `[[engteam:fanout-start]] …
[[engteam:fanout-end]]` JSON marker block (spec §5.4), parsed by
`extract_fanout_payload` into a Pydantic `FanoutPayload`
(`src/relay/harness/signaling/fanout.py`), surfaced as a terminal
`fanout` signal by `detect_in_text`, threaded through the loop as a
new `LoopResult("awaiting_children", fanout_payload=…)` return.
`RelayCore._apply_result` routes that status to `_dispatch_children`,
which spawns N child runs as separate `runs` rows joined via
`parent_run_id` (Shape B, NOT iters of the parent) whose worktrees
branch off the **parent's worktree HEAD** (not the project default
branch) via the new `provision_workspace(..., parent_worktree_path=…)`
param. Concurrency capped by an
`asyncio.Semaphore(settings.max_fanout_concurrent)` in the supervisor
(ADR-35, Option A: every child row exists in the DB from dispatch and
is swept by orphan-recovery on restart — no new persistent
intermediate state). Recursion bounded by `max_fanout_depth` (default
2, hard cap 4 via `RELAY_MAX_FANOUT_DEPTH`); `_fanout_depth` walks the
`parent_run_id` chain at dispatch and a depth-exceeded child finalises
as `failed`. The parent stays in `awaiting_children` with no
`run_ended` event — 9c lands the join. `join_prompt` flows from 9b to
9c via `iters.signal_args["payload"]["join_prompt"]` on the closing
fanout iter (OCQ-1 status-quo).

**Phase 9c** closes the fanout-join loop
(`docs/archive/2026-05-21-fanout-join-9c.md`, ADR-36): a new
`RelayCore._maybe_resume_parent` watcher fired from each child's
`_run` finally block — when all siblings of an `awaiting_children`
parent reach a terminal status, the watcher emits one
`subagent_return` per child + one `child_runs_resolved`, transitions
the parent `awaiting_children → running`, and re-enqueues with a
synthesizer `RunContext` whose body is `compose_join_prompt(
join_prompt, child_results)`. The trailer is a YAML-ish
`RELAY_CHILD_RESULTS:` block listing each child's
`id`/`role`/`status`/`summary`/`branch`/`worktree_path` (in the
**body, NOT preamble** — ADR-14's `RELAY_RUN_DIR`/`RELAY_PHASE`
invariant unchanged). The synthesizer iter runs on the parent's
existing worktree (no new worktree for the join); recursive fanout
from the synthesizer is permitted up to `max_fanout_depth`.
Partial-failure semantics: the synthesizer always runs once all
children settle regardless of mix; the orchestrator never auto-fails
the parent on a child's failure — the agent decides via the trailer.
**Two structural fixes are load-bearing**: (a) `_run`'s finally calls
the watcher *before* `state.settled.set()` so a caller awaiting a
child's `wait_for_run()` then immediately the parent's cannot race the
watcher's swap of `self._runs[parent_id]` and observe the stale
`awaiting_children` result; (b) `_dispatch_children` creates all child
rows + their `subagent_dispatch` events in one pass and enqueues in a
second pass, so a fast harness cannot let child A finish before child
B's row exists. `_enqueue_lock` serialises the watcher so two children
settling near-simultaneously cannot both resume the parent.

**Phase 9d** wires the runtime cancel-cascade
(`docs/archive/2026-05-21-fanout-join-9d.md`, ADR-37): `cancel_run` on
an `awaiting_children` parent now acquires `_enqueue_lock`, flips the
parent to `cancelled` **first** (parent-first is load-bearing — the
9c join watcher also acquires the same lock and re-reads the parent
under it; a child terminal landing between a descendants-first cascade
and the parent flip would let the watcher resume the parent
mid-cancel), then calls `_cascade_cancel_runtime` which walks
descendants depth-first with a per-descendant strategy: an
**in-flight** descendant (in-memory `_RunState` exists and not
settled) gets a fire-and-forget `cancel_event.set()` +
`session.cancel()` and lets its own `_run.CancelledError` branch write
the `run_ended` (pre-writing the DB here would double-emit); a
**DB-only** descendant (queued-but-not-started, or `_RunState` lost)
gets `set_run_status(cancelled, ended=True)` + `run_ended` written
directly. The 9a `_cascade_cancel_descendants` stays as the
startup-only sibling. `_run` gains a cancelled-before-start guard so a
queued descendant DB-flipped by the cascade exits immediately on
supervisor pickup with no stray `iter_started` event; the guard
bypasses `_maybe_resume_parent` because parent-first ordering already
moved the parent out of `awaiting_children`. The normal in-flight
cancel path (set `cancel_event` + cancel session) stays outside the
lock; the ADR-31 orphan safety net is preserved. No
schema/event-kind/sentinel change; `POST /api/runs/{id}/cancel` + MCP
`relay__cancel_run` inherit the behaviour with no signature change.

**Phase 9e** lands the dashboard "Children" pane
(`docs/archive/2026-05-21-fanout-join-9e.md`). (1) **Children pane** in
`RunDetailView` — rendered only for parent runs (`parent_run_id ==
null` + at least one child); each row shows `status · short-id · role
· branch · summary` fetched from the new `GET
/api/runs/{id}/children` via a `useRunChildrenQuery` Pinia Colada
hook; revalidates whenever the events store receives
`subagent_dispatch`, `subagent_return`, or `child_runs_resolved` (all
three in `INVALIDATING_KINDS`) via a new
`['runs','children',runId]` invalidation key. (2) **Parent chip** in
the run-detail header — `ParentRunChip.vue` links back to the parent
whenever `parent_run_id != null`. (3) **Cancel button cascade copy**
— predicate expands to `status ∈ {running, awaiting_children}`; for a
parent in `awaiting_children` the label reads "Cancel run and N
children". (4) **"Show child runs" toggle** in the Project view —
child runs hidden by default (`GET /api/runs` defaults
`include_children=false`); toggle re-fetches with
`include_children=true`. Backend additions:
`RelayCore.list_children(run_id)` (ordered by `created_at`);
`RelayCore.list_runs(..., include_children: bool = False)`; `GET
/api/runs/{id}/children` REST route; `?include_children=` query param.
MCP `relay__list_runs` passes `include_children=True` so MCP callers
see the full tree.

**Phase 9f** closes the fanout-join arc with OTel cross-run span
parenting (`docs/archive/2026-05-22-fanout-join-9f.md`, ADR-38): child
runs' `relay.run` spans, and a parent's synth-phase `relay.run` span,
now hang under the iter where the parent fanned out — so the full
fanout-join cycle renders as **one connected tree** in Langfuse
instead of three disconnected `relay.run` roots. Mechanism: a new
opaque `IterSpanContext = Any` carrier (defined + re-exported from
`relay.observability`) plus a new `Instrumentation.run_span(*,
parent_iter_ctx=None)` kwarg — when set, the run span is started
under that iter's OTel context via `trace.set_span_in_context(...)`.
The plumbing chain: `loop` captures the live `iter_span.context`
inside the **closing fanout iter only** (via the existing
`_drive_iter` defaulted-span seam) → returned on
`LoopResult.fanout_parent_ctx` → `_apply_result` threads it to
`_dispatch_children`, which stashes the context on each child's
`_RunState.parent_iter_ctx` in the **first pass** of the 9c
create-all-then-enqueue split → `_run` passes
`parent_iter_ctx=state.parent_iter_ctx` into `otel.run_span(...)`.
Synth-phase wiring (recursive-fanout-safe):
`_maybe_resume_parent` preserves `old_state.result.fanout_parent_ctx`
across the `_RunState` overwrite at the parent's resume and stashes it
on the fresh state's `parent_iter_ctx`, so the synth-phase
`relay.run` parents under the iter where **THIS** run fanned out
(sibling of THAT level's children, NOT a sibling of THIS level's
pre-fanout iters). NOOP invariant preserved: `_NoopIterSpan.context =
None` is a class attribute that constructs no provider/exporter and
makes no network call; NOOP `run_span` accepts and ignores the new
kwarg. Cross-run trace context lives in-memory only — a restart loses
the linkage (acceptable per ADR-34; the 9a cascade-cancel helper
finalises the tree).

**Phase 9g** closes the latent ADR-10 invariant gap that
`SessionEnded` was captured by the Option-D harness lookahead and
surfaced to OTel (ADR-29) but never written to the events table. A
new event kind `harness_session_ended` is appended in
`loop._finish_iter` on every iter-close path (signal / cancelled /
timeout / no-signal / crash) **BEFORE** the paired `iter_ended` event,
with payload `{stop_reason, messages, summary}` — `messages` verbatim
per ADR-18, `summary` populated only on the `done` close path (the
current sentinel grammar carries no summary in `signal.args` for
`done`, so this is `null` in practice today; plumbing is in place for
a future grammar change). The OTel mirror still reads from
`out.messages` in-memory in the loop's finally block (ADR-29
lookahead preserved); the new event row is for replay consumers (SSE,
audit, future analytics). Frontend gains a small `UsageRow.vue`
rendering stop_reason + summed token counts inline in the timeline.
New ADR-39 records the contract change. No schema, harness, or MCP
change.

**Cross-cutting traps from the 9f live-acceptance bug-fix sweep
(2026-05-23, `journal/260523-9f-bug-fixes.md`).** Three regressions
restored existing contracts and exposed lasting invariants:

- **`frontend/src/api/sse.ts::KNOWN_EVENT_TYPES` and
  `frontend/src/stores/events.ts::INVALIDATING_KINDS` are a dual-list
  contract.** The browser EventSource only fires listeners for
  explicitly registered named events — a kind in `INVALIDATING_KINDS`
  but missing from `KNOWN_EVENT_TYPES` is silently dropped client-side
  (refresh works because it skips SSE and hits REST replay). When
  adding a new event kind, update both lists.
- **Workspaces resolve under `project_root/.relay`, NOT
  `settings.data_dir`.** `settings.data_dir` holds the multi-tenant
  `relay.db` only; everything per-project is per-project.
  `RelayCore.get_run_artifacts_dir(run_id)` is the single resolver
  for the artifact read paths (`api/artifacts.py`, MCP
  `relay__read_artifact`, core's resume / preview `run_dir`
  derivations).
- **`UsageRow.vue` and `observability/otel.py::_aggregate_usage` read
  pi-flavoured token keys** (`input` / `output` / `cacheRead` /
  `cacheWrite` / `totalTokens` + `cost.total`) per ADR-18 — **NOT**
  Anthropic-API names (`input_tokens` / `cache_read_input_tokens` /
  etc). Pi `SessionEnded.messages[].usage` is the source of truth.
- **SSE wire shape vs store payload (ADR-45/46, 2026-05-25
  regression).** The SSE `data:` body for a persisted event is the
  full envelope `{seq, kind, payload, ts, run_id, iter_id}` that
  `api/events.py:_event_payload` builds. The REST replay path
  correctly unwraps `r.payload`; the live store path must do the
  same (`stores/events.ts:onSseEvent` — pluck `envelope.payload`,
  not the envelope itself). The 2026-05-25 bug stored the envelope
  as `payload`, so every renderer reading `event.payload.<field>`
  saw `undefined` and tool cards rendered with empty
  name/args/result until a refresh hit REST replay. Two ephemeral
  frame kinds — `heartbeat` (ADR-45) and `assistant_delta` (ADR-46)
  — deliberately ship the inner shape directly with NO envelope and
  NO `id:` line, so the browser keeps its Last-Event-ID cursor and
  the store special-cases them BEFORE the envelope-unwrap path.

**Post-MVP live-stream UX (2026-05-25, ADR-45 + ADR-46).** Two thin
additions to the SSE layer make a quiet "pi is thinking" phase
readable without changing the events table.
- **ADR-45 Plan A — heartbeat.** Idle live streams emit a named
  `heartbeat` SSE frame at the `_KEEPALIVE_S` cadence (dropped from
  15s → 5s) carrying `{run_id, server_ts, last_event_ts}`. No `id:`
  line (cursor unchanged). The frontend store routes it to
  `lastHeartbeat` (NOT the events list, lastSeq, or invalidations).
  `RunHealthBadge.vue` consumes it next to the `StatusBadge` to
  render a live-ticking "● live · last activity Xs ago" indicator
  with `live` / `slow` (>15s) / `stalled` (>60s) transitions;
  rendered nothing for terminal status. Dual-list rule satisfied:
  `heartbeat` is in `KNOWN_EVENT_TYPES` (listener fires) and
  intentionally absent from `INVALIDATING_KINDS` (no cache effect).
- **ADR-46 Plan B — streaming deltas.** New harness event
  `AssistantTextDelta(text, kind, turn_seq, delta_seq)` yielded
  inline by `_PiEventMapper` for every `text_delta` /
  `thinking_delta` — additive to the existing `AssistantText` flush
  at `turn_end` (ADR-18 concatenation invariant preserved).
  `EventStore.store_harness_event` routes deltas to a new
  `Broadcaster.publish_ephemeral(run_id, kind, data)` and does NOT
  append. `sse_event_stream`'s drain loop discriminates on the
  `_ephemeral` marker before reading `seq` and renders id-less
  named-event frames. The frontend `pendingMap` in the events store
  accumulates deltas per `${iterId}:${turnSeq}:${kind}`; the entry
  is dropped when the canonical `assistant_text` arrives or when
  `iter_ended` fires. `TimelinePane.vue` renders pending pseudo-rows
  below the canonical timeline, hidden under an active iter filter.
  Replay sees no pending rows but the canonical `AssistantText` is
  in the events list — identical final state. Signal detection
  still happens only on the turn-end `AssistantText`, never on a
  delta (ADR-18 anti-mention preserved).

**Phase 14a** (2026-05-22, `docs/archive/pause-for-review.md`,
`docs/archive/2026-05-22-pause-for-review-14a.md`) opens the
**pause-for-review** arc by landing the backend half: a new
`PauseReviewError` exception + `RelayCore.write_artifact(run_id,
rel_path, content, *, editor)` method (paired with a module-private
`_normalise_review_path` helper) and a new
`PUT /api/runs/:id/artifacts/{path:path}` REST route that is the
**single write entry point** on the run artifacts dir. The endpoint
appends a new event kind `artifact_edited` (iter-scoped to the paused
iter, payload `{path, size_before, size_after, sha256_before,
sha256_after, editor}` — content stays on disk per ADR-25, hashes
give integrity per ADR-40 §B1) and is **strictly coupled** to
`run.status == 'paused'` AND a matching `signal_args["review_path"]`
on the latest paused iter (OQ-1 strict coupling: writes only during a
declared review moment, not a general write API). The route reuses
the ADR-25 `resolve_within_sandbox` resolver verbatim
(traversal/absolute/NUL/symlink-escape → 400) and maps
`PauseReviewError.code` to HTTP — `unknown_run → 404`,
`too_large → 413`, `binary → 415`, every other code → 409
(`not_paused`, `no_review_path`, `path_mismatch`,
`missing_parent_dir`). Writes are atomic via tempfile-in-same-dir +
`Path.replace`. 14a does NOT auto-create intermediate dirs — a nested
`review_path` like `discussions/notes.md` is only accepted if
`discussions/` already exists. The 14a release ships an endpoint that
returns 409 for every real-world caller until 14b lands (no
production path writes `review_path` into `signal_args` yet); tests
synthesise the post-14b world by seeding directly via the
sessionmaker. The `core.py → api/files.py` imports for sandbox
helpers (`BINARY_SNIFF_BYTES`, `MAX_FILE_BYTES`,
`resolve_within_sandbox`) are **lazy inside `write_artifact`** to
avoid a circular `api/files → api/deps → core`. New ADR-40 records
A1 (sentinel-attribute opt-in over implicit dashboard inference), B1
(in-place write + hash-bearing event over versioned snapshots /
event-payload content), OQ-1 strict coupling, and the
`PauseReviewError`-code-driven HTTP mapping.

**Phase 14b** (2026-05-22,
`docs/archive/2026-05-22-pause-for-review-14b.md`) teaches the sentinel
parser the optional `review_path` attribute on
`[[engteam:pause-for-input ...]]`, lighting up 14a's write endpoint
as a working write path. New `extract_pause_review_path(text)` in
`src/relay/harness/signaling/sentinels.py` mirrors
`extract_pause_id` / `extract_pause_question` (line-anchored
`_PAUSE_RE.match` → `review_path="..."` regex with `\"`-unescape
support) and delegates validation to a module-private
`_validate_review_path`: empty / NUL-byte / absolute (leading `/` or
`PurePosixPath.is_absolute()`) / any `..` path component raise
`MarkerError` with a focused multi-line repair recipe
(`_REVIEW_PATH_REPAIR`). `detect_in_text`'s pause branch conditionally
adds `"review_path"` to `args` — **the key must be ABSENT from
`signal_args` when the attribute is absent** (load-bearing for 14a's
`no_review_path` 409 branch; a present-but-`None` key falsely
satisfies `if "review_path" in signal_args`). The parser performs
syntactic validation only; the 14a `resolve_within_sandbox` resolver
remains the runtime enforcement.
`skills/engineering-team/pi/references/sentinels.md` gains a
"Reviewable pauses (`review_path`)" sub-section.

**Phase 14c** (2026-05-23,
`docs/archive/2026-05-22-pause-for-review-14c.md`) lights up the
operator-facing half: when the paused iter's `signal_args.review_path`
is present (14b), `PauseAnswerForm.vue` switches into a richer mode
— a top review pane above the existing question/answer block fetches
the named artifact via `useArtifactContentQuery`, renders a
`<textarea>` (left) + `MarkdownRender` lazy markdown/shiki/mermaid
preview (right), and exposes a **Save** button wired through the new
`useArtifactWriteMutation` (a raw `fetch()`-backed Pinia Colada
mutation against the 14a `PUT` endpoint — raw fetch because the
hand-rolled backend route declares no Pydantic body model so the
generated OpenAPI op carries `requestBody?: never` and openapi-fetch
refuses a body field). The Resume button is disabled ONLY while a
Save is in flight; the answer textarea is unaffected. **OQ-3 missing
file** lands as a "Create at this path" banner + Save relabelled
"Create"; **OQ-7 binary** lands as a "not editable inline" message +
download link. 4xx errors (404/409/413/415/400) surface inline via an
`ApiError` mapper. Both `KNOWN_EVENT_TYPES` (`sse.ts`) and
`INVALIDATING_KINDS` (`stores/events.ts`) gain `'artifact_edited'`
(dual-list contract); the store's coalesced invalidation flush
broadens to add `['artifacts', runId]`. `TimelinePane.vue` renders
each `artifact_edited` as `✎ path · sha-before… → sha-after… ·
editor` (short-sha = first 4 chars + ellipsis; `null` `sha256_before`
for create → `∅`). `RunDetailView.vue` computes `pauseReviewPath`
(walks iters newest-first) and passes to `PauseAnswerForm` — null
when the paused iter didn't carry `review_path` makes the form
byte-for-byte the pre-14c minimal contract. `frontend/tests/setup.ts`
ships a narrow duck-typed `unhandledRejection` listener that
swallows only `{ name: 'ApiError', status: <number> }` rejections
(Pinia Colada surfaces a query's rejected promise as an unhandled
rejection before the SFC's lazy `loadError` computed first evaluates;
the listener keeps the 404 test path green while letting any
non-ApiError rejection still fail the gate).

**Phase 14d** (2026-05-23,
`docs/archive/2026-05-22-pause-for-review-14d.md`,
`skills/engineering-team/pi/phases/phase-2-planning.md`) activates
pause-for-review for the **primary caller**, closing the 14a → 14d
arc. One template change: Phase-2 Step-4 closing sentinel now emits
`review_path="improvement-plan.md"` on the same line as the existing
`id`/`question` attributes — **single-line is load-bearing** because
the parser is line-anchored (`sentinels.py:_PAUSE_RE.match(line)` +
`_REVIEW_PATH_RE.search(line)`); attribute formatting that wraps
across lines silently drops the attribute. Step-5 handoff template is
byte-identical (handoffs don't take `review_path` — unattended path;
no human review moment). The load-bearing **"Re-read it in full —
the user may have edited it"** instruction inside the
`prompt-start`/`prompt-end` body is preserved verbatim — that's the
ADR-20 mechanism by which the resumed iter picks up the operator's
edits (fresh context per iter means the agent only sees the edit
because it re-reads from disk). 14d is SKILL-TEMPLATE + JOURNAL ONLY
— no backend, frontend, MCP, sentinel-parser, ADR, or test file
touched. Live `PI_INTEGRATION=1` engteam end-to-end is
journal-attested per ADR-30.

**Phase 14e** (2026-05-23,
`docs/archive/2026-05-23-pause-for-review-14e.md`) lands the
audit-polish bundle with no contract change. `PauseAnswerForm.vue`'s
right pane gains a `[ Preview | Diff ]` toggle (Diff disabled while
clean; renders dirty-vs-loaded-baseline via the existing lazy
`DiffRender.vue` entry which dynamic-imports `diff2html`; baseline
updates on Save and a return-to-clean snaps back to Preview).
`TimelinePane.vue`'s `artifact_edited` rows become click-targets that
open the artifacts pane at the file's **current** on-disk content —
honestly framed as navigation, NOT a historical diff (ADR-40 §B1
deliberately does not preserve before-content; a historical diff is
unreconstructable). The handler mutates the shared `run:<runId>`
file-browser Pinia store's `selectedPath` and scrolls `ArtifactsPane`
into view; row layout is byte-identical to 14c.
`relay.pause.artifacts_edited_count` lands as a scalar attribute on
the **resumed iter's** `relay.iter` span: `RunContext` gains
`paused_predecessor_iter_id` (set by `resume_run`), `run_loop` issues
one `SELECT COUNT(*)` against `events.iter_id == :paused_iter_id AND
kind == 'artifact_edited'` before the loop body (single int, low
cardinality), and passes via `pause_artifacts_edited_count` on
`RunSpan.iter_span` — the OTel module never queries the DB (same
shape as `set_usage`). NOOP `Instrumentation` accepts and ignores the
kwarg. And the deferred 9e fanout-docs follow-up closes:
`skills/engineering-team/pi/phases/phase-2-planning.md` gains a
blockquote cross-link to `../references/fanout.md`.

**Phase 14f** (2026-05-23,
`docs/archive/2026-05-23-pause-for-review-14f.md`, ADR-41) extends
pause-for-review from a single `review_path` to a list via the
repeated-attribute grammar (`review_path="a.md"
review_path="b.md"` on the same `pause-for-input` line). The
line-anchored `_PAUSE_RE` is unchanged; collection moves from
`re.search` (first match) to `re.finditer` (all matches) in a new
`extract_pause_review_paths(text) -> list[str]`. Per-value validation
reuses the 14b `_validate_review_path` unchanged (empty / NUL /
absolute / traversal → `MarkerError` naming the offending value).
**Storage shape changes**: `signal_args.review_paths: list[str]`
replaces the scalar `signal_args.review_path` key — `detect_in_text`
writes ONLY the plural key on new emits. Readers (write endpoint,
dashboard) fall back to the scalar key during the migration window so
iters paused under 14a–14d survive a process restart. The 14b shim
`extract_pause_review_path` stays as a one-liner returning the first
value or `None` until no caller remains. `RelayCore.write_artifact`'s
coupling check generalises from exact-match to **set-membership**
against the normalised `signal_args.review_paths`; `PauseReviewError`
codes and HTTP mapping are unchanged. `PauseAnswerForm.vue` renders a
tab per path when N > 1 (per-tab dirty state, per-tab `*` marker, one
Save in flight at a time targeting the active tab; **Resume disabled
only while that Save is in flight, NOT while a non-active tab is
dirty** — an abandoned tab must not strand the operator). N == 1 (or
absent) is byte-identical to 14c (no tab bar). Component prop renames
`reviewPath: string | null` → `reviewPaths: string[]`;
`RunDetailView.vue`'s `pauseReviewPaths` reads the plural key first
and falls back to the legacy scalar. Engteam Phase-2 template **not**
modified — still emits exactly one `review_path`; plural is opt-in
for future skills. ADR-41 records the storage shape change.

**Chat-mode arc (W1–W6, 2026-05-29 → 2026-05-30, ADR-49 + ADR-50,
`docs/archive/2026-05-30-chat-mode-arc.md`)** adds a conversational webui for pi
alongside the chained-iter task flow. **Two run modes coexist with
opposite invariants — chat mode does NOT relax ADR-20 in task mode.**
The plan landed in six units across the runs table, the orchestrator
loop, the API surface, the dashboard, and an ADR pair.
- **W1** — schema + run creation. One new column on `runs`: `mode
  TEXT NOT NULL DEFAULT 'task'`, constrained at the Python boundary
  to `Literal["task", "chat"]`. `RunCreate` widens to accept `mode`
  (default `'task'`); `RunDetail` surfaces it. `RelayCore.start_run`
  branches: chat mode direct-writes `run_started` + a synthetic
  `pause_requested` event and settles **without spawning a first
  iter** — the first `resume_run` answer becomes iter 1's body.
  Worktree provisioning runs unchanged (pi may legitimately read or
  write project files during a chat). No new event kinds.
- **W2** — orchestrator loop branch + chat resume path. `resume_run`
  branches on `run.mode`: chat-mode resumes thread the prior iter's
  `pi_session_id` as pi's `--session` argument so each iter inherits
  the model's prior conversation memory; the operator's `answer` is
  the **verbatim** next-iter body — no preamble, no compressed
  handoff. `run_loop` branches on `ctx.mode`: chat-mode iters skip
  the `RELAY_*` preamble (chat has no `RELAY_RUN_DIR` /
  `RELAY_PHASE`), skip sentinel enforcement (no `done` / `handoff` /
  `pause-for-input` parsing — pi's `agent_end` is the natural turn
  boundary), and on `session_end` write a synthetic `pause_requested`
  so the run lands in `paused` waiting for the next message. Skill
  injection (ADR-44) is omitted from chat-mode spawns; pi's own
  auto-discovery of `<cwd>/.pi/skills/` and `~/.pi/agent/skills/` is
  preserved so project conventions still apply.
- **W3** — `closed` terminal status + close endpoint. New value in
  the `runs.status` enum (ADR-50), reachable only from `paused` or
  `running` via `POST /api/runs/{id}/close`; the close handler
  cancels any in-flight session first. Distinct from `cancelled`
  (user gave up on a task) and `done` (agent emitted terminating
  sentinel) — `StatusBadge.vue` renders dim-grey + dashed border
  (distinct from `cancelled`'s solid border). **FIVE `_TERMINAL`
  declarations must stay in sync** for any future status work:
  `src/relay/api/events.py`, `src/relay/core.py` (multiple
  cascade/safety-net tuples), `frontend/src/stores/events.ts`
  (`TERMINAL_STATUSES`), `frontend/src/views/RunDetailView.vue`
  (`TERMINAL`), `frontend/src/views/ChatView.vue` (`TERMINAL`).
  `awaiting_children` remains deliberately excluded (ADR-34: not
  terminal).
- **W4** — frontend ChatView + routing. New route `/chats/:id`;
  `ChatView.vue` is the conversational counterpart to
  `RunDetailView`. Composition: `ChatHeader.vue` (sticky bar with
  Close + Promote-to-task), `ChatTranscript.vue` (folded
  user/assistant turns), `ChatInput.vue` (composer + Send).
  **Transcript fold** — `pause_resolved.payload.answer` (non-empty)
  → ONE user turn; each `iter_started` … `iter_ended` block → ONE
  assistant turn from concatenated `assistant_text` with
  `payload.kind != 'thinking'` (chat surface hides reasoning,
  mirroring consumer chat products — the timeline view in
  `/runs/:id` keeps the thinking channel surface). Tool calls
  interleave inline as `ToolCallCard` `embedded` chips. Live tokens
  via the ADR-46 `assistant_delta` ephemeral stream — same pipeline
  TimelinePane uses. **Wrong-view guard pair**: `RunDetailView`'s
  setup watcher redirects `mode='chat'` runs opened via `/runs/:id`
  to `/chats/:id`; `ChatView`'s symmetric watcher does the reverse.
- **W5** — Project dashboard Chat button + Chats tab. Project view's
  runs pane gains a sibling **Chats** tab listing chat-mode runs
  separately; query filter `?mode=task` / `?mode=chat` on `GET
  /api/runs`. A **New chat** affordance creates a chat-mode run
  inline (no wizard — chat starts in `paused` with no iters, ready
  for the operator's first message).
- **W6** — Promote-to-task UI + ADRs + docs. The chat header's
  "Promote to task" button (stubbed in W4) is wired to real
  navigation. A new pure helper `frontend/src/lib/promotion.ts`
  `buildPromotionPrompt({events, projectName})` folds the same
  events the transcript renders (including the thinking-kind drop)
  into a markdown transcript bracketed by `--- Conversation ---` /
  `--- End conversation ---`. **Handoff travels through
  `sessionStorage` under `relay:promotion:<chatRunId>`, NOT the URL
  query string** — long transcripts can exceed browser URL length
  caps (~2KB-32KB depending on stack); the URL only carries a
  `?promoteFrom=<chatRunId>` marker. `NewRunWizard` reads + removes
  the entry on mount (one-shot — a refresh of the wizard URL won't
  re-populate). **Promotion is non-destructive** — `onPromote` does
  NOT close or cancel the chat; the operator may want to keep
  talking and promote again later. ADR-49 records the mode-split
  decision; ADR-50 records the `closed` terminal status.

**Cross-cutting trap from chat-mode (W6, 2026-05-30).** The
**five-list `_TERMINAL` sync rule** is the chat-mode shadow of the
9f dual-list contract — every status check across the backend +
frontend must agree on what's terminal. Any future status addition
must touch all five files (`api/events.py`, `core.py` cascade
tuples, `stores/events.ts::TERMINAL_STATUSES`,
`RunDetailView.vue::TERMINAL`, `ChatView.vue::TERMINAL`). Missing
one produces silent bugs: SSE generators keep streaming for runs
the dashboard considers ended, the events store keeps invalidating
queries past the run's end, or the chat view's lifecycle refetch
never converges. `awaiting_children` is non-terminal in all five
(ADR-34); `done` / `failed` / `cancelled` / `closed` are terminal
in all five.

**Resilient iter close arc (2026-06-04, ADR-53, `docs/archive/2026-06-04-resilient-iter-close.md`)** closes the clean+no-signal cliff exposed by run `20260604-174717-09d7` on `/Users/john/projects/horizons`, where pi emitted `unit-done` for W0.0 then introduced a WU0.1 proposal inline and ended its turn with conversational "OK to proceed?" text — no closing sentinel. The loop's `if signal is None:` branch widens into three sub-cases discriminated by `outcome.marker_headline`, `outcome.stop_reason`, and a one-shot `recovery_used` flag local to `run_loop`. **Sub-case (1)** (clean stop, no marker headline, recovery unused): close the iter with `exit_reason="agent_end_no_signal"`, widen `effective_max += 1` (the recovery shot is NOT a `max_iters` consumption), set `pending_recovery = True`, set `body = _RECOVERY_BODY` (a `RELAY_RECOVERY_NOTICE:` block listing the four terminal sentinels and instructing the agent to re-emit one), `continue`. The next iter's `iter_ended` carries `recovery_iter: true`. **Sub-case (2)** (same predicate, `recovery_used == True`): synthesise `signal_kind="pause"` + `signal_args={"id": f"autopause-{run_id}-{seq}", "question": …, "next_prompt": "", "review_paths": []}`, return `LoopResult("paused", reason="agent_end_no_signal_autopause", …)`. Mirrors the chat-mode synth-pause shape; the dashboard's `PauseAnswerForm` picks it up unchanged. **Sub-case (3)** (marker_headline set OR non-clean stop): existing `failed` behaviour stands — pi tried to emit a sentinel and got it wrong, or the harness crashed. Marker violations are real bugs we surface, not omissions we paper over. `pending_recovery` is the canonical recovery-iter identifier — NOT byte-equality on `body == _RECOVERY_BODY` — because handoff carry-forward writes agent-authored text into `body` and a literal collision (improbable but possible) would silently mis-tag a normal iter. Two parallel defences land alongside the loop change: every task-mode preamble now carries a `RELAY_SENTINEL_REMINDER:` line listing the four sentinels (pre-emptive nudge, `src/relay/orchestrator/preamble.py`); and `skills/engineering-team/pi/phases/phase-3-development.md` gains a "Closing sentinel is mandatory" rule explicitly forbidding "OK to proceed?" / "Awaiting your sign-off" text as a substitute for `pause-for-input`. A dashboard escape hatch handles legacy `failed` runs: `POST /api/runs/{id}/reopen` flips a `failed` run whose last iter's `exit_reason == "agent_end_no_signal"` back to `paused`, AND synthesises a paused iter row on the LAST iter (`signal_kind="pause"`, `signal_args={"id": f"reopen-{run_id}-{seq}", …}`) atomically in the same DB transaction that writes `status=paused` and clears `ended_at`. The iter-row synth is load-bearing: `resume_run`'s `latest_paused_iter` query matches on `signal_kind == "pause"`; without it the reopen-then-resume round-trip 409s and the feature is unreachable. The historical `iter.exit_reason` is NOT overwritten — audit truth survives. Frontend: `useReopenRunMutation` + "Reopen as paused" button on the failure-hint card in `RunRightPane.vue`, gated on `status === 'failed' AND last_iter.exit_reason === 'agent_end_no_signal'` (single-string compare — the `_autopause` suffix never reaches `iter.exit_reason`, so a second arm would be dead code) with inline error display on rejection. Two cross-cutting facts to remember: (a) `LoopResult.reason` now has the new value `"agent_end_no_signal_autopause"` distinct from `"agent_end_no_signal"`, but the iter row's `exit_reason` column NEVER carries the `_autopause` suffix — that suffix exists only on `LoopResult.reason` (telemetry / orchestration), never on the iter row (audit); (b) chat-mode's pre-existing auto-pause uses `iter_span.set_exit("signal")` while WU4's task-mode auto-pause uses `iter_span.set_exit("agent_end_no_signal")` — intentional divergence: chat has no sentinel grammar so the distinction is moot, task-mode genuinely had no sentinel so the synth pause is a relay-side decision recorded honestly.

## What relay is

A Python service that orchestrates *chained agent sessions* against a
swappable headless harness (pi for MVP). It breaks a large plan into
work units, runs each in a **fresh** harness session, and carries state
forward via a deliberately compressed handoff. A structured SQLite event
store is the source of truth; a Vue dashboard tails it live and replays
history from it. This codebase is a clean-break rewrite of an earlier
bash + Flask prototype (archived at `~/projects/archive/relay-v1/`);
there is no backward compatibility.

## Document authority — read these before designing or coding

The four docs in `docs/` are the canonical source. They have a
hierarchy; when they disagree, this is the precedence:

- **`docs/spec.md`** — canonical design (architecture, data model,
  harness layer, signaling, REST/MCP surface, dashboard, observability).
  Reflects current consensus and is updated as design evolves. **When
  building, this is the contract.**
- **`docs/decisions.md`** — ADR log with rationale and rejected
  alternatives. **Append-only.** Never edit or delete an existing ADR;
  superseded ADRs get a `**Status:** superseded by ADR-NN` header and a
  new ADR is added at the bottom. If you make a decision that changes
  the spec, record it as an ADR here *and* update `spec.md`.
- **`docs/motivation.md`** — why v1 must be replaced; goals, non-goals,
  hard constraints, parked risks. Consult before proposing scope changes.
- **`docs/plan.md`** — the phased (0–8) MVP build sequence with
  per-phase deliverables and verification criteria. Follow it; it is the
  execution order.

`spec.md` §13 tracks open questions (OQ-1…OQ-6); resolve them via the
de-risking evidence in `scratch/`, not by guessing.

## De-risking evidence is ground truth

`scratch/pi_derisk_workdir/findings.md` records empirically confirmed pi
behavior (run 2026-05-19). Treat it as authoritative over assumptions:

- pi authenticates via `PI_AGENT_SDK=1` (Max-subscription path) with no
  further config. Always set this env var when spawning pi.
- **No 30-second tool timeout** (a 70s Bash ran to completion) — this is
  the load-bearing finding behind choosing pi over the Claude Agent SDK.
- 11 confirmed pi event types; the `pi event → relay HarnessEvent`
  mapping in findings.md (and `spec.md` §4.2) is verified, not
  speculative. Captured event fixtures live alongside it as `*.jsonl` —
  use them for harness unit tests (Phase 1).
- pi has **no subagents at the protocol level** — relay manages
  subagents at the orchestrator layer (ADR-06).
- Confirmed pi version is **v0.78.0** (per ADR-52 — built from the
  `johnmathews/pi:relay-bridge-v1` tag; was v0.74.0 under the
  npm-install delivery ADR-51 documented). Pin to it (`.tool-versions`
  + `Settings.pi_expected_version`). Note `motivation.md` mentions a
  newer release exists — pinning below current is intentional.

## Load-bearing design invariants

These are easy to violate by accident and must survive any
implementation:

- **Fresh context per iter.** `last_session_id` is intentionally always
  `None` between iters (`spec.md` §6). Pi's session resume preserves
  context; relay's entire value proposition is the *opposite* — fresh
  contexts with a compressed handoff. Resume is reserved for crash
  recovery only.
- **All writes flow through `RelayCore`.** REST routes, MCP tools, and
  the orchestrator share one in-process `RelayCore` instance and mutate
  state only through it; route handlers never touch the DB directly
  (ADR-07, ADR-15). This replaces v1's "dashboard never writes" rule.
- **Event store is the single source of truth.** Every observable
  action is an append-only `events` row (no in-place updates; status
  transitions are new events). SSE tails it; replay re-streams it; OTel
  mirrors it (ADR-10).
- **Harness isolation.** Only the `harness/` package knows about pi.
  The orchestrator sees normalized `HarnessEvent` types only (ADR-04).
- **Single-user, localhost MVP.** `user_id` FKs exist from day one but
  default to a sentinel; do not build multi-user/auth/RBAC in MVP
  (ADR-12). Many capabilities are deliberate non-goals — check
  `motivation.md` before adding scope.

## Toolchain (established in Phase 0; keep this section accurate)

- Python 3.13, dependency management via **`uv`** (not pip/poetry).
  `uv sync` to install, `uv run <cmd>` to execute. `uv.lock` is committed.
- Tests: **`pytest`** (`uv run pytest`); lint **`ruff`**
  (`uv run ruff check .`; `scratch/` is excluded — it is de-risking
  evidence, not source); types **`mypy`** strict (`uv run mypy`; package
  carries a `py.typed` marker).
- Test async convention (ADR-24): `pytest-asyncio` runs in
  `asyncio_mode = "auto"`. `tests/api/` uses bare `async def test_*` +
  `httpx.AsyncClient` over `ASGITransport`, entering the real lifespan
  via `app.router.lifespan_context`; `tests/orchestrator/` and
  `tests/harness/` keep the explicit `asyncio.run()` wrapper pattern
  (sync `def test_*`) — both coexist under the one `auto` config.
  `create_app(settings, *, harness=)` is a scripted-harness injection
  seam (mirrors the `settings` seam) so API tests never spawn pi.
  `openapi-spec-validator` (dev-dep) asserts `/openapi.json` is valid
  OpenAPI v3.
- Schema management is hand-rolled `create_all` for the MVP (ADR-17);
  `src/relay/db/migrations/` is a placeholder for future numbered
  upgrade scripts. Alembic is deferred.
- Two DB engines, both behind `relay.db` (ADR-21): a **sync** engine
  for `create_all` schema bootstrap only; an **async** `aiosqlite`
  engine (deps `aiosqlite`, `sqlalchemy[asyncio]` → `greenlet`) for all
  orchestrator I/O. Nothing above `relay.db` constructs an engine.
- Backend: FastAPI + Pydantic v2 + Uvicorn; SQLite via SQLAlchemy.
- MCP server (Phase 5, ADR-27): the **bundled** official SDK
  (`mcp.server.fastmcp`, dep pinned `mcp>=1.27.1,<2` — the `<2` cap is
  load-bearing, v2 rearchitects the transport), built with
  `streamable_http_path="/"` and mounted at `/mcp` in the app lifespan,
  which wraps its body in `async with mcp.session_manager.run():` (a
  mounted sub-app's ASGI lifespan is not auto-run — the #1367 footgun).
  Tools are thin `RelayCore` adapters reusing `api/schemas.py`. Tests:
  `tests/mcp/` (`test_mcp_tools.py` in-process via `FastMCP.call_tool`;
  `test_mcp_mount.py` end-to-end through the real lifespan). Ops ref:
  `docs/mcp.md`.
- OTel mirror (Phase 7, ADR-29): deps `opentelemetry-api`,
  `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, all
  pinned `>=1.27,<2` (the `<2` is *precautionary*, not load-bearing —
  OTel 2.0 doesn't exist yet; recorded honestly as such). Deliberately
  **no** `opentelemetry-semantic-conventions` dep (its GenAI module is
  unstable) — `gen_ai.*` keys are stable string literals. The mirror
  is an injected `Instrumentation` (the harness-style seam: `RelayCore(
  …, otel=)`); `none` → a literal no-op that constructs no provider/
  exporter and makes no network call; `langfuse` → a **self-owned,
  non-global** `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter)`
  to `{RELAY_LANGFUSE_HOST}/api/public/otel/v1/traces` with HTTP Basic
  `base64(public:secret)`. Span emission is threaded into the loop by
  defaulted parameter (run span in `core._run`, iter/tool spans in
  `loop`/`_drive_iter`) — additive, no control-flow change. Usage on
  the terminal-sentinel path relies on the Option-D one-event
  `AssistantText` lookahead in `harness/pi.py` `PiSession.events()`
  (harness-only, ADR-04; order-preserving; no event-store change).
  Tests: `tests/observability/test_otel_export.py` (span structure via
  `InMemorySpanExporter`, no network) +
  `tests/harness/test_pi_session_lookahead.py` (Option D, offline fake
  proc). Ops ref: `docs/observability.md`.
- Frontend (`frontend/`, Phase 4): Vue 3 + vue-router **v5** + Pinia +
  Pinia Colada + Vite, TypeScript strict. Typed API client generated
  by `openapi-typescript` 7 + `openapi-fetch` off the running backend's
  `/openapi.json` (`npm run gen:api`; backend must be up). Render
  pipeline: markdown-it (+footnote/task-list, `html:false`), shiki
  (`createHighlighterCore` + JS regex engine + lazily-imported
  grammars — never the convenience bundle), mermaid (dynamic
  `import()` only), diff2html. Gate: `npm run check` = `eslint
  --max-warnings 0` + `vue-tsc` + `vitest` (jsdom, v8 coverage —
  vitest 4 has no `coverage.all` toggle, scope via `coverage.include`).
  Vite dev-proxies `/api` → `:7800` with a long `proxyTimeout` and
  no SSE buffering. Rationale + the five toolchain mandates: **ADR-26**;
  `frontend/README.md` has the operational notes. The full gate is
  Python (`ruff`/`mypy`/`pytest`) **and** the frontend `npm run check`.
- Console script: `relay`. Implemented today: `relay serve`,
  `relay --version` (Phase 0). The Phase-6 `relay install-skill`
  subcommand was retired 2026-05-25 (ADR-44) — relay now injects the
  bundled engineering-team skill into pi via `pi --skill
  <bundled-path>` on every spawn (one `--skill` pair per
  `Settings.pi_skill_paths` entry; default = `bundled_skill_dir()`
  from `harness/skills.py`; env override `RELAY_PI_SKILLS`
  colon-separated; empty string opts out so pi only sees its own
  auto-discovered skills under `<cwd>/.pi/skills/` and
  `~/.pi/agent/skills/`). Skill source lives at
  `skills/engineering-team/<harness>/` (variant directory, default
  `pi`); the variant model is documented in ADR-33, the delivery
  switch in ADR-44, operational notes in `docs/skills.md`. `relay
  start` / `status` / `cancel` arrive in later phases. Default bind
  `127.0.0.1:7800`.
- Pi integration tests are gated behind `PI_INTEGRATION=1`; harness
  unit tests run offline against the captured `scratch/*.jsonl` fixtures.
  Orchestrator tests live under `tests/orchestrator/` and drive the loop
  against a scripted `Harness` double (no pi). Tests stay under
  `tests/` (`testpaths=["tests"]`), not the per-package `tests/` dirs
  plan.md sketches.
- Packaging (Phase 8, ADR-30; pi bundled per ADR-51): a multi-stage
  `Dockerfile` (Node stage builds `frontend/dist/`; a second Node
  stage builds pi from `johnmathews/pi:relay-bridge-v1` (ADR-52 —
  npm-published pi strips the agent-SDK bridge); the resulting
  image carries pi 0.78.0 with the bridge intact;
  `ARG PI_REF=relay-bridge-v1` is the Dockerfile pin; `.tool-versions`
  agrees; `python:3.13-slim`
  runtime copies in the Node binary + pi's node_modules and runs the
  `uv`-synced backend from `/app/.venv/bin/relay` — not `uv run`, no
  runtime cache write; healthcheck uses `urllib`, not curl; `RUN pi
  --version` as `USER relay` is a build-time sanity check;
  `ENV PI_AGENT_SDK=1` is belt-and-braces for `docker exec … pi`)
  + `.dockerignore` + `docker-compose.example.yml` (bind-mounts
  `~/.pi` → `/home/relay/.pi` for the per-user OAuth credential;
  un-vendored Langfuse — points at `docs/langfuse-compose.example.yml`).
  **Pi auth is per-user OAuth and cannot be baked in** — one-time
  host `PI_AGENT_SDK=1 pi` login populates `~/.pi/agent/auth.json`
  before the first `docker compose up`. **Uid gotcha**: image runs as
  uid 10001 (`relay`); host `~/.pi/agent/auth.json` (mode 600) needs
  either a host-side chown to 10001 or a compose `user:` override —
  documented in the compose example. `.github/workflows/ci.yml` runs
  the **full** gate (Python `ruff`/`mypy`/`pytest` **and** `frontend/
  npm run check`) and publishes to `ghcr.io/johnmathews/relay` on
  push to `main` via `${{ github.token }}` (`workflow_dispatch`
  present). The prod frontend is served by FastAPI via the additive
  conditional `relay.api.static.mount_frontend` (no-op without a
  build) — spec §11.2.
- **`PI_AGENT_SDK=1` is load-bearing, not cosmetic** (ADR-51, grounded
  in pi v0.78.0 source via the fork at `johnmathews/pi:relay-bridge`
  (`packages/ai/src/providers/anthropic-agent-sdk.ts`,
  `packages/ai/src/providers/agent-sdk-mcp-tools.ts`)).
  Flag set → OAuth requests route through the `@anthropic-ai/claude-
  agent-sdk` bridge against Claude Pro/Max subscription quota AND
  `buildPiMcpServerForBridge` wraps pi's tools as an MCP server for
  the agent-sdk to consume. Flag unset → legacy direct-HTTP path
  (per pi's source comment: "currently 400ing under most account
  states") AND the MCP tool bridge stays in chat-only fallback so
  tool calls don't fire. `PiHarness.spawn` injects it per-iter
  (`harness/pi.py:469`); the production image sets `ENV
  PI_AGENT_SDK=1` as defense-in-depth for ad-hoc `docker exec`
  invocations.
