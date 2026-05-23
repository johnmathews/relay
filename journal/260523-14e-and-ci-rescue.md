# 14e shipped + CI rescue (frontend unhandled rejection + GHCR rename)

**Date:** 2026-05-23
**Commits:** `4fcb711` (14e), `a9813f1` (frontend setup file), `14935fe` (GHCR rename)
**Plan:** [docs/plans/2026-05-23-pause-for-review-14e.md](../docs/plans/2026-05-23-pause-for-review-14e.md)

## What 14e shipped

The "audit polish" bundle for pause-for-review (no contract change):

- **Diff toggle** in `PauseAnswerForm.vue`'s right pane. `[ Preview | Diff ]`
  segmented control above the existing editor. Diff disabled while the
  textarea is byte-equal to the loaded baseline; on dirty it lazy-loads
  `diff2html` via the existing `DiffRender.vue` entry (no new bundle
  weight). A successful Save + a Discard both return to clean and snap
  the right pane back to Preview. The 415 binary state renders no
  toggle at all. Driven by a `viewMode: 'preview' | 'diff'` ref + a
  `watch(isDirty, …)` that forces `'preview'` when clean.
- **Clickable `artifact_edited` timeline rows.** The row tag changed
  from `<div>` to `<button>`; the click handler calls
  `useBrowserUiStore('run:'+runId).selectFile(payload.path)` and
  `scrollIntoView()` on the artifacts pane. Honest navigation, not a
  historical diff (ADR-40 §B1 deliberately does not preserve
  before-content). `TimelinePane` gained an optional `runId` prop;
  `RunDetailView` passes `detail.id`.
- **OTel pause attribute** `relay.pause.artifacts_edited_count` on the
  resumed iter's `relay.iter` span. Plumbing:
  `RunContext.paused_predecessor_iter_id` (new field) → set by
  `resume_run` to `paused.id` → `run_loop` issues one
  `SELECT COUNT(*)` against `events.iter_id == :paused_iter_id AND
  kind == 'artifact_edited'` *before* the loop body, consumed once on
  the resumed iter's `iter_span(..., pause_artifacts_edited_count=)`
  call. NOOP `Instrumentation` accepts and ignores the new kwarg
  (same shape as the 9f `parent_iter_ctx` kwarg).
- **Fanout phase-2 cross-link.** Blockquote in
  `skills/engineering-team/pi/phases/phase-2-planning.md` pointing at
  `../references/fanout.md`. Closes the deferred 9e follow-up; the
  CLAUDE.md "deferred fanout-docs" line in the 9e block is removed.

Tests: 4 new backend (`tests/observability/test_otel_pause_attr.py` —
0/1/3-edit + NOOP cases), 7 new frontend (5 PauseAnswerForm
Diff-toggle + 2 TimelinePane click-target). All gates green locally
before push.

The single test-double in `tests/orchestrator/test_relay_core.py`'s
`_RecordingRunSpan` needed the new kwarg added — flagged here because
it's easy to miss when changing a protocol signature.

## CI rescue — two unrelated red herrings

After pushing 14e, CI was red. Two distinct failures, neither
introduced by 14e:

### Red herring 1 — frontend unhandled rejection (pre-existing from 14c)

The 14c `PauseAnswerForm.spec.ts` case "404 on initial fetch →
'Create at this path' banner" sets up a Pinia Colada query that
rejects with `ApiError(404)`. Colada captures it into
`query.error.value`, but the SFC's `loadError` computed only reads
that value lazily on the next render tick — by which point the
rejected promise has already fired Node's `unhandledRejection`. Vitest
forwards that to its reporter and exits 1 even though all 180 tests
pass.

Why this hadn't been caught locally: `npm run check 2>&1 | tail …` in
the executing session pipes the output to `tail`, which means `$?`
captures `tail`'s exit code (0), not `npm`'s (1). CLAUDE.md's 14c
paragraph claimed "the gate exits 0" — that claim was wrong; it's
only true when the exit code isn't piped away. CI ran straight, no
pipe, exit code 1. This had been red on `main` since 14c (commit
c2234ae); subsequent pushes 14d / 14e all showed the same failure.

**Fix:** `frontend/tests/setup.ts` (wired via vitest `setupFiles`)
installs a narrow `unhandledRejection` listener on both `process` and
`window` that swallows ONLY rejections matching
`{ name: 'ApiError', status: <number> }`. Anything else still
surfaces. Duck-typed (no `import` of `@/lib/queries` — that would
force-load `@/api/client` before per-test `vi.mock('@/api/client',
…)` could hoist, breaking ~14 unrelated tests; learned this the hard
way on the first attempt).

Production code untouched — the rejection only goes unhandled in the
test env because `mount()`'s template doesn't sync-read `error.value`
fast enough.

### Red herring 2 — GHCR docker push 401 (latent since the repo rename)

With the frontend fix in, the gate passed for the first time in days
— and the docker job ran for the first time since 14b
(2026-05-22 21:57Z). It immediately 401'd:

```
ERROR: failed to build: unauthorized: unauthenticated:
User cannot be authenticated with the token provided.
```

Root cause: the GitHub repo was renamed `relay-v2 → relay` (the
`github.com/johnmathews/relay-v2` URL now 404s; remote is
`github.com/johnmathews/relay`). The workflow still pushed to
`ghcr.io/johnmathews/relay-v2` — a package that exists on GHCR
(last image at 14b, digest `05948b06…`) but whose "linked
repository" went stale after the rename. `GITHUB_TOKEN` from
`johnmathews/relay` no longer has automatic `packages:write` on the
old-name package, even with `permissions: packages: write` in the
workflow.

This had actually been broken since the rename, but the `docker` job
has `needs: gate` — so every `gate`-failed push (14d / 14e /
fix(frontend) attempt 1) short-circuited before docker ever ran.
The 401 surfaced only once the gate was fixed.

**Fix:** rename the workflow tag to `ghcr.io/johnmathews/relay`
(matches the user's global Docker/CI policy
`ghcr.io/johnmathews/<repo-name>`). Updated 7 files
(`.github/workflows/ci.yml`, `README.md`,
`docker-compose.example.yml`, `CLAUDE.md` ×2, `docs/spec.md`,
`docs/plan.md`, `docs/getting-started.md` ×2). The stale `relay-v2`
package on GHCR is now an orphaned artifact; manual web-UI cleanup
is a separate housekeeping task.

Deliberately NOT touched (historical policies):
- `docs/decisions.md` ADR-30 — ADRs are append-only.
- `journal/260519-phase-{3,8}-*.md` — journal entries are dated history.

Final CI run (`14935fe`): **gate ✓ 1m46s, docker ✓ 2m13s, total
4m07s**. First successful CI on `main` since 14b.

## Lessons / what to watch for

- **`needs: gate` masks downstream-job failures.** Any persistent
  gate failure can hide a stale docker / publish / deploy step
  indefinitely. Worth a periodic `workflow_dispatch` run that skips
  the gate, or splitting docker so it runs on the same job as the
  gate's last step.
- **Don't pipe `npm run check` to `tail` when capturing exit codes.**
  `tail` swallows the real exit. Use `npm run check > /tmp/log 2>&1;
  echo $?` instead.
- **A GitHub repo rename does NOT migrate GHCR package linkage.**
  Existing packages keep their old names and remain linked to the
  old (now-deleted) repo, breaking automatic `GITHUB_TOKEN`
  `packages:write` from the renamed repo. Either rename the tag in
  the workflow (preferred per the user's policy) or re-link the
  package via the GHCR web UI.
- **Pinia Colada query rejections + jsdom + vitest = unhandled
  rejection in CI.** The pattern is "the SFC's computed reads
  `error.value` only on render, which happens after the
  microtask-queue tick where the rejection lands." A test setup file
  that suppresses only `ApiError`-shaped rejections is the
  least-invasive fix; the production code path is fine because Vue
  reactivity sets up the observation eagerly when the component is
  actually rendered in a browser.
