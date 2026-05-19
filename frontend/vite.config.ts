/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import type { IncomingMessage, ServerResponse } from 'node:http'

// MANDATE 4 — SSE-safe dev proxy.
// The backend SSE endpoint (GET /api/events/{run_id}) is a long-lived
// text/event-stream. The default http-proxy timeout and Node's response
// buffering will stall the live tail in dev. We therefore:
//   - set an explicit long proxyTimeout (1 hour) so the stream is not
//     killed mid-run,
//   - in a configure(proxy) hook, on proxyRes detect
//     content-type: text/event-stream and disable buffering
//     (flushHeaders + Connection: keep-alive, no compression/transform).
// Target defaults to the runtime default 127.0.0.1:7800 and is
// overridable via VITE_API_PROXY_TARGET.
const PROXY_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:7800'
const ONE_HOUR_MS = 1000 * 60 * 60

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: PROXY_TARGET,
        changeOrigin: true,
        // Long timeouts so a multi-hour run's SSE stream is not severed.
        timeout: ONE_HOUR_MS,
        proxyTimeout: ONE_HOUR_MS,
        configure: (proxy) => {
          proxy.on(
            'proxyRes',
            (
              proxyRes: IncomingMessage,
              _req: IncomingMessage,
              res: ServerResponse,
            ) => {
              const ct = proxyRes.headers['content-type'] ?? ''
              if (ct.includes('text/event-stream')) {
                // Disable any buffering / transform of the SSE body so
                // events reach the browser as they are produced.
                proxyRes.headers['cache-control'] = 'no-cache, no-transform'
                proxyRes.headers['x-accel-buffering'] = 'no'
                res.setHeader('Cache-Control', 'no-cache, no-transform')
                res.setHeader('X-Accel-Buffering', 'no')
                res.setHeader('Connection', 'keep-alive')
                // Flush headers immediately; do not wait for the body to
                // fill a buffer before the client sees the response.
                if (typeof res.flushHeaders === 'function') {
                  res.flushHeaders()
                }
              }
            },
          )
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/**/*.spec.ts'],
    coverage: {
      // MANDATE 5 — "vitest v4 changed the coverage.all default; set it
      // explicitly". Reality in vitest 4.1.6 (verified against the
      // installed `CoverageOptions` type): the `all` *toggle has been
      // removed entirely*. v4 unconditionally counts every file matched
      // by `include` (the old `all: true` behavior is now the only
      // behavior — no opt-out). Passing `all: true` is now a TS error
      // ("'all' does not exist in type 'CoverageOptions'"). We therefore
      // satisfy the mandate's intent — untested source files DO count —
      // by explicitly scoping `include` to all of `src/**` and excluding
      // only generated/config/test files. (Flagged for the lead to
      // record as an ADR/spec note: the mandate's literal `all: true`
      // is unimplementable on vitest v4; the intent is preserved.)
      provider: 'v8',
      include: ['src/**/*.{ts,vue}'],
      exclude: [
        'src/api/schema.d.ts',
        'src/**/*.d.ts',
        '**/*.config.*',
        'tests/**',
      ],
    },
  },
})
