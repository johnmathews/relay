import createClient from 'openapi-fetch'
import type { paths } from './schema'

// The generated OpenAPI `paths` already include the `/api` prefix
// (e.g. `/api/runs`, `/api/events/{run_id}`), so the effective request
// URL is `<origin>/api/...`. We resolve against the *current page
// origin* (not a hardcoded host): in dev that origin is the Vite dev
// server, whose proxy forwards `/api` to the backend (:7800); in prod
// the SPA is served same-origin by the backend, so it hits it directly.
// Using the origin (rather than an empty/relative base) keeps openapi-
// fetch's internal `new Request()` valid in every JS runtime (browser,
// jsdom, Node) since a bare relative URL has no base outside a browser
// document. `globalThis.location` is always present in the browser and
// in the jsdom test env; the `?? ''` guard is defensive for non-DOM
// runtimes (the dashboard only ever runs with a DOM).
export const API_BASE_URL = globalThis.location?.origin ?? ''

export const api = createClient<paths>({ baseUrl: API_BASE_URL })

export type Api = typeof api
