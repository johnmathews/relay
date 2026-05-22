# Post-9f bug fixes — three independent regressions

**Date:** 2026-05-23
**Branch:** `main` (3 commits ahead of c44d762 at the start of this session)

Three orthogonal bugs filed in
[`journal/260522-9f-langfuse-acceptance.md`](260522-9f-langfuse-acceptance.md)
"Side observations" — all closed in this session as separate
commit chains.

## Bug 3 — UsageRow.vue sums to zero

**Symptom.** The new ADR-39 `harness_session_ended` timeline row
rendered `CLEAN Σ in 0 · out 0 · cache 0` even though Langfuse showed
real `gen_ai.usage.input_tokens=3`, `output_tokens=335`,
`relay.usage.cache_write_tokens=19072` attributes on the same iter's
`relay.iter` span.

**Diagnosis.** One `sqlite3 ... select payload from events where kind='harness_session_ended'`
against the acceptance-run DB showed pi's actual
`SessionEnded.messages[].usage` shape:

```json
{ "input": 3, "output": 335, "cacheRead": 0, "cacheWrite": 19072,
  "totalTokens": 19410, "cost": { "total": 0.076554, ... } }
```

— pi-flavoured keys, NOT Anthropic-API standard
`input_tokens`/`output_tokens`/`cache_read_input_tokens`. The SFC was
summing the Anthropic names (none of which exist in the payload), so
every term was zero. The OTel side (`observability/otel.py::_aggregate_usage`)
already reads the right names — there was one source of truth for the
shape, and the SFC didn't match it.

A secondary bug: the SFC summed across ALL messages (no role filter),
while OTel correctly filters to `role == "assistant"` (only assistant
messages carry a `usage` block).

**Fix.** `frontend/src/components/runs/UsageRow.vue` now reads
`input`/`output`/`cacheRead`/`cacheWrite` and filters to assistant
messages — byte-for-byte mirroring `_aggregate_usage`. The row now
shows the full picture: `Σ in 3 · out 335 · cache r 0 / w 19072 · $0.0766`
(input/output/cache-read/cache-write/cost), matching what's in
Langfuse.

**Tests.** `frontend/tests/UsageRow.spec.ts` rebuilt against a
real-world fixture dumped from the acceptance-run DB (the prior
synthetic fixture happened to mirror the SFC's wrong field names, so
the bug went undetected). `TimelinePane.spec.ts`'s
`harness_session_ended` case was also re-baselined to the pi shape.
Three SFC tests + 1 TimelinePane test now exercise the real payload
shape.

**Scope.** Frontend SFC + test fixture only; the backend payload
(ADR-18: messages opaque) is unchanged.

**Commit:** `b227297`.

## Bug 1 — Worktree provisioned under the wrong project root

**Symptom.** A run started against a project registered at
`/Users/john/projects/relay/relay-fanout-test` had its worktree
provisioned at
`/Users/john/projects/relay/relay-v2/.relay/worktrees/20260522-180504-355f`
— relay-v2's tree, not the scratch project's.

**Diagnosis.** Query against `.relay/relay.db` confirmed:

```
projects: id=3 root_path=/Users/john/projects/relay/relay-fanout-test
runs:    id=20260522-180504-355f project_id=3 worktree_path=/Users/john/projects/relay/relay-v2/.relay/worktrees/...
```

So `project_id` was correctly `3`, the project's `root_path` was
correctly the scratch dir — but the worktree landed under relay-v2.
The culprit: `provision_workspace(project_root, data_dir, run_id)`
was being called by `core.py` with `data_dir = self._settings.data_dir`
— the relay-global SQLite root (`<cwd>/.relay`). Spec.md §3.3 is
explicit that worktrees + run-artifacts dirs live under
`<project_root>/.relay/`, not under the server-global data dir.

The relay-global `data_dir` is the right home for the single
multi-tenant SQLite event store (`relay.db`, ADR-12) but **not** for
per-run filesystem state.

**Fix.** Drop `data_dir` from `provision_workspace`'s signature; it
now resolves the workspace under
`project_data_dir(project_root) = project_root / ".relay"`. A new
`RelayCore.get_run_artifacts_dir(run_id)` is the single resolver the
read paths (`api/artifacts.py`, MCP `relay__read_artifact`,
`core.py`'s resume / preview run_dir derivations) call — they no
longer reach into `settings.data_dir`. `spec.md` §11.1's
`RELAY_DATA_DIR` description is clarified to call out that worktrees
& artifacts are per-project, not under this dir.

Existing runs whose `worktree_path` rows still point at the old
relay-global location will 404 through the new artifacts/MCP routes —
acceptable for the single-user MVP; on-disk files are not touched.

**Tests.** New `tests/orchestrator/test_project_data_dir.py` — 2
integration tests register a project at path A (with `data_dir` at a
**different** path B to mirror the live repro) and assert both
`worktree_path` and `get_run_artifacts_dir(run_id)` land under A,
not B. Existing `test_lifecycle.py`, `test_lifecycle_child_worktree.py`,
`test_artifacts.py`, `test_mcp_tools.py` updated for the new
`provision_workspace` signature + the new `get_run_artifacts_dir`
core method (the artifacts stub no longer needs a settings object).

**Scope.** Backend; no schema change, no new event kinds, no ADR
(the spec §3.3 layout was already canonical — the implementation just
diverged from it).

**Commit:** `1409a5f`.

## Bug 2 — SSE didn't stream live (required browser refresh)

**Symptom.** The dashboard's run-detail view showed `No events yet`
+ `1/3 iters RUNNING` and stayed there. After a browser refresh the
events appeared (REST replay path on the now-terminal run).

**Diagnosis.** ADR-23 promises no-gap-no-dup live tailing. The
backend SSE path (subscribe-replay-cutover) is well-tested. The bug
is on the **frontend** side: `frontend/src/api/sse.ts`'s
`KNOWN_EVENT_TYPES` allowlist was missing two recently-added kinds:

- `harness_session_ended` — added in ADR-39 (Phase 9g, this week)
- `child_runs_resolved`   — added in Phase 9a (last week)

The W1 wrapper registers an `EventSource.addEventListener(<kind>, ...)`
for each name in this list. The browser's EventSource only fires
listeners for **explicitly registered** event names — a named event
with `event: <unknown-kind>` is silently dropped (it does NOT fall
through to the generic `message` handler). So live events of those
two kinds were dropped client-side. Other kinds (`iter_started`,
`assistant_text`, etc.) still flowed.

The pre-existing test for `child_runs_resolved` delivery
(`events.store.spec.ts::also invalidates on subagent_return and
child_runs_resolved`) falsely passed because the fixture also emitted
`subagent_return` in the same case, and `subagent_return` alone
triggered the same invalidation key the assertion checked — so the
`child_runs_resolved` half of the test never actually exercised the
wrapper.

Whether this fully explains the user-visible "No events yet" is
ambiguous — at minimum, `harness_session_ended` (emitted on every
iter close) and `child_runs_resolved` (emitted on the fanout join)
were both dropping; the rest of the taxonomy was flowing. Refresh on
a now-terminal run skipped SSE entirely and hit the REST replay path,
which surfaces every kind from the DB — masking the drop.

**Fix.** `frontend/src/api/sse.ts`'s `KNOWN_EVENT_TYPES` now includes
both kinds, with an inline comment that ties the list to spec §3.2
and names the `grep` command that catalogs every kind the backend can
emit. Future kind additions that forget this file should fail review.

**Tests.** Two new isolating cases in `events.store.spec.ts`:
`delivers child_runs_resolved live` and `delivers
harness_session_ended live`. Each emits ONLY the kind under test on
the fake EventSource and asserts the event reaches `store.events`
(the timeline source) — not just that an invalidation fired.

**Scope.** Frontend; no backend change, no ADR (ADR-23's contract is
unchanged — restored for the two kinds that had silently slipped out
of the wrapper's view).

**Commit:** `56daaa3`.

## Result

All three regressions closed with isolated commits, each backed by
a failing-then-green test. Full backend gate: 298 passed, 3 pi-e2e
gated (ruff + mypy --strict clean, 39 source files, backend coverage
94%). Full frontend gate: 161 passed across 27 files (eslint
`--max-warnings 0` + vue-tsc + vitest, clean).
