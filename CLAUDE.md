# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Phases 0–8 are complete — the MVP is done.** Phase 0 scaffold + Phase 1 harness layer +
Phase 2 orchestrator (`RelayCore`, append-only `EventStore`, chained-iter
`run_loop`, run lifecycle, `RELAY_*` preamble). Phase 3 adds the **REST
API + persistence** (`src/relay_v2/api/`, `src/relay_v2/sse.py`): every
spec §7 endpoint (runs create/list/get/cancel/resume/events/preview; SSE
live stream; projects CRUD; sandboxed read-only file browser; versioned
prompts CRUD), Pydantic v2 schemas, and the SSE broadcaster (in-process
post-commit fan-out + DB-tail `Last-Event-ID` replay/cutover). Every
route is a thin adapter over the single shared `RelayCore` (ADR-07/15);
new capability was added as `RelayCore` service methods, not route
logic. SSE only tails the event store (ADR-10). Auto-generated OpenAPI
3.1 validated. Phase 4 adds the **Vue 3 dashboard MVP** (`frontend/`):
the primary control plane per ADR-15/spec §9 — Hub, Project view
(runs/prompts/files panes), 4-step New-Run wizard with side-effect-free
preview, Run-detail with a live SSE timeline + iters/artifacts/worktree
panes, prompts CRUD, project register/unregister. A typed
`openapi-fetch` client is generated from `/openapi.json`; Pinia Colada
is the REST cache (SSE pushes coalesce cache invalidations); the file
render pipeline is markdown-it + lazily-loaded shiki + dynamic-import
mermaid + diff2html. The Worktree pane is deliberately degraded to
read-only `worktree_path`/`branch` (live git status/diff is a named
post-MVP gap — Phase-4 scoping decision). Verified against a
scripted-harness double + the running backend; `uv run pytest` green
(142 passed, 3 pi-e2e gated behind `PI_INTEGRATION=1`), `ruff`/`mypy
--strict` clean, backend coverage 92%; the frontend gate (`npm run
check` = eslint `--max-warnings 0` + `vue-tsc` + vitest, 136 passed)
is green, eager bundle ~41 KB gz (heavy renderers lazy). Phase 5 adds
the **MCP server** (`src/relay_v2/mcp/`): a FastMCP server mounted at
`/mcp` on the same app, whose seven spec §8 tools
(`relay__list_runs/get_run/start_run/cancel_run/pause_response/
tail_events/read_artifact`) are thin adapters over the single shared
`RelayCore`, reusing the REST `api/schemas.py` Pydantic models
(ADR-07/15) — no proxying, no new core capability. Mounted in the app
lifespan with `async with mcp.session_manager.run():` (the #1367
footgun: a mounted sub-app's lifespan is not auto-run). Phase 6 adds
the **engineering-team skill port** (`skills/engineering-team/`, 11
docs) + `relay install-skill` (`src/relay_v2/cli/install_skill.py`):
the v1 skill ported faithfully with six deliberate adaptations
(single-session/no-subagent-dispatch, `.relay/runs` paths,
relay-provisioned worktree, inlined Phase-4 gate replacing
`/done`+`/merge-push`, repointed sentinel pointers, `uv run`
examples) — sentinel grammar verbatim (ADR-28). Skill+CLI only; no
orchestrator/REST/SSE/MCP contract changed. Phase 7 adds the **OTel
mirror** (`src/relay_v2/observability/`): an opt-in
`relay.run`→`relay.iter`→`relay.tool_call` span tree mirroring the
event store (ADR-10 — never a second source), exported OTLP/HTTP to
self-hosted Langfuse when `RELAY_OTEL_EXPORT=langfuse`, a strict
literal no-op (no provider/exporter/network) when `none`. GenAI/usage
attributes come from pi's verbatim `SessionEnded.messages[].usage`
(ADR-18); recovering them on the terminal-sentinel close path needed a
one-event `AssistantText` lookahead in `PiSession.events()` —
**Option D**, harness-only (ADR-04), order-preserving, deterministic,
no loop/event-store contract change (ADR-29). Phase 8 adds the
**verification & polish** layer (ADR-30): a rewritten `README.md`
(Phases 0–8; install/run/dashboard/MCP/observability/Docker); an
**additive, conditional** production frontend mount
(`src/relay_v2/api/static.py` — `mount_frontend` appends a vue-router
history-mode `StaticFiles` catch-all at `/` in the lifespan *after*
`/mcp`, a literal no-op when `frontend/dist/` is absent so dev/test is
byte-for-byte unchanged; spec §11.2); a multi-stage `Dockerfile` +
`.dockerignore` + `docker-compose.example.yml`; and
`.github/workflows/ci.yml` (full Python **and** frontend gate + GHCR
publish to `ghcr.io/johnmathews/relay` on push to `main`,
`workflow_dispatch`). The Phase-8 verification split is ADR-30
(automated CI for the deterministic half — ruff/mypy/pytest + `npm run
check` + `docker build`; manual journal-attested for the real-pi e2e
demo, "image pulls and runs", "MCP from Claude Code", live-Langfuse
tree — gated like `PI_INTEGRATION=1`, mirroring ADR-24/28 §3/29).
`uv run pytest` green (**194 passed**, 3 pi-e2e gated),
`ruff`/`mypy --strict` clean (**38** source files), backend coverage
93%; `docker build` + container-boot smoke verified locally. **Two
follow-ups remain open, deliberately not closed by Phase 8** (closing
either is a contract change): the live-Langfuse-UI acceptance was
never run (manual, journal-attested when done); and the latent ADR-10
gap that `agent_end`/`SessionEnded` is never persisted as an `events`
row on the sentinel-close path (its own ADR + spec §6 change —
ADR-29/30). Operational refs: `docs/harness.md`,
`docs/orchestrator.md`, `docs/api.md`, `docs/dashboard.md`,
`docs/mcp.md`, `docs/skills.md`, `docs/observability.md` (Phase 7;
the OTel mirror + Langfuse wiring + the manual trace-tree acceptance
procedure; `docs/langfuse-compose.example.yml` is the self-host
pointer; `docs/mcp-config.example.json` is the MCP client
registration snippet; `frontend/README.md` is the dev quick-start).
Design docs (`docs/`) and the pi de-risking `scratch/` dir remain the
canonical context. New ADRs: ADR-19/20/21 (Phase 2 — orchestrator
runtime, pause/resume, async DB), ADR-22 (resume forward-progress,
pre-Phase-3 hardening), ADR-23 (SSE broadcaster + Last-Event-ID
cutover), ADR-24 (API test toolchain), ADR-25 (run-artifacts second
sandboxed root), ADR-26 (Phase-4 frontend toolchain mandates), ADR-27
(Phase-5 MCP toolchain: bundled SDK, `mcp>=1.27.1,<2` pin, lifespan
session-manager wiring), ADR-28 (Phase-6 skill port: single-session,
repo-root + wheel force-include, manual behavioral verification),
ADR-29 (Phase-7 OTel mirror: self-owned non-global TracerProvider,
deferred literal no-op, `opentelemetry-*>=1.27,<2` pins, Option-D
pi-harness lookahead so terminal-sentinel iters still recover usage,
automated span-structure tests + manual Langfuse-UI acceptance),
ADR-31 (post-MVP bugfix: a non-Cancelled exception out of the loop
or `_apply_result` is finalised as `failed` + `run_ended`
`internal_error: …` instead of leaving the run permanently
`running` — paired with an `expanduser` + existence check in
`register_project` so `~/...` no longer lurks as a literal path;
extended in the same iter with a startup-time orphan sweep in
`RelayCore.start()` and a `cancel_run` safety net that finalise any
'running' row whose owning process is gone — single-user/-process
MVP per ADR-12, so a 'running' row at startup must come from a
prior process and can never resume). **Phase 9a** then adds the
defensive plumbing for the post-MVP fanout-join feature
(`docs/proposals/parallel-iters-fanout-join.md`,
`docs/plans/2026-05-21-fanout-join-9a.md`): a new
`awaiting_children` run status (NOT terminal — can transition back
to `running` once children settle in 9c), a reserved
`child_runs_resolved` event kind in the taxonomy, and a depth-first
cascade-cancel helper threaded through `_recover_orphans` so a
parent in `awaiting_children` at startup is finalised together with
its descendants under the S1 convention (ADR-34: recovering an
in-flight fanout across a restart is a V1 non-goal; the helper is
reused by 9d for runtime cancel-cascade). The `_TERMINAL` constants
in `api/events.py`, `frontend/src/stores/events.ts`, and
`frontend/src/views/RunDetailView.vue` keep their existing values
(they already exclude `awaiting_children` correctly) — the change is
the comments + a regression test
(`tests/api/test_sse.py::test_sse_treats_awaiting_children_as_live`)
that an event appended after the SSE generator subscribes still
reaches the consumer. `StatusBadge.vue` gains a dedicated
amber-tinted variant. 211 backend tests pass (205 + 5 orphan/cascade
+ 1 SSE live; 3 pi-e2e still gated); 142 frontend tests pass (+1 new
StatusBadge case). No production code path creates an
`awaiting_children` row yet — 9b/9c land the dispatch + join.
**Phase 9b** then lands the fanout dispatch
(`docs/plans/2026-05-21-fanout-join-9b.md`): a new closing sentinel
verb `[[engteam:fanout]]` paired with a `[[engteam:fanout-start]] …
[[engteam:fanout-end]]` JSON marker block (spec §5.4) — parsed by
`extract_fanout_payload` into a Pydantic `FanoutPayload`
(`src/relay_v2/harness/signaling/fanout.py`), surfaced as a
terminal `fanout` signal by `detect_in_text`, threaded through the
loop as a new `LoopResult("awaiting_children", fanout_payload=…)`
return path. `RelayCore._apply_result` routes that status to
`_dispatch_children`, which spawns N child runs (Shape B — separate
`runs` rows joined via `parent_run_id`, NOT iters of the parent)
whose worktrees branch off the parent's worktree HEAD (not the
project default branch) via the new
`provision_workspace(..., parent_worktree_path=…)` param. Concurrency
is capped by an `asyncio.Semaphore(settings.max_fanout_concurrent)`
in the supervisor (ADR-35, Option A: every child row exists in the
DB from dispatch and is swept by the existing orphan-recovery
machinery on restart — no new persistent intermediate state).
Recursion is bounded by `max_fanout_depth` (default 2, hard cap 4
via `RELAY_MAX_FANOUT_DEPTH`); `_fanout_depth` walks the
`parent_run_id` chain at dispatch time and a depth-exceeded child
finalises as `failed`. The parent stays in `awaiting_children` with
no `run_ended` event — 9c will land the join/synthesizer iter that
transitions it back to `running`. `join_prompt` flows from 9b to
9c via `iters.signal_args["payload"]["join_prompt"]` on the closing
fanout iter (OCQ-1 resolution: option a, status-quo, guarded by a
dedicated integration assertion; promotable to a dedicated column
in 9c if it feels too implicit). The OCQ-2 "restart with parent in
`awaiting_children` + children still pending" edge is covered by a
direct-`_recover_orphans` regression in `test_fanout_integration.py`
that confirms the 9a cascade helper finalises all three rows. New
ADR-35 records the Option-A semaphore decision over Option-B
queue-and-block (queue would replicate the gap 9a closed for
`awaiting_children`). 237 backend tests pass (211 + 15 sentinel + 2
loop + 3 lifecycle + 4 dispatch + 2 integration; 3 pi-e2e still
gated), `ruff`/`mypy --strict` clean (**39** source files), backend
coverage 94%. No frontend changes in 9b (the dashboard "Children"
pane is 9e). 9c lands the synthesizer iter + parent resume; 9d the
runtime cancel-cascade; 9e the dashboard; 9f OTel span parenting.
**Phase 9c** then closes the fanout-join loop
(`docs/plans/2026-05-21-fanout-join-9c.md`): a new
`RelayCore._maybe_resume_parent` watcher fired from each child's `_run`
finally block (ADR-36, OCQ-3) — when all siblings of an
`awaiting_children` parent reach a terminal status, the watcher emits
one `subagent_return` per child + one `child_runs_resolved`, transitions
the parent `awaiting_children → running`, and re-enqueues it with a
synthesizer `RunContext` whose body is
`compose_join_prompt(join_prompt, child_results)` — the `join_prompt`
recovered from the closing fanout iter's
`signal_args["payload"]["join_prompt"]` (OCQ-1's 9b channel kept, OCQ-4
evaluated and held), the trailer a YAML-ish `RELAY_CHILD_RESULTS:`
block listing each child's `id`/`role`/`status`/`summary`/`branch`/
`worktree_path` (OCQ-5: body, NOT preamble — ADR-14's
`RELAY_RUN_DIR`/`RELAY_PHASE` invariant unchanged). The synthesizer
iter runs on the parent's existing worktree (no new worktree for the
join); recursive fanout from the synthesizer is permitted up to
`max_fanout_depth`. Partial-failure semantics: the synthesizer always
runs once all children settle regardless of mix; the orchestrator
never auto-fails the parent on a child's failure — the agent decides
via the trailer (OCQ-6, proposal §cancellation-semantics). Two
structural fixes landed with the watcher and are recorded in ADR-36's
implementation-refinement section: (a) `_run`'s finally calls the
watcher *before* `state.settled.set()` so a caller awaiting a child's
`wait_for_run()` then immediately the parent's cannot race the
watcher's swap of `self._runs[parent_id]` and observe the stale
`awaiting_children` result; (b) `_dispatch_children` creates all child
rows + their `subagent_dispatch` events in one pass and enqueues them
in a second pass, so a fast harness cannot let child A finish before
child B's row exists (which would short-circuit the watcher's "all
terminal?" check on a partial set). The existing `_enqueue_lock`
serialises the watcher so two children settling near-simultaneously
cannot both resume the parent. New ADR-36 records the watcher-
placement + body-shape decisions. 256 backend tests pass (237 + 15
new from 9c: 7 lifecycle_join + 8 join_watcher + 1 new fanout-join
integration; 3 pi-e2e still gated), `ruff`/`mypy --strict` clean
(**39** source files, no new modules), backend coverage 94%. No
frontend changes in 9c (the dashboard "Children" pane is still 9e).
**Phase 9d** then wires the runtime cancel-cascade
(`docs/plans/2026-05-21-fanout-join-9d.md`): `cancel_run` on an
`awaiting_children` parent now acquires `_enqueue_lock`, flips the
parent to `cancelled` *first* (parent-first ordering — load-bearing,
because the 9c join watcher also acquires the same lock and re-reads
the parent under it; a child terminal landing between a
descendants-first cascade and the parent flip would let the watcher
resume the parent mid-cancel, exactly what we're cancelling), then
calls a new sibling helper `_cascade_cancel_runtime` that walks
descendants depth-first and applies a per-descendant strategy: an
**in-flight** descendant (in-memory `_RunState` exists and not
settled) gets a fire-and-forget signal — `cancel_event.set()` +
`session.cancel()` — and lets its own `_run.CancelledError` branch
write the `run_ended` (pre-writing the DB here would double-emit);
a **DB-only** descendant (queued-but-not-started, or `_RunState`
lost) gets `set_run_status(cancelled, ended=True)` + `run_ended`
written directly, mirroring the 9a startup helper. The 9a
`_cascade_cancel_descendants` stays as the startup-only sibling
(no in-memory states exist post-restart by definition). `_run`
gains a cancelled-before-start guard so a queued descendant
DB-flipped by the cascade exits immediately on supervisor pickup
with no stray `iter_started` event; the guard intentionally
bypasses `_maybe_resume_parent` because the cascade's
parent-first ordering already moved the parent out of
`awaiting_children`, so the watcher would no-op anyway. The
in-flight cancel path (set `cancel_event` + cancel session) stays
outside the lock — it's preserved verbatim for normal running
runs, and the ADR-31 orphan safety net for "no in-memory state +
DB row stuck" is preserved too. No new schema, no new event kinds,
no new sentinel grammar; `POST /api/runs/{id}/cancel` and the MCP
`relay__cancel_run` tool inherit the new behaviour with no
signature change. New ADR-37 records parent-first ordering, the
in-flight-vs-DB-only split, `_enqueue_lock` reuse for serialising
against `_maybe_resume_parent`, and the fire-and-forget +
cancelled-before-start guard rationale. 266 backend tests pass
(256 + 10 new from 9d in `tests/orchestrator/test_cancel_cascade.py`:
3 cascade-helper + 5 cancel_run branches + 1 cancelled-before-start
guard + 1 deep-tree integration; 3 pi-e2e still gated),
`ruff`/`mypy --strict` clean (**39** source files, no new modules),
backend coverage 94%. No frontend changes in 9d. 9f will land OTel
span parenting across runs.
**Phase 9e** lands the dashboard "Children" pane
(`docs/plans/2026-05-21-fanout-join-9e.md`). Four user-visible pieces:
(1) **Children pane** in `RunDetailView` — rendered only for parent
runs (`parent_run_id == null` + at least one child); each row shows
`status · short-id · role · branch · summary` fetched from the new
`GET /api/runs/{id}/children` endpoint via a `useRunChildrenQuery`
Pinia Colada hook; revalidates whenever the events store receives
`subagent_dispatch`, `subagent_return`, or `child_runs_resolved` (all
three added to `INVALIDATING_KINDS`) via a new
`['runs','children',runId]` invalidation key. (2) **Parent chip** in
the run-detail header — `ParentRunChip.vue` renders a link back to the
parent's detail view whenever `parent_run_id != null`. (3) **Cancel
button cascade copy** — the predicate expands to `status ∈ {running,
awaiting_children}`; for a parent in `awaiting_children` the label
reads "Cancel run and N children" (N from the children query length);
the API call is unchanged. (4) **"Show child runs" toggle** in the
Project view Runs pane — child runs are hidden by default (the default
`GET /api/runs` excludes them via `include_children=false`); the toggle
sets `RunListFilters.includeChildren = true` and re-fetches. Backend
additions: `RelayCore.list_children(run_id)` returning all direct
children ordered by `created_at`; `RelayCore.list_runs(...,
include_children: bool = False)` filtering out rows where
`parent_run_id IS NOT NULL` by default; `GET /api/runs/{id}/children`
REST route (thin `list_children` adapter); `?include_children=` query
param on `GET /api/runs`; MCP `relay__list_runs` passes
`include_children=True` so MCP callers see the full tree. New frontend
SFCs: `ChildrenPane.vue`, `ParentRunChip.vue`. No new ADR, no new
schema, no new event kinds, no new sentinel grammar. 276 backend tests
pass (266 + 10 new: +2 `list_children`, +2 `list_runs` default, +1 MCP,
+3 children-route, +2 `include_children` API; 3 pi-e2e still gated),
`ruff`/`mypy --strict` clean (**39** source files), backend coverage
94%; 155 frontend tests pass (142 + 13 new: +2 `ParentRunChip`, +5
`ChildrenPane`, +2 events store, +4 `RunDetailView`, +1 `ProjectView`).
Manual smoke (live fanout → Children pane populates; Parent chip
navigates; Cancel cascade label; toggle hides/shows children) is
journal-attested per ADR-30.
**Phase 9f** then closes the fanout-join arc (9a→9f) with OTel
cross-run span parenting (`docs/plans/2026-05-22-fanout-join-9f.md`,
ADR-38): child runs' `relay.run` spans, and a parent's synth-phase
`relay.run` span, now hang under the iter where the parent fanned
out — so the full fanout-join cycle renders as **one connected tree**
in Langfuse instead of three disconnected `relay.run` roots
(`parent → fanout iter` / `child A` / `child B` / `parent synth iter`).
Mechanism: a new opaque `IterSpanContext = Any` carrier (defined +
re-exported from `relay_v2.observability`) plus a new
`Instrumentation.run_span(*, parent_iter_ctx=None)` kwarg — when set,
the run span is started under that iter's OTel context via
`trace.set_span_in_context(...)`. The plumbing chain: `loop` captures
the live `iter_span.context` inside the **closing fanout iter** only
(via the existing `_drive_iter` defaulted-span seam) → returned on
`LoopResult.fanout_parent_ctx` → `_apply_result` threads it to
`_dispatch_children`, which stashes the context on each child's
`_RunState.parent_iter_ctx` in the **first pass** of the 9c
create-all-then-enqueue split (so the invariant survives — every
child row + dispatch event still exists before any child is enqueued)
→ `_run` passes `parent_iter_ctx=state.parent_iter_ctx` into
`otel.run_span(...)`. Synth-phase wiring (Task 4b — the recursive
symmetry that ADR-38 §interpretive-subtlety captures):
`_maybe_resume_parent` preserves `old_state.result.fanout_parent_ctx`
across the `_RunState` overwrite at the parent's resume and stashes
it on the fresh state's `parent_iter_ctx`, so the synth-phase
`relay.run` parents under the iter where **THIS** run fanned out
(making the synth phase a sibling of THAT level's children, NOT a
sibling of THIS level's pre-fanout iters — the symmetric and
recursive-fanout-safe choice). NOOP invariant: byte-for-byte
unchanged — `_NoopIterSpan.context = None` is a class attribute that
constructs no provider/exporter and makes no network call;
`NoopInstrumentation.run_span` accepts and ignores the new kwarg.
ADR-38 records that cross-run trace context lives in-memory only
(threaded via `LoopResult` → `_RunState`, never persisted), uses an
opaque carrier so the orchestrator never imports the OTel API
directly (ADR-29's seam preserved), and that a restart loses the
linkage — acceptable under ADR-34 (in-flight fanout across restart
is a V1 non-goal; the 9a cascade-cancel helper finalises the tree).
No new schema, no new event kinds, no new sentinel grammar, no new
modules — source file count stays at **39**. 293 backend tests pass
(276 + 17 new from 9f: span-parenting unit, dispatch-threading,
synth-phase preserve, NOOP-kwarg, end-to-end InMemorySpanExporter
trace-tree integration; 3 pi-e2e still gated), `ruff`/`mypy
--strict` clean, backend coverage 94%. Acceptance: automated via
`InMemorySpanExporter` (one connected tree per cycle, no orphan
roots) + manual live-Langfuse-UI journal-attested per ADR-30.
**Out-of-scope reminders**: the latent ADR-10 gap that
`agent_end`/`SessionEnded` is never persisted as an `events` row on
the sentinel-close path is still parked (ADR-29/30 — its own ADR +
spec §6 change when opened). The fanout-join arc (9a→9f) is now
fully shipped.
**Phase 9g** then closes the latent ADR-10 invariant gap that
`SessionEnded` was captured by the Option-D harness lookahead and
surfaced to OTel (ADR-29) but never written to the events table. A
new event kind `harness_session_ended` is appended in
`loop._finish_iter` on every iter-close path (signal / cancelled /
timeout / no-signal / crash) BEFORE the paired `iter_ended` event,
with payload `{stop_reason, messages, summary}` — `messages`
verbatim per ADR-18, `summary` populated only on the `done` close
path (the current sentinel grammar carries no summary in
`signal.args` for `done`, so this is `null` in practice today; the
plumbing is in place for a future grammar change). The OTel mirror
still reads from `out.messages` in-memory in the loop's finally
block (ADR-29 lookahead preserved); the new event row is for replay
consumers (SSE, audit, future analytics). Frontend gains a small
`UsageRow.vue` rendering stop_reason + summed token counts inline
in the timeline; `INVALIDATING_KINDS` in `stores/events.ts`
includes the new kind so Colada caches refresh. New ADR-39 records
the contract change; `spec.md` §3.2 gains the taxonomy row and §6
the close-time persistence paragraph. No schema change, no harness
change (ADR-04 preserved), no MCP change. 294 backend tests pass
(293 + 2 new from 9g: 1 `test_loop_emits_harness_session_ended_on_done_close`
+ 1 `test_sse_replay_includes_harness_session_ended`; 3 pi-e2e still
gated; one pre-existing unrelated skill-structure test remains
failing and is tracked separately), `ruff`/`mypy --strict` clean
(**39** source files), backend coverage 94%; 158 frontend tests
pass (155 + 3 new: 2 `UsageRow` + 1 `TimelinePane`). Note that the
prevailing orchestrator-test pattern reads back the *kinds present*
(set membership) rather than exact kinds lists or event counts —
so the planned widespread re-baseline never materialised; only two
new tests cover the new event directly.
**Post-9g bug-fix sweep** (2026-05-23) closed three independent
regressions filed in the 9f live-acceptance journal
([journal/260523-9f-bug-fixes.md](journal/260523-9f-bug-fixes.md)),
each shipped as its own commit chain: (1) `UsageRow.vue` was reading
Anthropic-API token names (`input_tokens`/`output_tokens`/
`cache_read_input_tokens`) but pi's `SessionEnded.messages[].usage`
uses pi-flavoured keys (`input`/`output`/`cacheRead`/`cacheWrite`/
`totalTokens` + `cost.total`) per ADR-18 — the same names
`_aggregate_usage` in `observability/otel.py` reads; the SFC + fixture
now mirror that source of truth (and assistant-role filter), restoring
the in-tile token + cost summary. (2) `provision_workspace` was being
called with `settings.data_dir` (the relay-global SQLite root) so a
run started against project A had its worktree provisioned under
relay-v2's tree instead of `A/.relay/worktrees/<run_id>` — spec §3.3
violation. Fix: `provision_workspace(project_root, run_id, …)` drops
its `data_dir` arg and resolves the workspace under
`project_data_dir(project_root) = project_root / ".relay"`; new
`RelayCore.get_run_artifacts_dir(run_id)` is the single resolver for
the read paths (`api/artifacts.py`, MCP `relay__read_artifact`,
core's resume / preview `run_dir` derivations) — they no longer
reach into `settings.data_dir`; `spec.md` §11.1's `RELAY_DATA_DIR`
description is clarified accordingly (`data_dir` now holds the
multi-tenant `relay.db` only; everything per-project is per-project).
Existing runs whose `worktree_path` rows still point at the old
relay-global location will 404 through the new routes — acceptable
for the single-user MVP, no data lost on disk. (3) `KNOWN_EVENT_TYPES`
in `frontend/src/api/sse.ts` was missing `harness_session_ended`
(ADR-39) and `child_runs_resolved` (9a) — the browser EventSource
only fires listeners for explicitly registered named events, so live
events of those two kinds were silently dropped client-side (refresh
worked because it skipped SSE and hit REST replay); both kinds added,
inline comment ties the list to spec §3.2 with the `grep` command
that catalogs every kind the backend can emit, and the prior 9a test
that falsely passed (the fixture also emitted `subagent_return` whose
invalidation key was the assertion target) is supplemented by two
isolating cases that emit ONLY the kind under test. No schema change,
no new event kinds, no new sentinel grammar, no new modules, no new
ADR — all three are pure bug fixes restoring existing contracts. 298
backend tests pass (296 + 2 new from Bug 1: `test_project_data_dir.py`;
3 pi-e2e still gated), `ruff`/`mypy --strict` clean, backend coverage
94%; 161 frontend tests pass (158 + 3 new: 1 `UsageRow` real-payload
+ 2 `events.store` isolating cases).
**Phase 14a** (2026-05-22,
[docs/proposals/pause-for-review.md](docs/proposals/pause-for-review.md),
[docs/plans/2026-05-22-pause-for-review-14a.md](docs/plans/2026-05-22-pause-for-review-14a.md))
opens the **pause-for-review** arc by landing the backend half: a new
`PauseReviewError` exception + `RelayCore.write_artifact(run_id,
rel_path, content, *, editor)` method (paired with a module-private
`_normalise_review_path` helper) and a new
`PUT /api/runs/:id/artifacts/{path:path}` REST route that is the
**single write entry point** on the run artifacts dir. The endpoint
appends a new event kind `artifact_edited` (iter-scoped to the paused
iter, payload `{path, size_before, size_after, sha256_before,
sha256_after, editor}` — content stays on disk per ADR-25, hashes
give an integrity check per ADR-40 §B1) and is **strictly coupled**
to `run.status == 'paused'` AND a matching `signal_args["review_path"]`
on the latest paused iter (OQ-1 strict-coupling decision: writes only
during a declared review moment; not a general write API). The route
reuses the ADR-25 `resolve_within_sandbox` resolver verbatim for
sandbox checks (traversal/absolute/NUL/symlink-escape → 400), maps
`PauseReviewError.code` to HTTP status (`unknown_run → 404`,
`too_large → 413`, `binary → 415`, every other code → 409 —
`not_paused`, `no_review_path`, `path_mismatch`, `missing_parent_dir`),
and writes atomically via a tempfile-in-same-dir + `Path.replace`
rename so a crash mid-write leaves the original file intact. 14a does
NOT auto-create intermediate dirs — a nested `review_path` like
`discussions/notes.md` is only accepted if `discussions/` already
exists (proposal §OQ-3). The 14a release ships an endpoint that
returns 409 for every real-world caller until 14b lands (no production
path writes `review_path` into `signal_args` yet); this is the
documented interim state, and the test suite synthesises the
post-14b world by seeding `signal_args["review_path"]` directly via
the sessionmaker. New ADR-40 records A1 (sentinel-attribute opt-in
over implicit dashboard inference), B1 (in-place write + hash-bearing
event over versioned snapshots / event-payload content), OQ-1 strict
coupling, the sandbox-resolver reuse, the atomic write pattern, and
the `PauseReviewError`-code-driven HTTP mapping; `spec.md` §3.2 gains
the taxonomy row and §7 the PUT route + a note paragraph. The
`core.py → api/files.py` imports for the sandbox helpers
(`BINARY_SNIFF_BYTES`, `MAX_FILE_BYTES`, `resolve_within_sandbox`)
are **lazy inside `write_artifact`** to avoid a circular import
(`api/files → api/deps → core`); a future cleanup may lift those
names into a shared utility module. 14a is BACKEND ONLY — no
frontend changes (`KNOWN_EVENT_TYPES` / `INVALIDATING_KINDS` /
`PauseAnswerForm.vue` / `TimelinePane.vue` are 14c), no sentinel
parser change (14b), no `compose_resume_prompt` annotation (deferred
per OQ-4), no MCP tool, no skill template change (14d). 313 backend
tests pass (298 + 15 new in `test_artifacts_write.py`: 2 happy-paths
+ 1 normalisation + 4 precondition 409s + 1 unknown-run 404 + 2
sandbox 400s + 2 body 415s + 1 oversize 413 + 1 atomic-write + 1
editor-field; 3 pi-e2e still gated), `ruff`/`mypy --strict` clean
(**39** source files, no new modules), backend coverage 94%. No
frontend changes — frontend test count unchanged at 161.
**Phase 14b** (2026-05-22,
[docs/plans/2026-05-22-pause-for-review-14b.md](docs/plans/2026-05-22-pause-for-review-14b.md))
teaches the sentinel parser the optional `review_path` attribute on
`[[engteam:pause-for-input ...]]`, lighting up 14a's
documented-interim-409 write endpoint as a working write path for any
skill that emits the attribute. New `extract_pause_review_path(text)`
in `src/relay_v2/harness/signaling/sentinels.py` mirrors
`extract_pause_id` / `extract_pause_question` (line-anchored
`_PAUSE_RE.match` → `review_path="..."` regex with `\"`-unescape
support) and delegates syntactic validation to a module-private
`_validate_review_path`: empty / NUL-byte / absolute (leading `/` or
`PurePosixPath.is_absolute()`) / any `..` path component raise
`MarkerError` with a focused multi-line repair recipe
(`_REVIEW_PATH_REPAIR`, same shape as `extract_fanout_payload`'s
`_REPAIR`). `detect_in_text`'s pause branch calls the new extractor
and conditionally adds `"review_path"` to `args` — **the key is
ABSENT from `signal_args` when the attribute is absent** (load-bearing
for 14a's `no_review_path` 409 branch in `write_artifact`; a
present-but-`None` key would falsely satisfy `if "review_path" in
signal_args`). The parser performs syntactic validation only (no
filesystem resolution); the 14a `resolve_within_sandbox` resolver
remains the runtime enforcement per ADR-25/40. `signal_args["review_path"]`
travels through the existing pause-persistence machinery (the same
JSON column already carrying `next_prompt`/`question`/`id`), so the
loop, event store, and SSE need no change. `skills/engineering-team/
pi/references/sentinels.md` gains a "Reviewable pauses
(`review_path`)" sub-section + a verbs-list annotation; `spec.md` §5
gets a one-paragraph note. 14b is HARNESS-SIGNALING + SKILL-DOCS
ONLY — no frontend change (14c), no Phase-2 skill template change
(14d), no `compose_resume_prompt` change (deferred per OQ-4 to 14e),
no new event kind (`artifact_edited` already shipped in 14a), no new
ADR (the A1 decision is recorded in ADR-40). 325 backend tests pass
(313 + 12 new: 11 in `test_signaling_sentinels.py` covering
absent/present/subdir/quote-unescape/empty/absolute/traversal/
nested-traversal/NUL + `detect_in_text` present-and-absent cases, +
1 orchestrator integration `test_pause_signal_args_carries_review_path`
in `test_loop.py` asserting end-to-end persistence; 3 pi-e2e still
gated), `ruff`/`mypy --strict` clean (**39** source files, no new
modules), backend coverage 94%. Frontend test count unchanged at 161
(no frontend file touched).
**Phase 14c** (2026-05-23,
[docs/plans/2026-05-22-pause-for-review-14c.md](docs/plans/2026-05-22-pause-for-review-14c.md))
lights up the operator-facing half of the pause-for-review arc: when
the paused iter's `signal_args.review_path` is present (14b),
`PauseAnswerForm.vue` switches into a richer mode — a top review pane
above the existing question/answer block fetches the named artifact
via `useArtifactContentQuery`, renders a `<textarea>` (left) +
`MarkdownRender` lazy markdown/shiki/mermaid preview (right), and
exposes a **Save** button wired through the new
`useArtifactWriteMutation` (a raw `fetch()`-backed Pinia Colada
mutation against the 14a `PUT /api/runs/{id}/artifacts/{path}`
endpoint — raw fetch because the hand-rolled backend route declares
no Pydantic body model so the generated OpenAPI op carries
`requestBody?: never` and openapi-fetch refuses a body field; ADR-40
is unchanged). The Resume button keeps its label and shape and is
disabled ONLY while a Save is in flight (proposal §"Tradeoffs"
choice (a), OQ — locked); the answer textarea is unaffected. **OQ-3
missing file** lands as a "Create at this path" banner + a Save
button relabelled "Create" (Save enabled even when textarea is
empty); **OQ-7 binary** lands as a "not editable inline" message +
a `<a href={artifactRawUrl} download>Download</a>` link. 4xx errors
from the PUT (404/409/413/415/400) surface inline via an `ApiError`
mapper; the operator's textarea content is preserved across save
failures. The `frontend/src/api/sse.ts::KNOWN_EVENT_TYPES` list +
`frontend/src/stores/events.ts::INVALIDATING_KINDS` set both gain
`'artifact_edited'` (the post-9g sweep's load-bearing dual-list
contract — a kind in `INVALIDATING_KINDS` but not
`KNOWN_EVENT_TYPES` is silently dropped by the browser EventSource);
the store's coalesced invalidation flush broadens to add
`['artifacts', runId]` so the editor's loaded baseline (and the
artifacts pane's content cache) refreshes when a save lands.
`TimelinePane.vue` renders each `artifact_edited` event as a small
inline row (`✎ path · sha-before… → sha-after… · editor`); short-sha
is the first 4 chars + ellipsis (mirroring how short run-ids render
elsewhere); a `null` `sha256_before` (create path) renders as `∅`.
No "view diff" link in v1 (proposal §OQ-6 → 14e). `frontend/src/
views/RunDetailView.vue` computes `pauseReviewPath` (walks iters
newest-first, mirroring `pauseQuestion`) and passes it to
`PauseAnswerForm` as a prop — null when the paused iter didn't carry
`review_path`, which makes the SFC's review pane absent and the form
byte-for-byte the pre-14c minimal contract. `frontend/src/api/
schema.d.ts` regenerated from the running backend's `/openapi.json`
to include the 14a PUT op (the regenerated op carries
`requestBody?: never` per above; the raw-fetch mutation is the
contract-honest workaround). `spec.md` §9.1's pause-action bullet
gains a paragraph naming the review-pane mode. 14c is FRONTEND ONLY
— no backend file changed; no MCP, sentinel, or skill-template
change (14d still pending); no diff view or
`compose_resume_prompt` annotation (deferred to 14e per OQ-5/OQ-4).
325 backend tests pass (unchanged from 14b — `uv run pytest` green;
3 pi-e2e still gated), `ruff`/`mypy --strict` unchanged. 173
frontend tests pass (161 + 12 new — 9 PauseAnswerForm review-pane
cases in `tests/PauseAnswerForm.spec.ts` covering absent/render/
save-PUT/saved-badge/resume-disable/404-create/415-binary/
409-inline/discard, +2 TimelinePane cases in
`tests/TimelinePane.spec.ts` for the populated edit row + the
∅→after create-path render, +1 events-store isolating case in
`tests/events.store.spec.ts` emitting ONLY `artifact_edited` and
asserting both delivery + the artifacts-cache invalidation; the
existing coalesced-flush test bumped from 3 → 4 invalidate calls
because the artifacts prefix joined the flush set). One unhandled
vitest rejection ("1 error — Unhandled Rejection: ApiError: not
found") fires from the 404 case — Pinia Colada surfaces the query's
rejected promise as an unhandled rejection before the SFC's lazy
`loadError` computed first evaluates; the existing `queries.spec.ts`
404 test stays clean because it reads `error.value` synchronously in
the test body. CONTRARY to the original 14c claim, this DOES fail
the gate (CI exited 1 for two days; local checks that piped
`npm run check` to `tail` lost the real exit code and looked green).
Fixed in commit `a9813f1` via `frontend/tests/setup.ts`, a
narrow duck-typed `unhandledRejection` listener that swallows only
`{ name: 'ApiError', status: <number> }` rejections (anything else
still fails). ADR unchanged (the A1/B1 decisions are recorded in
ADR-40 from 14a; no new ADR — 14c is the UX implementation of those
locks). No new modules, no new event kinds, no new sentinel grammar.
**Phase 14d** (2026-05-23,
[docs/plans/2026-05-22-pause-for-review-14d.md](docs/plans/2026-05-22-pause-for-review-14d.md),
[skills/engineering-team/pi/phases/phase-2-planning.md](skills/engineering-team/pi/phases/phase-2-planning.md),
[journal/260523-14d-live-acceptance.md](journal/260523-14d-live-acceptance.md))
activates pause-for-review for the **primary caller**, closing the
14a → 14d arc. One template change — the Phase-2 Step-4 closing
sentinel now emits `review_path="improvement-plan.md"` on the same
line as the existing `id`/`question` attributes (single-line is
load-bearing — the parser is line-anchored per
`sentinels.py:_PAUSE_RE.match(line)` + `_REVIEW_PATH_RE.search(line)`,
so attribute formatting that wraps across lines silently drops the
attribute). One new paragraph in the Step-4 "Notes:" block names the
attribute, points at `references/sentinels.md` §"Reviewable pauses",
clarifies the `$RELAY_RUN_DIR`-relative semantics + the single-line
parser constraint, and notes that omitting it keeps pre-14b
behaviour. The Step-5 handoff template is byte-identical (handoffs
don't take `review_path` — unattended path; no human review moment).
The load-bearing "Re-read it in full — the user may have edited it"
instruction inside the `prompt-start`/`prompt-end` body is preserved
verbatim — that's the ADR-20 mechanism by which the resumed iter
picks up the operator's edits (fresh context per iter means the agent
only sees the edit because it re-reads from disk). No backend,
frontend, MCP, sentinel-parser, ADR, or test file touched — 14d is
SKILL-TEMPLATE + JOURNAL ONLY. The automated gate is unchanged
(325 backend, 173 frontend, ruff/mypy strict clean — template-text
change, expected); the live `PI_INTEGRATION=1` engteam end-to-end
acceptance is journal-attested per ADR-30 (operator-driven, the
mirror of 9f's Langfuse-UI gate) — the journal entry above documents
the protocol and leaves the attestation section pending the
operator's live run. The pause-for-review arc (14a → 14d) is now
fully shipped; 14e is the parking lot for the diff view (proposal
§OQ-5), per-edit annotation in `compose_resume_prompt` (§OQ-4,
deferred ADR-40 carry-over), plural `review_paths` (§OQ-2), and the
optional OTel span attribute carrying per-pause `artifact_edited`
count.
**Phase 14e** (2026-05-23,
[docs/plans/2026-05-23-pause-for-review-14e.md](docs/plans/2026-05-23-pause-for-review-14e.md))
lands the "audit polish" bundle for the pause-for-review arc with no
contract change. `PauseAnswerForm.vue`'s right pane gains a
`[ Preview | Diff ]` toggle (Diff disabled while the textarea is
clean; renders dirty-vs-loaded-baseline via the existing lazy
`DiffRender.vue` entry, which dynamic-imports `diff2html`; baseline
updates on Save and a return-to-clean snaps the right pane back to
Preview). `TimelinePane.vue`'s `artifact_edited` rows become
click-targets that open the artifacts pane at the file's *current*
on-disk content — honestly framed as navigation, not a historical
diff (ADR-40 §B1 deliberately does not preserve before-content; a
historical diff is unreconstructable). The click handler mutates the
shared `run:<runId>` file-browser Pinia store's `selectedPath` and
scrolls `ArtifactsPane` into view; the visual row layout (✎ · path ·
sha-before… → sha-after… · editor) is byte-identical to 14c.
`relay.pause.artifacts_edited_count` lands as a scalar attribute on
the **resumed iter's** `relay.iter` span: `RunContext` gains a new
`paused_predecessor_iter_id` field (set by `resume_run`), `run_loop`
issues one `SELECT COUNT(*)` against `events.iter_id == :paused_iter_id
AND kind == 'artifact_edited'` *before* the loop body (so the
attribute lands at iter-start, single int, low cardinality), and
passes the count via the new `pause_artifacts_edited_count` kwarg on
`RunSpan.iter_span`. The OTel module never queries the DB — same
shape as `set_usage(messages)` (orchestrator pre-fetches, OTel sets
the attribute); NOOP `Instrumentation` accepts and ignores the kwarg
(no provider, no exporter, no network — same as the 9f
`parent_iter_ctx` kwarg). And the deferred 9e fanout-docs follow-up
closes: `skills/engineering-team/pi/phases/phase-2-planning.md`
gains a blockquote cross-link to `../references/fanout.md` (the
reference doc + phase-1/phase-3 cross-links already shipped
2026-05-22 — phase-2 was the remaining gap; the 9e block's
"deferred" line is removed). No new ADR; no grammar change; no
event-kind change; no MCP change. Sibling sub-phase 14f (plural
`review_paths`) lands ADR-41 and the only contract change in the
14e/14f bundle. OQ-4 stays parked pending 14d live-acceptance
evidence (proposal §"Open questions"). 328 backend tests pass
(325 + 3 new from 14e: `test_otel_pause_attr.py` covering
0/1/3 `artifact_edited` events on the resumed iter span + NOOP
acceptance; 3 pi-e2e still gated), `ruff`/`mypy --strict` clean
(**39** source files, no new modules), backend coverage 94%;
180 frontend tests pass (173 + 7 new: 5 PauseAnswerForm Diff-toggle
cases — default Preview / disabled-while-clean / enabled-on-dirty /
back-to-Preview-on-Save-or-Discard / no-toggle-on-binary; 2
TimelinePane click-target cases — basic + create-path).

## What relay v2 is

A Python service that orchestrates *chained agent sessions* against a
swappable headless harness (pi for MVP). It breaks a large plan into
work units, runs each in a **fresh** harness session, and carries state
forward via a deliberately compressed handoff. A structured SQLite event
store is the source of truth; a Vue dashboard tails it live and replays
history from it. v2 is a clean-break rewrite of v1 (`~/projects/relay`,
bash + Flask); there is no backward compatibility.

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
- Confirmed pi version is **v0.74.0**; pin to it (`docs/plan.md`
  pre-phase). Note `motivation.md` mentions a newer release exists —
  pinning below current is intentional.

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
  `src/relay_v2/db/migrations/` is a placeholder for future numbered
  upgrade scripts. Alembic is deferred.
- Two DB engines, both behind `relay_v2.db` (ADR-21): a **sync** engine
  for `create_all` schema bootstrap only; an **async** `aiosqlite`
  engine (deps `aiosqlite`, `sqlalchemy[asyncio]` → `greenlet`) for all
  orchestrator I/O. Nothing above `relay_v2.db` constructs an engine.
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
  `relay --version` (Phase 0), `relay install-skill`
  (Phase 6 — `[--project PATH] [--force] [--harness NAME]`; ADR-28,
  `docs/skills.md`). Skill source lives at
  `skills/engineering-team/<harness>/` (variant directory, default
  `pi`); the variant model is documented in ADR-33. `relay start` /
  `status` / `cancel` arrive in later phases. Default bind
  `127.0.0.1:7800`.
- Pi integration tests are gated behind `PI_INTEGRATION=1`; harness
  unit tests run offline against the captured `scratch/*.jsonl` fixtures.
  Orchestrator tests live under `tests/orchestrator/` and drive the loop
  against a scripted `Harness` double (no pi). Tests stay under
  `tests/` (`testpaths=["tests"]`), not the per-package `tests/` dirs
  plan.md sketches.
- Packaging (Phase 8, ADR-30): a multi-stage `Dockerfile` (Node stage
  builds `frontend/dist/`; `python:3.13-slim` runtime runs the
  `uv`-synced backend from `/app/.venv/bin/relay` — not `uv run`, no
  runtime cache write; healthcheck uses `urllib`, not curl) +
  `.dockerignore` + `docker-compose.example.yml` (un-vendored Langfuse
  — points at `docs/langfuse-compose.example.yml`).
  `.github/workflows/ci.yml` runs the **full** gate (Python
  `ruff`/`mypy`/`pytest` **and** `frontend/ npm run check`) and
  publishes to `ghcr.io/johnmathews/relay` on push to `main` via
  `${{ github.token }}` (`workflow_dispatch` present). The prod
  frontend is served by FastAPI via the additive conditional
  `relay_v2.api.static.mount_frontend` (no-op without a build) — spec
  §11.2.
