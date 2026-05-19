# relay-v2 frontend

Vue 3 + TypeScript + Vite dashboard for relay-v2. This is the Phase 4
vertical-slice foundation (W1): scaffold, typed API client, SSE wrapper,
and the lint/typecheck/test gate. Views are placeholders filled by later
units (W2–W8).

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
   contract documented in `src/lib/render.ts` (implemented in W6).
3. **mermaid**: dynamic `import('mermaid')` on first diagram render
   only, never a static top-level import — see `src/lib/render.ts`.
4. **Vite SSE dev-proxy**: `/api` proxy uses a 1h `proxyTimeout` and a
   `configure(proxy)` hook that disables buffering for
   `text/event-stream` so the live tail does not stall —
   `vite.config.ts`.
5. **vitest v4 `coverage.all`** is set explicitly (`true`, v8 provider)
   in `vite.config.ts` since v4 changed the default.
