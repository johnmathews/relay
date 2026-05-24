// Dev-server SSE header mirror for the Vite `/api` proxy.
//
// Why a separate module: the `proxy.on('proxyRes', ...)` hook inside
// `vite.config.ts` was simple enough to inline but contained a subtle
// ordering bug that silently broke live SSE — see
// `tests/sse-proxy-headers.spec.ts` for the regression. Extracting the
// pure function makes the contract testable in isolation without
// needing to spin up the real dev server.
//
// The bug: calling `res.flushHeaders()` inside the proxyRes handler
// commits the downstream response to the socket BEFORE http-proxy has
// copied the upstream response headers (notably `Content-Type:
// text/event-stream`) onto `res`. The browser then sees the SSE body
// served as `text/plain`, and `EventSource` aborts the connection with
// `EventSource's response has a MIME type ("text/plain") that is not
// "text/event-stream"`. The run-detail timeline stays empty until a
// browser refresh (which uses REST replay, not SSE).
//
// The fix: explicitly mirror `proxyRes.headers['content-type']` onto
// `res` BEFORE calling `flushHeaders()`. Other headers we want
// (Cache-Control, X-Accel-Buffering, Connection) are also set
// explicitly so the order is unambiguous and the test can assert it.

import type { IncomingMessage, ServerResponse } from 'node:http'

/**
 * Mirror upstream SSE headers onto the proxied downstream response,
 * disable buffering, then flush. No-op for non-SSE upstream responses.
 *
 * **Call order is load-bearing.** `Content-Type` MUST be set on `res`
 * before `flushHeaders()` is called, or the browser sees `text/plain`
 * and EventSource aborts. The regression test in
 * `tests/sse-proxy-headers.spec.ts` records this order.
 */
export function mirrorSseHeaders(
  proxyRes: IncomingMessage,
  res: ServerResponse,
): void {
  const ct = proxyRes.headers['content-type'] ?? ''
  if (!ct.includes('text/event-stream')) return

  // Tell upstream/downstream caches not to transform the SSE body.
  proxyRes.headers['cache-control'] = 'no-cache, no-transform'
  proxyRes.headers['x-accel-buffering'] = 'no'

  // Mirror the upstream content-type ONTO the downstream response.
  // Without this, calling flushHeaders below commits the response with
  // no Content-Type and the browser falls back to text/plain.
  res.setHeader('Content-Type', ct)
  res.setHeader('Cache-Control', 'no-cache, no-transform')
  res.setHeader('X-Accel-Buffering', 'no')
  res.setHeader('Connection', 'keep-alive')

  // Flush LAST so all the mirrored headers are part of the committed
  // response head.
  if (typeof res.flushHeaders === 'function') {
    res.flushHeaders()
  }
}
