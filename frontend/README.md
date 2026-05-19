# relay-v2 frontend

Vue 3 + TypeScript + Vite dashboard for relay-v2 — the Phase 4
control plane (complete): Hub, Project view (runs/prompts/files panes),
4-step New-Run wizard, Run detail with the live SSE timeline +
iters/artifacts/worktree panes, prompts CRUD, project register /
unregister.

This README is the **developer quick-start**. The operational
reference (architecture, state model, backend contract, mandates) is
`docs/dashboard.md`; canonical design is `docs/spec.md` §9.

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
