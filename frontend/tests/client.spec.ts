import { describe, it, expect, vi, afterEach } from 'vitest'
import { api, API_BASE_URL } from '../src/api/client'

describe('api client', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses the page origin so /api paths resolve same-origin', () => {
    // Generated OpenAPI paths already include the `/api` prefix; the
    // client base is the page origin so the effective URL is
    // `<origin>/api/...` (dev: proxied; prod: same-origin backend).
    expect(API_BASE_URL).toBe(globalThis.location.origin)
    expect(API_BASE_URL).not.toContain('/api')
  })

  it('round-trips a typed GET /api/runs through a mocked fetch', async () => {
    // openapi-fetch binds `globalThis.fetch` at createClient() time, so
    // we inject the mock per-request via the `fetch` init option (this
    // is also how callers can swap transport).
    const fetchMock = vi.fn(
      async (req: Request): Promise<Response> => {
        expect(req.url).toBe(`${globalThis.location.origin}/api/runs`)
        return new Response(
          JSON.stringify({ runs: [], next_offset: null }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      },
    )

    const { data, error } = await api.GET('/api/runs', {
      fetch: fetchMock as unknown as typeof fetch,
    })

    expect(error).toBeUndefined()
    expect(data).toBeDefined()
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})
