# 2026-05-24 — Vite dev-proxy stripped SSE `Content-Type`; live timeline silently broken

## Symptom

Operator report: "the UI is still not updating in real time. I have to
refresh the browser to see changes in the UI when a run is running."

A `running` run's `/runs/:id` page rendered an empty timeline; status
badge stayed frozen; refreshing the browser populated the timeline (via
REST replay) but new events never appeared live.

## Root cause

`frontend/vite.config.ts`'s `proxy.on('proxyRes', …)` hook for `/api`
called `res.flushHeaders()` from inside the listener. `http-proxy`
copies upstream response headers onto the downstream `res` *after* the
proxyRes listeners return — but `flushHeaders()` commits the response
head to the socket immediately. So the proxied SSE response went out
with the hook's manually-set headers (Cache-Control, X-Accel-Buffering,
Connection) but **no `Content-Type` at all**. The browser defaulted to
`text/plain`, and `EventSource` aborted the live connection with:

```
EventSource's response has a MIME type ("text/plain") that is not "text/event-stream"
```

Proof, side-by-side curls:

```
$ curl -sI http://localhost:5173/api/events/<id>     # via Vite proxy
HTTP/1.1 200 OK
Vary: Origin
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
Connection: keep-alive
(no Content-Type)

$ curl -sI http://127.0.0.1:7800/api/events/<id>     # backend direct
HTTP/1.1 200 OK
content-type: text/event-stream; charset=utf-8
…
```

The bug was invisible in tests because no test exercised the dev proxy,
and invisible in the daily flow because REST replay on browser refresh
masked the broken live path.

## Investigation path

1. Bug reported alongside a separate `agent_end_no_signal` symptom on
   run `20260524-203345-f27c` (worktree edits had landed; the run only
   failed because the bare prompt had no sentinel grammar — working as
   designed; documented in the response, no code change).
2. SSE wiring audit on paper looked correct: `KNOWN_EVENT_TYPES` matched
   the backend taxonomy; `INVALIDATING_KINDS` covered every lifecycle
   kind; the vite proxy had an explicit `text/event-stream` configure
   hook.
3. Backend SSE confirmed healthy via direct curl (`content-type:
   text/event-stream`).
4. Live-path verification: flipped the failed run to `status='running'`
   in SQLite, navigated to its detail page in Playwright with
   `data-event-count` instrumentation on `TimelinePane`. Result: 0
   events rendered, console error pinpointing the MIME mismatch.
5. Side-by-side curls confirmed the proxy was dropping `Content-Type`.

## Fix

Extracted the proxyRes hook into a pure helper
`frontend/dev/sse-proxy-headers.ts::mirrorSseHeaders` that:

1. Detects `text/event-stream` upstream.
2. Explicitly mirrors `Content-Type` onto the downstream `res` BEFORE
   `flushHeaders()`.
3. Sets the other SSE-friendly headers (Cache-Control: no-transform,
   X-Accel-Buffering: no, Connection: keep-alive) explicitly so the
   ordering is unambiguous and the regression test can assert it.

`vite.config.ts` now delegates to the helper.

## Regression test

`frontend/tests/sse-proxy-headers.spec.ts` (6 cases) records:

- For SSE upstream, `Content-Type` is mirrored verbatim.
- `setHeader('Content-Type', …)` runs strictly BEFORE `flushHeaders()`
  (call-order recorded with a mock-based array). Reverting the fix
  fails this case.
- Non-SSE upstream is passed through untouched.
- A response object without `flushHeaders` does not throw.

## Restored invariants / docs

`frontend/README.md` "Load-bearing post-MVP invariants" gains a section
3 explicitly naming the flush-after-mirror rule and pointing at the
regression test. The pre-existing section 3 (UsageRow pi-flavoured
tokens) is renumbered to 4.

## Verification

`cd frontend && npm run check` — green:
- lint clean
- typecheck clean (after adding `dev/**/*.ts` to both
  `tsconfig.app.json` and `tsconfig.node.json` includes)
- 192 tests pass (186 → +6 SSE-proxy regression)

Manual: re-flipped the run to `running`, reloaded the detail page —
timeline rendered all 31 historical events from the live SSE stream
with no console errors. Then restored the run to `failed` to leave the
DB clean.

## Out of scope (filed mentally, not opened)

- **Hub and Project list views never auto-update.** They use only
  `useRunsQuery` and subscribe to nothing live; a run finishing on a
  list page leaves a stale chip until navigation. The SSE-on-detail
  side-effect (`invalidate(['runs'])`) only helps when the operator is
  ALSO on the detail page. Wiring lifecycle invalidations into the
  list views is a separate feature.
- **`agent_end_no_signal` on bare ad-hoc prompts is a UX cliff.** A
  one-shot tiny prompt cannot satisfy the chained-iter contract; the
  agent does the work correctly but the run finalises as `failed`. A
  proposal for an "auto-done" or "ad-hoc one-shot" mode is a separate
  design conversation.
