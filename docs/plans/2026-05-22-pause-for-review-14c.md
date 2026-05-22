# Plan — Phase 14c (pause-for-review: dashboard inline editor)

**Status:** ready to execute
**Date:** 2026-05-22
**Source proposal:** `docs/proposals/pause-for-review.md` (sub-phase 14c)
**Predecessors:** 14a (write endpoint + `artifact_edited` event —
shipped 2026-05-22, commit `dfefb87`); 14b (sentinel `review_path`
attribute — pending).
**Successor of:** 14d (skill template + journal-attested live
acceptance).
**Depends on:** 14a (the PUT endpoint exists in the OpenAPI doc and
returns hashes); 14b (paused iters carry `signal_args.review_path`).
Either order of merge works because 14c only **renders** `review_path`
when present and 14a's endpoint is callable without sentinel changes;
in practice land 14b first so the dashboard surfaces something useful
without manual SQL.

## Goal

Light up the operator UX. When a run is paused **and** its paused
iter's `signal_args` carries a `review_path`, the dashboard's
`PauseAnswerForm` switches into a richer mode: it fetches the
artifact, shows a textarea above the answer block, renders a live
markdown preview alongside, and offers a **Save** button that fires
`PUT /api/runs/:id/artifacts/{path}` to land an `artifact_edited`
event. The Resume button stays present and unchanged in shape —
disabled while a PUT is in flight (proposal §"Tradeoffs and risks"
choice (a)).

The same event also surfaces as a one-line row in the run timeline so
replay / live tailing both show *that* an edit happened (no diff in
v1 — that's 14e).

After 14c, the human-in-the-loop story is complete end-to-end: the
agent declares `review_path` (14b), the operator edits inline (14c),
the event store records every save with integrity hashes (14a), and
the resumed agent re-reads the file on the next iter (unchanged ADR-20
flow). 14d activates this for the primary caller (engineering-team
Phase 2).

## Locked decisions (from proposal alignment)

- **Editor is a plain `<textarea>`, not Monaco / CodeMirror.** ADR-26
  eager-bundle budget (~41 KB gz) is load-bearing. A richer editor is
  a future enhancement, never v1.
- **Markdown preview is render-only (existing markdown-it / shiki /
  mermaid pipeline from `ArtifactsPane.vue`).** No new heavy
  dependency.
- **Diff view (server-current vs dirty buffer) is deferred to 14e.**
  The three-state UI surface (saved-clean / dirty / saved-after-edit)
  adds complexity without v1 evidence it bites.
- **Conflict policy: last-write-wins** (ADR-12 single-user MVP).
  Two-tab edit races land both `artifact_edited` events in order; the
  loser sees stale until they refresh. Flag for multi-user.
- **PUT-during-resume race: disable Resume while a PUT is in flight**
  (proposal option (a)). Once `pause_resolved` lands the run flips
  `running`, the next PUT returns 409 `not_paused`, and the UI
  surfaces the 409 inline — the orchestrator does not need a grace
  window (proposal §refinement rejected).
- **OQ-3 missing file: "Create at this path" state.** When the GET
  for `review_path` returns 404, show an empty textarea + a Save
  button labelled "Create"; the first save creates the file (14a's
  endpoint already handles file-absent → create with `size_before=0`
  / `sha256_before=null`).
- **OQ-7 binary file: "binary; not editable inline" state.** When
  the GET returns 415, show the message + a download link (the
  existing `useArtifactDownloadHref` helper or equivalent).
- **OQ-6 timeline row: minimal in v1.** `TimelinePane.vue` renders
  one short line per `artifact_edited` event:
  `path · sha256-before → sha256-after · editor`. **No "view diff"
  link in v1** — the proposal calls that a stretch goal; ship the
  bare row.
- **`KNOWN_EVENT_TYPES` + `INVALIDATING_KINDS` updated in 14c, not
  earlier.** Adding them in 14a/14b would be dead code (no consumer
  yet); 14c is when the editor + timeline row first consume the
  event.

## What 14c does NOT do

- Does not change the Phase-2 skill template (14d).
- Does not change any backend file (no `src/relay_v2/...` edits).
- Does not change `compose_resume_prompt` (deferred per OQ-4).
- Does not add a diff view (proposal §OQ-5 → 14e).
- Does not add a "view diff" link on the timeline row (proposal
  §OQ-6 stretch → 14e).
- Does not add CodeMirror / Monaco (ADR-26).
- Does not change the SSE backend wiring — the new event kind
  already exists (added in 14a) and the SSE replay/cutover contract
  is unchanged.
- Does not add an MCP tool.

## File-by-file changes

### Generated client — `frontend/src/api/schema.d.ts`

**Regenerate from the running backend's `/openapi.json`** so the typed
client knows about `PUT /api/runs/{run_id}/artifacts/{file_path}`.
The 14a backend route is already wired; running

```
cd frontend && npm run gen:api
```

against a locally-running `relay serve` produces a new `schema.d.ts`
with the PUT operation. Commit the regenerated file alongside the
hand-written changes below. The diff should be small: one new entry
under `"/api/runs/{run_id}/artifacts/{file_path}"` for the `put`
operation + the request/response schemas.

(If `gen:api` is parameterised so it requires the backend bind port,
follow the existing `frontend/README.md` quick-start. No new tooling
needed.)

### SSE registry — `frontend/src/api/sse.ts`

Add `'artifact_edited'` to the `KNOWN_EVENT_TYPES` array (lines
85-102), placed adjacent to the other lifecycle entries (e.g. after
`'pause_resolved'` or before `'error'`). Reuse the existing inline
comment style — one trailing comment per new kind:

```ts
  'pause_requested',
  'pause_resolved',
  'artifact_edited', // ADR-40 (14a/14c)
  'error',
```

This is load-bearing per the post-9g bug-fix sweep (`KNOWN_EVENT_TYPES`
must explicitly list every kind the SSE wrapper subscribes to;
unlisted kinds appear only after a refresh that hits REST replay).

### Events store — `frontend/src/stores/events.ts`

Add `'artifact_edited'` to the `INVALIDATING_KINDS` set (lines
74-...). Place it near `'pause_resolved'`. The invalidation key the
store should arm is whatever key the artifact-content query uses
(`keys.artifactContent(runId, path)`) so the editor's loaded baseline
refreshes when an edit lands. A new top-level key
`['runs', 'artifact-edited', runId]` is **not** needed — the existing
content key is what the editor reads.

(Verify the existing `armInvalidation` predicate accepts the kind; if
it needs per-kind branching that flags specific cache keys, branch
artifact_edited to invalidate `keys.artifactContent` for the affected
path. The simplest viable shape: a broad invalidation for any pause-
related cache; refine if measurable churn shows up.)

### Mutation hook — `frontend/src/lib/queries.ts`

Add `useArtifactWriteMutation` next to `useArtifactContentQuery`
(near line 914). Signature mirrors other write mutations in the
file (e.g. `usePromptUpdateMutation` near line 551):

```ts
export function useArtifactWriteMutation() {
  return useMutation({
    mutation: async (args: {
      runId: string
      path: string
      content: string
      editor?: string
    }) =>
      unwrap(
        await api.PUT('/api/runs/{run_id}/artifacts/{file_path}', {
          params: {
            path: { run_id: args.runId, file_path: args.path },
          },
          body: { content: args.content, editor: args.editor },
        }),
      ) as { path: string; size: number; sha256: string },
    onSuccess: (_data, vars) => {
      // Invalidate the loaded baseline so subsequent reads see the
      // post-save sha256.
      queryCache.invalidateQueries({
        key: keys.artifactContent(vars.runId, vars.path),
      })
    },
  })
}
```

Errors propagate as `ApiError` carrying `status` (404 / 409 / 413 /
415 / 400 — see ADR-40) so the form can surface friendly inline
messages.

### Component — `frontend/src/components/runs/PauseAnswerForm.vue`

**Rebuild** the SFC. The minimal v1 form (35 lines of script today)
becomes a two-section form: an optional **review pane** at the top
(only when `signal_args.review_path` is present), and the existing
question / answer / submit block below — unchanged in shape.

Recommended split: **keep everything in `PauseAnswerForm.vue`** for
v1 — the editor section adds ~80 lines of script + ~60 lines of
template, well within the SFC's surface. Promote to a sibling
`PauseReviewPane.vue` ONLY if 14e's diff view or 14e's per-file
metadata grow the surface meaningfully. (Premature split for v1.)

Props (additive):

```ts
const props = defineProps<{
  runId: string
  question: string
  /** From the paused iter's signal_args; absent when the agent did
   *  not declare a reviewable artifact (14b). */
  reviewPath?: string | null
}>()
```

State (new):

- `loadedContent: Ref<string | null>` — server-current content,
  loaded once via `useArtifactContentQuery`.
- `loadedSha: Ref<string | null>` — server-current hash (for the
  "Saved" badge).
- `dirty: Ref<string>` — editor textarea bound via v-model.
- `saving: Ref<boolean>` — true between Save click and the PUT
  response; gates Resume disabled state.
- `saveError: Ref<string | null>` — inline error for the save action.
- `savedAt: Ref<string | null>` — `"Edited at HH:MM:SS"` badge after
  success.

Lifecycle:

- On mount **when `reviewPath` is present**, fire `useArtifactContentQuery
  (runId, reviewPath)`. On success: `loadedContent = data.content`,
  `loadedSha = sha256(data.content)` (or skip the per-load hash — use
  the post-save sha returned by the mutation as the source of truth),
  `dirty = data.content`.
- On 404 → enter "Create at this path" state: empty textarea, Save
  button enabled even when textarea is empty (so an explicit empty
  save creates the file). Show a short banner: "File not yet on
  disk. Saving will create it."
- On 415 → enter "Binary, not editable inline" state: hide the
  textarea, show a message + a download link
  (`useArtifactDownloadHref` or equivalent).
- On 400 / 413 / 5xx → show an inline error banner; keep the answer
  textarea functional so the operator can still resume without
  editing.

Save action:

- Click `Save` → call `useArtifactWriteMutation.mutateAsync({runId,
  path: reviewPath, content: dirty})`.
- During: `saving = true`; the Resume button is `disabled`.
- On success: `loadedContent = dirty` (so "dirty" tracking resets);
  `loadedSha = result.sha256`; `savedAt = new Date().toLocaleTimeString()`;
  `saving = false`. The query cache is invalidated by the mutation's
  `onSuccess` hook (so a parent component reading
  `useArtifactContentQuery` re-fetches).
- On error: `saveError = mapApiError(...)`; `saving = false`. The
  textarea content is preserved (operator's work is not lost).

Discard action:

- Click `Discard local changes` → `dirty = loadedContent ?? ''`;
  `saveError = null`. (Disabled when `dirty === loadedContent`.)

Resume action (existing):

- Submit button stays as today, **with `disabled` extended** to
  also disable when `saving === true`. The label
  ("Resume run") is unchanged.
- Inline error handling for the resume mutation is unchanged.

Template structure (sketch):

```vue
<template>
  <form class="pause-form" data-testid="pause-answer-form" @submit.prevent="onSubmit">
    <h3 class="pause-form__title">Run paused — answer to continue</h3>

    <!-- Review pane (14c): renders only when review_path is present. -->
    <section v-if="reviewPath" class="pause-review" data-testid="pause-review-pane">
      <header class="pause-review__header">
        <span class="pause-form__label">Reviewing</span>
        <code class="pause-review__path">{{ reviewPath }}</code>
        <span v-if="savedAt" class="pause-review__badge">Edited at {{ savedAt }}</span>
      </header>

      <div v-if="reviewState === 'binary'" class="pause-review__binary">
        This artifact is binary; not editable inline.
        <a :href="downloadHref">Download</a>
      </div>
      <div v-else-if="reviewState === '404'" class="pause-review__banner">
        File not yet on disk. Saving will create it.
      </div>

      <div v-if="reviewState !== 'binary'" class="pause-review__editor">
        <textarea
          v-model="dirty"
          class="pause-review__textarea"
          data-testid="pause-review-textarea"
          spellcheck="false"
          :disabled="saving"
        />
        <div class="pause-review__preview" v-html="rendered" />
      </div>

      <div v-if="reviewState !== 'binary'" class="pause-review__actions">
        <ActionButton
          type="button"
          :loading="saving"
          :disabled="saveDisabled"
          data-testid="pause-review-save"
          @click="onSave"
        >Save</ActionButton>
        <ActionButton
          type="button"
          :disabled="discardDisabled || saving"
          data-testid="pause-review-discard"
          variant="ghost"
          @click="onDiscard"
        >Discard local changes</ActionButton>
      </div>

      <p v-if="saveError" class="pause-form__error" data-testid="pause-review-error">
        {{ saveError }}
      </p>
    </section>

    <!-- Existing question / answer block, unchanged in shape. -->
    <span class="pause-form__label">Question</span>
    <pre class="pause-form__question" data-testid="pause-question">{{ question }}</pre>

    <label for="pause-answer" class="pause-form__label">Your answer</label>
    <textarea
      id="pause-answer"
      v-model="answer"
      class="pause-form__input"
      rows="5"
      data-testid="pause-answer-input"
    />

    <p v-if="inlineError" class="pause-form__error" role="alert" data-testid="pause-error">
      {{ inlineError }}
    </p>

    <ActionButton
      type="submit"
      :loading="resume.isLoading.value"
      :disabled="saving"
    >Resume run</ActionButton>
  </form>
</template>
```

The `rendered` computed is the same shape as `ArtifactsPane.vue` /
`FileViewer.vue` use: lazy import of markdown-it + shiki + mermaid.
Reuse the existing `lib/render.ts` helper rather than inlining
anything.

### View — `frontend/src/views/RunDetailView.vue`

Add **one prop** to the `<PauseAnswerForm>` instantiation (line 316
region):

```vue
<PauseAnswerForm
  :run-id="runId"
  :question="pauseQuestion"
  :review-path="pauseReviewPath"
  @resumed="onResumed"
/>
```

…where `pauseReviewPath` is a new computed mirroring `pauseQuestion`
(line 99):

```ts
const pauseReviewPath = computed(() => {
  const iters = detail.value?.iters ?? []
  for (const it of iters) {
    if (it.signal_kind === 'pause' && it.signal_args != null) {
      const rp = it.signal_args.review_path
      if (typeof rp === 'string' && rp !== '') return rp
    }
  }
  return null
})
```

No other RunDetailView changes; the rest of the view continues to
treat `paused` exactly as it does today.

### Timeline — `frontend/src/components/runs/TimelinePane.vue`

Add a render branch for `artifact_edited`, mirroring the
`harness_session_ended` branch at line 153 (which renders the
`<UsageRow>` SFC). One small inline row, no new SFC needed in v1:

```vue
} else if (ev.kind === 'artifact_edited') {
  // ADR-40 — show one-liner: path · sha-before → sha-after · editor.
  // No "view diff" link in v1 (proposal §OQ-6 → 14e).
}
```

…with a `<div class="timeline-row__edit" data-testid="artifact-edited-row">`
rendering:

```
✎ improvement-plan.md · a3f2… → 9b1e… · dashboard
```

(Shorthand each sha to its first 4 chars + ellipsis, matching how
short run-ids render elsewhere.)

If the inline render bloats `TimelinePane.vue` past readability,
extract an `ArtifactEditedRow.vue` SFC following the `UsageRow.vue`
pattern. v1 default: inline.

### Tests — `frontend/tests/PauseAnswerForm.spec.ts`

Extend the existing module (currently 4 tests at lines 1-74). Add a
**new describe block** `'PauseAnswerForm — review pane (14c)'`
covering:

1. **`review pane absent when review_path is null`** — mount with
   `reviewPath: null` (or omitted); `data-testid="pause-review-pane"`
   does not exist. The existing minimal form still renders.
2. **`review pane fetches and renders the artifact on mount`** —
   mock `api.GET` for `/api/runs/{run_id}/artifacts/{file_path}` to
   resolve `{content: "# Original\n", size: 11, sha256: "abc..."}`.
   Mount with `reviewPath: "plan.md"`. After `flushPromises`,
   `data-testid="pause-review-textarea"` value equals `"# Original\n"`;
   `data-testid="pause-review-pane"` exists.
3. **`Save fires PUT with the textarea content`** — typing edits the
   textarea, click Save, assert `api.PUT` called with
   `body: {content: "<edited>", editor: undefined}` (default editor
   is set server-side).
4. **`Save success shows "Edited at" badge and clears dirty`** — PUT
   resolves 200; assert the badge text matches `/Edited at \d/`.
5. **`Resume disabled while Save in flight`** — make the PUT resolve
   deferred; click Save; assert the submit button has `disabled`;
   resolve the PUT; assert the button re-enables.
6. **`404 on initial fetch → "Create at this path" banner`** — mock
   GET to reject with `ApiError(status: 404)`. The banner appears;
   Save with empty content fires PUT (creates the file).
7. **`415 on initial fetch → binary message + download link`** —
   mock GET to reject with `ApiError(status: 415)`. The textarea is
   NOT rendered; the download anchor is present.
8. **`Save 409 surfaces inline`** — PUT rejects with `ApiError(status:
   409, body.detail: "...path_mismatch...")`. The inline error shows
   the detail; the textarea content is preserved.
9. **`Discard reloads the loaded baseline`** — type to dirty the
   textarea; click Discard; textarea reverts to the initial fetched
   content.

Existing 4 tests must continue to pass (the no-`reviewPath` mount
shape is unchanged).

### Tests — `frontend/tests/TimelinePane.spec.ts`

If the existing module has a fixture-driven loop over event kinds,
add an `artifact_edited` case; assert `data-testid="artifact-edited-row"`
renders the path + sha-truncations. If no such module exists, add
**one focused test** (matching the `UsageRow` test from the post-9g
sweep): render TimelinePane with a single `artifact_edited` event in
its events array; assert the row's text contains the expected path
and short hashes.

### Tests — `frontend/tests/events-store` or equivalent

Add **one isolating regression** (matching the post-9g bug-fix sweep
pattern): an SSE event of kind `artifact_edited` fired alone (no
sibling events) triggers `INVALIDATING_KINDS` and refreshes the
artifact-content cache. The fanout-sweep test pattern from
`tests/stores/events.test.ts` (or whatever filename) is the model —
emit ONLY the kind under test so the assertion target cannot be a
sibling kind.

### Spec — `docs/spec.md`

**§9 (Dashboard, Pause / answer)** — add **one paragraph** describing
the review-pane mode. Mirror the existing §9 prose style (single
short paragraph + a "When …" sentence):

> When the paused iter's `signal_args.review_path` is present, the
> answer form renders an inline review pane above the question /
> answer block. It fetches the named artifact via
> `GET /api/runs/:id/artifacts/{path}`, renders a textarea (left)
> and the existing markdown / shiki / mermaid preview pipeline
> (right), and exposes a Save button that fires
> `PUT /api/runs/:id/artifacts/{path}` (ADR-40). The Resume button
> remains operational and is disabled only while a Save is in
> flight; the answer textarea is unaffected. The pane is absent (and
> the existing minimal form renders unchanged) when the paused iter
> does not declare a `review_path`.

(No backend-spec change. §3.2 (`artifact_edited` event kind) and §7
(PUT endpoint) were updated in 14a.)

## ADR — none

14c is the *UX implementation* of decisions A1/B1 already recorded in
ADR-40. No new ADR unless an open question gets answered with a
non-trivial trade-off (e.g. promoting the editor to CodeMirror,
adding a diff view in v1, splitting `PauseReviewPane.vue`). Default:
no new ADR.

## Verification

Frontend gate (must be green before commit):

- `cd frontend && npm run check` = `eslint --max-warnings 0` +
  `vue-tsc` + `vitest`. Expected pass count delta: **+9 PauseAnswerForm
  cases** + **1 TimelinePane case** + **1 events-store isolating case**
  = **172 frontend tests** (current 161 + 11 new). The 4 existing
  PauseAnswerForm tests continue to pass.
- Bundle size: `npm run build` then check the dist sizes. The eager
  bundle should stay ≤ 45 KB gz (ADR-26 budget ~41 KB gz today; the
  new code is pure script + template additions to an SFC already
  eager-loaded, so the delta should be small — ~2-3 KB gz).
- `schema.d.ts` regeneration: `cd frontend && npm run gen:api` against
  a locally-running `relay serve`. Commit the regenerated file.

Backend gate: untouched. `uv run pytest` / `ruff` / `mypy --strict`
must remain green; run them once to confirm no collateral breakage.

Manual smoke (journal-attest after the live engteam run in 14d, not
required for 14c merge):

- Start a paused run with `signal_args.review_path` set (via 14b's
  parser or a direct DB seed).
- Open the dashboard → run-detail → the review pane appears with the
  artifact content.
- Edit, click Save → "Edited at HH:MM:SS" badge appears;
  `artifact_edited` row lands in the timeline.
- Resume → run continues; the agent's next iter sees the edited
  file on disk.

## Acceptance criteria

- All 11 new vitest cases pass; existing 161 frontend tests pass.
- `npm run check` clean (eslint 0 warnings; vue-tsc 0 errors).
- `frontend/src/api/schema.d.ts` regenerated to include the PUT op.
- `frontend/src/api/sse.ts::KNOWN_EVENT_TYPES` includes
  `'artifact_edited'`.
- `frontend/src/stores/events.ts::INVALIDATING_KINDS` includes
  `'artifact_edited'`.
- `frontend/src/components/runs/PauseAnswerForm.vue` renders the
  review pane when `reviewPath` is present and the existing minimal
  form when absent.
- `frontend/src/components/runs/TimelinePane.vue` renders a one-line
  row for each `artifact_edited` event.
- `docs/spec.md` §9 gains a one-paragraph note on the review-pane
  mode.
- `CLAUDE.md` "Current state" gains a 14c paragraph in the post-9g /
  14a shape: dated, names the SFC + the new mutation hook + the SSE
  registry + timeline updates, references ADR-40, gives the test
  count delta, names what 14c does NOT do (scope fence).
- No backend file changed.

## Out of scope for 14c (recap)

- Sentinel grammar `review_path` parsing → **14b** (predecessor).
- Engineering-team Phase-2 template emits `review_path` → **14d**.
- Live engteam acceptance + journal entry → **14d**.
- Diff view in the editor (saved-clean / dirty / saved-after-edit
  three-state surface) → **14e** (proposal §OQ-5).
- "View diff" link on timeline rows → **14e** (proposal §OQ-6
  stretch).
- `compose_resume_prompt` annotation when edits happened → **14e**
  (deferred per OQ-4).
- CodeMirror / Monaco editor → never v1 (ADR-26 budget).

## Commit shape

One commit:

```
feat(frontend): pause-for-review inline editor + timeline row (14c)

- PauseAnswerForm.vue: review pane (textarea + markdown preview) when
  signal_args.review_path is present; Save fires PUT; Resume disables
  while PUT in flight; 404 → create state; 415 → binary state
- TimelinePane.vue: one-line row for artifact_edited
- KNOWN_EVENT_TYPES + INVALIDATING_KINDS + useArtifactWriteMutation
- Regenerated schema.d.ts for the 14a PUT route
- spec.md §9 names the review-pane mode

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Notes for the executing session

- **Run `npm run gen:api` early.** The typed `api.PUT` call depends
  on `schema.d.ts` including the new operation. Do this first so
  TypeScript errors don't pile up. The backend must be running for
  this; if `relay serve` is not up locally, start it (`uv run relay
  serve`) in another terminal.
- **The review pane is conditional on `reviewPath` being a non-empty
  string.** A paused run without `review_path` (every pre-14b run,
  and any 14b skill that omits the attribute) MUST render the
  existing minimal form unchanged. Test case (1) is the regression
  guard.
- **The Resume button stays present and labelled "Resume run".**
  Only its `disabled` attribute grows a new condition (`saving`);
  the label, position, and emit behaviour are unchanged. Operators
  who don't edit (just type "go" and submit) must see no UX change.
- **`useArtifactContentQuery` already exists** and is used by the
  artifacts pane. The new review pane should use the SAME hook —
  the cache key (`keys.artifactContent`) will be the same, and the
  invalidation in `useArtifactWriteMutation`'s `onSuccess` will hit
  it correctly.
- **Don't add new heavy deps.** No `diff2html` import in the
  template (it's already in the bundle, but instantiating a diff
  view eagerly bloats the pause path). No CodeMirror / Monaco.
  Plain `<textarea>` only.
- **The "Edited at HH:MM:SS" badge is client-side time** — it's a
  UX cue, not an audit timestamp. The audit is the event store's
  `events.ts` (the server-side timestamp). Don't fetch the badge
  from the server.
- **Last-write-wins is acceptable for v1.** Two tabs editing the
  same file both send PUTs; both `artifact_edited` events land; the
  loser sees stale until they refresh. ADR-12. The
  `useArtifactWriteMutation`'s `onSuccess` invalidates the cache,
  so the loser's next focus / refetch sees the actual state.
- **The timeline row is one-line, no link in v1.** Future 14e may
  add a "view diff" link that opens the artifact at the recorded
  hash. Don't pre-build the link.
- **CLAUDE.md update at the end** — same shape as the 14a paragraph.
  Don't skip; the walkthrough is the canonical orientation doc.
