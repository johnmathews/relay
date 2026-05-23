# relay-v2 frontend

Vue 3 + TypeScript + Vite dashboard for relay-v2 — the Phase 4
control plane (complete): Hub, Project view (runs/prompts/files
panes), 4-step New-Run wizard, Run detail with the live SSE timeline
+ iters/artifacts/worktree panes, prompts CRUD, project register /
unregister.

Post-MVP additions (shipped on top of the Phase-4 base):

- **Fanout-join (9a–9f):** `ChildrenPane.vue` on parent run detail,
  `ParentRunChip.vue` on child run detail, cascade-aware Cancel
  button copy, "Show child runs" toggle on the Project Runs pane
  (see `docs/dashboard.md` §"Fanout-join dashboard additions").
- **`harness_session_ended` event (9g):** `UsageRow.vue` renders the
  closing-iter row with `stop_reason` + summed token counts inline
  in the timeline.
- **Pause-for-review (14a–14f):** `PauseAnswerForm.vue` review-pane
  mode — fetches the named artifact, renders a textarea + lazy
  markdown preview, exposes a Save button. The right pane carries a
  `[ Preview | Diff ]` toggle (14e — `DiffRender.vue` is the lazy
  diff2html entry). Multi-path pauses (14f) render a tab per
  declared path with per-tab dirty state; single-path is byte-
  identical to 14c. `TimelinePane.vue` `artifact_edited` rows are
  click-targets that navigate the artifacts pane to the file's
  current on-disk content.

This README is the **developer quick-start**. The operational
reference (architecture, state model, backend contract, mandates) is
`docs/dashboard.md`; canonical design is `docs/spec.md` §9; the
acceptance-testing tracker is `docs/acceptance-testing.md`.

## Develop

The dashboard talks to the relay backend over a relative `/api` path.
In dev, Vite proxies `/api` to the backend.

```sh
# 1. Start the backend (default 127.0.0.1:7800), e.g.:
#    uv run relay serve
# 2. Run the dev server:
npm run dev
```

Override the proxy target with `VITE_API_PROXY_TARGET`
(default `http://127.0.0.1:7800`).

## Regenerate the typed API client

The client types are generated from the backend's live OpenAPI schema.
**The backend must be running.**

```sh
npm run gen:api          # uses VITE_API_PROXY_TARGET or :7800
# against a non-default port:
VITE_API_PROXY_TARGET=http://127.0.0.1:7811 npm run gen:api
```

Output: `src/api/schema.d.ts` (committed; do not hand-edit).

## The gate

```sh
npm run check    # = lint && typecheck && test
npm run build    # vue-tsc -b && vite build (must also pass)
```

## Mandates — do not regress (recorded as Phase 4 ADR/spec notes)

1. **vue-router is v5** (not v4) — `src/lib/routes.ts`.
2. **shiki**: `createHighlighterCore` + dynamic langs + JS regex engine
   (`@shikijs/engine-javascript`), never the convenience bundle —
   `src/lib/render.ts`.
3. **mermaid**: dynamic `import('mermaid')` on first diagram render
   only, never a static top-level import — `src/lib/render.ts`.
4. **Vite SSE dev-proxy**: `/api` proxy uses a 1h `proxyTimeout` and a
   `configure(proxy)` hook that disables buffering for
   `text/event-stream` so the live tail does not stall —
   `vite.config.ts`.
5. **vitest v4 removed the `coverage.all` toggle entirely** — it now
   unconditionally counts every file matched by `coverage.include`, so
   coverage scope is set via `coverage.include` in `vite.config.ts` (a
   literal `coverage.all` is a v4 type error). See ADR-26.
6. **Routed views are keyed by `route.fullPath`** (`src/App.vue`) so a
   param-only navigation remounts — no per-run state or SSE stream is
   carried across runs.

Full rationale: ADR-26 (`docs/decisions.md`).

## Load-bearing post-MVP invariants (do not regress)

These came out of the post-9g bug-fix sweep and the 14c CI-rescue;
each one fixes a class of bug that's invisible in code review but
catastrophic in production. Audit when adding a new event kind or
mounting a new Pinia Colada query.

### 1. `KNOWN_EVENT_TYPES` ↔ `INVALIDATING_KINDS` dual-list invariant

Every event kind the backend can emit (`docs/spec.md` §3.2) **must
appear in both** lists:

- `src/api/sse.ts::KNOWN_EVENT_TYPES` — the list of named events the
  `EventSource` registers listeners for. The browser's native
  `EventSource` ONLY fires listeners for explicitly-registered named
  events; an unregistered kind is silently dropped on the live SSE
  stream.
- `src/stores/events.ts::INVALIDATING_KINDS` — the set of kinds whose
  arrival triggers a Pinia Colada cache invalidation (coalesced via
  `queueMicrotask`).

A kind in `INVALIDATING_KINDS` but missing from `KNOWN_EVENT_TYPES`
is the exact bug that shipped in the post-9g sweep: `harness_session
_ended` and `child_runs_resolved` were silently dropped on live SSE;
refresh worked (REST replay does not depend on the registered names),
masking the regression. When adding a new event kind, audit both
lists against:

```sh
# Catalog every kind the backend can emit.
grep -rn 'await self\._store\.append(\|"kind":' src/relay_v2/
```

The CI gate does not enforce this (the dropped-event symptom only
shows in live SSE); a vitest case in `events.store.spec.ts` exercises
an isolated emit per kind to catch the most obvious regressions.

### 2. Vitest unhandled-rejection swallower is narrowly scoped

`tests/setup.ts` registers a `process.on('unhandledRejection')` hook
that swallows ONLY rejections shaped like
`{ name: 'ApiError', status: <number> }`. This catches the
narrow-and-real case where a Pinia Colada query 4xx's on mount
faster than the component's `loadError` computed first evaluates
(observed in the 14c review-pane 404 case — Colada surfaces the
rejected promise as an unhandled rejection before the SFC reads
`error.value`). Anything else still fails CI.

**Do not broaden the swallower.** If a test triggers a non-ApiError
unhandled rejection, fix the test (or the component) rather than
relaxing this filter. The original 14c regression cost CI two days
of red because a local check piped `npm run check` to `tail` and lost
the real exit code; the lesson is to keep the filter as narrow as
possible.

### 3. Pi-flavoured token names in `UsageRow.vue`

Pi's `SessionEnded.messages[].usage` carries `input` / `output` /
`cacheRead` / `cacheWrite` / `totalTokens` (verbatim per ADR-18),
NOT Anthropic-API names (`input_tokens` / `output_tokens` /
`cache_read_input_tokens`). The `UsageRow.vue` aggregator + its
fixture must mirror the pi keys. The same names are read by
`src/relay_v2/observability/otel.py::_aggregate_usage` — that is the
single source of truth for how usage is summed.

## Recently-touched components (post-MVP)

| File | Why it exists | Key invariant |
|---|---|---|
| `src/components/runs/PauseAnswerForm.vue` | Pause-for-review form (14a–14f). Reads `signal_args.review_paths` (plural, ADR-41) with fallback to legacy scalar `review_path`. | Resume disabled only while an active-tab Save is in flight — unsaved on non-active tabs surfaces as a soft warning but does NOT block Resume. |
| `src/components/files/DiffRender.vue` | Lazy diff2html entry for the 14e Preview/Diff toggle. | Dynamic-imports `diff2html` on first render — never eager. |
| `src/components/runs/UsageRow.vue` | Renders `harness_session_ended` events in the timeline (9g). | Pi-flavoured token names (see "Load-bearing invariants" §3). |
| `src/components/runs/ChildrenPane.vue` | Fanout children list on parent run detail (9e). | Revalidates on `subagent_dispatch` / `subagent_return` / `child_runs_resolved`. |
| `src/components/runs/ParentRunChip.vue` | Parent run link on child run detail (9e). | Renders only when `parent_run_id != null`. |

## Verify before commit

The composite gate is `npm run check`. When capturing the exit code,
**never pipe to `tail`** — `tail`'s exit code masks the real one
(local hazard that cost two days of red CI in 14c). Redirect to a
file and inspect:

```sh
npm run check > /tmp/check.log 2>&1; echo "EXIT=$?"
tail -8 /tmp/check.log
```
