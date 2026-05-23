// Vitest setup — wired via `test.setupFiles` in vite.config.ts.
//
// Pinia Colada queries that resolve to an `ApiError` (e.g. a 404 in a
// 14c "Create at this path" test) reject internally before any consumer
// has observed `query.error.value`. The rejected promise then fires the
// Node `unhandledRejection` event, which vitest treats as a test-run
// failure even though every assertion passed. We swallow ONLY
// ApiError-shaped rejections here — anything else is still a real
// unhandled rejection and will fail the run.
//
// Duck-typed (does not import from `@/lib/queries`): pulling `queries`
// here would force-load `@/api/client` before individual test files'
// `vi.mock('@/api/client', ...)` hoists, neutering their mocks. The
// check is intentionally narrow — name === 'ApiError' AND a numeric
// `status` — so any other unhandled rejection is still surfaced.
//
// Both jsdom (`window`) and Node (`process`) listeners are installed:
// vitest forwards Node-level unhandled rejections to its reporter, so
// the Node listener is the one that suppresses the CI-fail; the
// `window` listener is harmless redundancy for jsdom-internal events.
//
// Production code is unaffected: it reads `error.value` through Vue
// reactivity, which silences the rejection naturally. This setup file
// is test-environment-only.

interface ApiErrorLike {
  name: string
  status: number
}

function isApiErrorRejection(r: unknown): r is ApiErrorLike {
  return (
    typeof r === 'object'
    && r !== null
    && (r as { name?: unknown }).name === 'ApiError'
    && typeof (r as { status?: unknown }).status === 'number'
  )
}

if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (ev) => {
    if (isApiErrorRejection(ev.reason)) {
      ev.preventDefault()
    }
  })
}

if (typeof process !== 'undefined' && process.on != null) {
  process.on('unhandledRejection', (reason) => {
    if (isApiErrorRejection(reason)) {
      // Swallowed — see file header.
      return
    }
    // Re-throw on the next tick so vitest still surfaces real ones.
    throw reason
  })
}
