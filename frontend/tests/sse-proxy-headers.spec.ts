// Regression test for the Vite dev-proxy SSE Content-Type bug.
//
// The bug: in vite.config.ts's `proxy.on('proxyRes', ...)` hook the
// helper called `res.flushHeaders()` BEFORE explicitly mirroring
// `proxyRes.headers['content-type']` onto `res`. http-proxy normally
// copies upstream headers to the downstream response after the hook
// returns, but `flushHeaders()` commits the response head to the
// socket immediately — so `Content-Type: text/event-stream` never
// landed, the browser defaulted to `text/plain`, and EventSource
// aborted the live connection with a MIME-type error. The dashboard's
// run-detail timeline stayed empty until a browser refresh (which uses
// REST replay, not SSE).
//
// What we lock in:
//   1. For an SSE upstream response, `Content-Type` IS set on `res`.
//   2. `setHeader('Content-Type', …)` is called BEFORE `flushHeaders()`.
//      A regression that re-introduces the wrong order (or drops the
//      explicit mirror) fails this assertion.
//   3. Non-SSE upstream responses are passed through untouched.

import { describe, it, expect, vi } from 'vitest'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { mirrorSseHeaders } from '../dev/sse-proxy-headers'

function makeProxyRes(contentType: string | undefined): IncomingMessage {
  return {
    headers: contentType != null ? { 'content-type': contentType } : {},
  } as unknown as IncomingMessage
}

interface RecordingRes {
  res: ServerResponse
  calls: Array<['setHeader', string, string] | ['flushHeaders']>
  headers: Map<string, string>
}

function makeRes(): RecordingRes {
  const calls: RecordingRes['calls'] = []
  const headers = new Map<string, string>()
  const res = {
    setHeader: vi.fn((name: string, value: string) => {
      calls.push(['setHeader', name, value])
      headers.set(name.toLowerCase(), value)
    }),
    flushHeaders: vi.fn(() => {
      calls.push(['flushHeaders'])
    }),
  } as unknown as ServerResponse
  return { res, calls, headers }
}

describe('mirrorSseHeaders', () => {
  it('mirrors upstream Content-Type onto the downstream response for SSE', () => {
    const proxyRes = makeProxyRes('text/event-stream; charset=utf-8')
    const { res, headers } = makeRes()

    mirrorSseHeaders(proxyRes, res)

    expect(headers.get('content-type')).toBe(
      'text/event-stream; charset=utf-8',
    )
    expect(headers.get('cache-control')).toBe('no-cache, no-transform')
    expect(headers.get('x-accel-buffering')).toBe('no')
    expect(headers.get('connection')).toBe('keep-alive')
  })

  it('sets Content-Type BEFORE flushHeaders (the load-bearing order)', () => {
    const proxyRes = makeProxyRes('text/event-stream')
    const { res, calls } = makeRes()

    mirrorSseHeaders(proxyRes, res)

    const ctIdx = calls.findIndex(
      (c) => c[0] === 'setHeader' && c[1] === 'Content-Type',
    )
    const flushIdx = calls.findIndex((c) => c[0] === 'flushHeaders')
    expect(ctIdx).toBeGreaterThanOrEqual(0)
    expect(flushIdx).toBeGreaterThanOrEqual(0)
    // Without this ordering, the response is committed without
    // Content-Type and the browser aborts the EventSource.
    expect(ctIdx).toBeLessThan(flushIdx)
  })

  it('also rewrites proxyRes.headers so downstream caches stay sane', () => {
    const proxyRes = makeProxyRes('text/event-stream')

    mirrorSseHeaders(proxyRes, makeRes().res)

    expect(proxyRes.headers['cache-control']).toBe('no-cache, no-transform')
    expect(proxyRes.headers['x-accel-buffering']).toBe('no')
  })

  it('is a no-op for non-SSE upstream responses', () => {
    const proxyRes = makeProxyRes('application/json')
    const { res, calls, headers } = makeRes()

    mirrorSseHeaders(proxyRes, res)

    expect(calls).toEqual([])
    expect(headers.size).toBe(0)
    expect(proxyRes.headers['cache-control']).toBeUndefined()
  })

  it('is a no-op when upstream has no Content-Type at all', () => {
    const proxyRes = makeProxyRes(undefined)
    const { res, calls } = makeRes()

    mirrorSseHeaders(proxyRes, res)

    expect(calls).toEqual([])
  })

  it('tolerates a response with no flushHeaders method', () => {
    const proxyRes = makeProxyRes('text/event-stream')
    const headers = new Map<string, string>()
    const res = {
      setHeader: (name: string, value: string) => {
        headers.set(name.toLowerCase(), value)
      },
      // flushHeaders intentionally omitted
    } as unknown as ServerResponse

    expect(() => mirrorSseHeaders(proxyRes, res)).not.toThrow()
    expect(headers.get('content-type')).toBe('text/event-stream')
  })
})
