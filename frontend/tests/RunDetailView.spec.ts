import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import { createRouter, createMemoryHistory } from 'vue-router'

const GET = vi.fn()
const POST = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    GET: (...a: unknown[]) => GET(...a),
    POST: (...a: unknown[]) => POST(...a),
  },
}))

import RunDetailView from '../src/views/RunDetailView.vue'
import type { Run } from '../src/lib/queries'

// jsdom has no EventSource; stub a no-op so the LIVE path (running/
// paused) doesn't throw. The store↔wrapper contract itself is covered
// by events.store.spec.ts with a real injected fake.
class NoopEventSource {
  addEventListener(): void {}
  close(): void {}
}
beforeEach(() => {
  ;(globalThis as { EventSource?: unknown }).EventSource =
    NoopEventSource as unknown
})

// Router required for ParentRunChip's router-link to render as <a> tags.
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
  ],
})

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}

function makeDetail(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'run-1',
    project_id: 1,
    prompt_id: 7,
    prompt_body: 'do the thing',
    user_id: 1,
    status: 'running',
    started_at: '2026-05-19T10:00:00Z',
    ended_at: null,
    max_iters: 5,
    iter_timeout: 600,
    worktree_path: null,
    branch: null,
    parent_run_id: null,
    iters: [],
    ...over,
  }
}

// Keep `detail` as a convenience alias so existing tests stay unchanged.
const detail = makeDetail

function makeChildRow(
  overrides: Partial<{
    id: string
    status: string
    branch: string
    parent_run_id: string
  }> = {},
): Run {
  return {
    id: 'child-a',
    project_id: 1,
    prompt_id: null,
    prompt_body: 'x',
    user_id: 0,
    status: 'running',
    started_at: '2026-05-21T00:00:00Z',
    ended_at: null,
    max_iters: 1,
    iter_timeout: 60,
    worktree_path: '/wt/child-a',
    branch: 'relay/child-a',
    parent_run_id: 'parent-1',
    ...overrides,
  } as unknown as Run
}

/**
 * Mount RunDetailView.
 *
 * The caller is responsible for setting up `GET.mockImplementation`
 * (or `mockResolvedValue`) BEFORE calling `mountView` — exactly as the
 * existing tests already do.  `mountView` wraps that implementation to
 * additionally handle `/api/runs/{run_id}/children` (returning
 * `opts.children`, default `[]`) so existing tests don't need to care
 * about the children endpoint at all.
 */
function mountView(opts: { children?: Run[] } = {}): ReturnType<typeof mount> {
  const childRows = opts.children ?? []
  // Capture whatever mock the caller set up (may be mockImplementation
  // OR mockResolvedValue — both are accessible via GET.getMockImplementation).
  const callerImpl = GET.getMockImplementation()

  GET.mockImplementation((path: string, ...rest: unknown[]) => {
    if (path === '/api/runs/{run_id}/children') {
      return Promise.resolve(ok(childRows))
    }
    if (callerImpl) {
      return callerImpl(path, ...rest)
    }
    // Fallback: resolve undefined so the query doesn't hang.
    return Promise.resolve(ok(undefined))
  })

  return mount(RunDetailView, {
    props: { id: 'run-1' },
    global: {
      plugins: [createPinia(), PiniaColada, router],
      // ArtifactsPane mounts the shared FileTree (which fires its own
      // listing query); stub it here — its behaviour is covered by
      // ArtifactsPane.spec.ts. WorktreePane is light + network-free so
      // we let it render to assert the W7 wiring end-to-end.
      // ChildrenPane is stubbed — its internal router-links and
      // useRunChildrenQuery usage are covered by ChildrenPane.spec.ts.
      // ParentRunChip is NOT stubbed so we can assert the rendered <a>
      // href (router plugin above makes router-link resolve to <a>).
      stubs: {
        TimelinePane: true,
        PauseAnswerForm: true,
        ArtifactsPane: true,
        ChildrenPane: true,
      },
    },
  })
}

describe('RunDetailView', () => {
  beforeEach(() => {
    GET.mockReset()
    POST.mockReset()
  })

  it('renders header fields from run detail', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    const t = w.text()
    expect(t).toContain('Run run-1')
    expect(t).toContain('#7') // prompt id
    expect(t).toContain('0 / 5') // iters / max
    expect(w.find('.status-badge').text()).toBe('done')
  })

  it('Cancel button shown only when running and calls cancel mutation', async () => {
    GET.mockResolvedValue(ok(detail({ status: 'running' })))
    POST.mockResolvedValue(ok(detail({ status: 'cancelled' })))
    const w = mountView()
    await flushPromises()

    const btn = w.find('[data-testid="cancel-run"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    await flushPromises()
    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/cancel', {
      params: { path: { run_id: 'run-1' } },
    })
  })

  it('Cancel button hidden when not running', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="cancel-run"]').exists()).toBe(false)
  })

  it('PauseAnswerForm shown only when paused', async () => {
    GET.mockResolvedValue(ok(detail({ status: 'paused' })))
    const w = mountView()
    await flushPromises()
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(true)
  })

  it('W5 iters pane + W7 artifacts/worktree panes are mounted', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    // W5 slot hosts the real ItersPane; W7 slots now host the real
    // Artifacts/Worktree panes (no more placeholders).
    expect(w.find('[data-testid="iters-pane-slot"]').exists()).toBe(true)
    expect(w.find('[data-testid="iters-pane"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifacts-pane-slot"]').exists()).toBe(true)
    expect(w.findComponent({ name: 'ArtifactsPane' }).exists()).toBe(true)
    expect(w.find('[data-testid="worktree-pane-slot"]').exists()).toBe(true)
    expect(w.find('[data-testid="worktree-pane"]').exists()).toBe(true)
  })

  it('renders a collapsible Prompt block carrying the run prompt_body', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(detail({ status: 'done', prompt_body: 'do something useful' })),
        )
      return Promise.resolve(
        ok({ events: [], after_seq: 0, limit: 500, offset: 0 }),
      )
    })
    const w = mountView()
    await flushPromises()
    const det = w.find('details.run-detail__prompt')
    expect(det.exists()).toBe(true)
    expect(det.find('summary').text()).toBe('Prompt')
    expect(det.find('pre').text()).toBe('do something useful')
  })

  it('shows the latest assistant_text as the activity peek when present', async () => {
    // Terminal status uses REST replay (no SSE), so the event list is
    // populated synchronously from the events endpoint.
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(
        ok({
          events: [
            {
              id: 1,
              run_id: 'run-1',
              iter_id: 1,
              seq: 1,
              ts: '2026-05-24T22:00:00Z',
              kind: 'assistant_text',
              payload: { text: 'first thought' },
            },
            {
              id: 2,
              run_id: 'run-1',
              iter_id: 1,
              seq: 2,
              ts: '2026-05-24T22:00:01Z',
              kind: 'assistant_text',
              payload: { text: 'latest thought' },
            },
          ],
          after_seq: 2,
          limit: 500,
          offset: 0,
        }),
      )
    })
    const w = mountView()
    await flushPromises()
    const peek = w.find('[data-testid="latest-activity"]')
    expect(peek.exists()).toBe(true)
    expect(peek.text()).toContain('latest thought')
    expect(peek.text()).not.toContain('first thought')
  })

  it('hides the activity peek when no assistant_text has streamed', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(
        ok({ events: [], after_seq: 0, limit: 500, offset: 0 }),
      )
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="latest-activity"]').exists()).toBe(false)
  })

  it('shows a failure banner with a friendly explanation for agent_end_no_signal', async () => {
    // Field bug: a fresh "Hello, this is a test" prompt → pi replies
    // without emitting any [[engteam:...]] closing sentinel. The loop
    // correctly fails the run with exit_reason="agent_end_no_signal",
    // but the dashboard rendered no diagnostic — the user saw only a
    // failed status badge and a JSON-dumped run_ended boundary row.
    // The banner surfaces the reason inline with a "did you install
    // the engineering-team skill?" hint so the user knows why.
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(
            detail({
              status: 'failed',
              ended_at: '2026-05-21T00:19:28Z',
              iters: [
                {
                  id: 1,
                  run_id: 'run-1',
                  seq: 1,
                  phase: null,
                  prompt: 'Hello, this is a test',
                  preamble: '',
                  pi_session_id: 'abc',
                  signal_kind: null,
                  signal_args: null,
                  exit_reason: 'agent_end_no_signal',
                  started_at: '2026-05-21T00:19:23Z',
                  ended_at: '2026-05-21T00:19:28Z',
                },
              ],
            }),
          ),
        )
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    const banner = w.find('[data-testid="run-failure-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('agent_end_no_signal')
    // The friendly hint distinguishes "agent didn't emit a sentinel"
    // from a true crash — point the user at the skill workflow.
    expect(banner.text()).toMatch(/closing sentinel|engineering-team skill/i)
  })

  it('shows a failure banner with the marker error for a marker violation', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(
            detail({
              status: 'failed',
              ended_at: '2026-05-21T00:25:00Z',
              iters: [
                {
                  id: 1,
                  run_id: 'run-1',
                  seq: 1,
                  phase: null,
                  prompt: 'do it',
                  preamble: '',
                  pi_session_id: 'abc',
                  signal_kind: null,
                  signal_args: {
                    marker_error: 'missing prompt-start before handoff',
                  },
                  exit_reason: 'agent_end_no_signal',
                  started_at: '2026-05-21T00:24:00Z',
                  ended_at: '2026-05-21T00:25:00Z',
                },
              ],
            }),
          ),
        )
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    const banner = w.find('[data-testid="run-failure-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('missing prompt-start before handoff')
  })

  it('no failure banner on a successful run', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="run-failure-banner"]').exists()).toBe(false)
  })

  it('Worktree pane shows path + branch when present (read-only)', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(
            detail({
              status: 'done',
              worktree_path: '/srv/wt/run-1',
              branch: 'relay/run-1',
            }),
          ),
        )
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="worktree-path"]').text()).toBe(
      '/srv/wt/run-1',
    )
    expect(w.find('[data-testid="worktree-branch"]').text()).toBe(
      'relay/run-1',
    )
    expect(w.find('[data-testid="worktree-note"]').exists()).toBe(true)
  })

  // ── 9e: ChildrenPane + ParentRunChip + cascade-aware Cancel ──────────

  it('shows the Cancel button on awaiting_children with cascade copy', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(makeDetail({ status: 'awaiting_children' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView({
      children: [makeChildRow({ id: 'child-a' }), makeChildRow({ id: 'child-b' })],
    })
    await flushPromises()

    const btn = w.find('[data-testid="cancel-run"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('Cancel run and 2 children')
  })

  it('shows "Cancel run" (no cascade copy) when running with zero children', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(makeDetail({ status: 'running' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView({ children: [] })
    await flushPromises()

    const btn = w.find('[data-testid="cancel-run"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('Cancel run')
  })

  it('renders the Parent chip when detail.parent_run_id is set', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(makeDetail({ parent_run_id: 'parent-abc' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView({ children: [] })
    await flushPromises()

    const chip = w.find('[data-testid="parent-run-chip"]')
    expect(chip.exists()).toBe(true)
    expect(chip.attributes('href')).toBe('/runs/parent-abc')
  })

  it('omits the Parent chip on top-level runs', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(makeDetail({ parent_run_id: null })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = mountView({ children: [] })
    await flushPromises()

    expect(w.find('[data-testid="parent-run-chip"]').exists()).toBe(false)
  })
})
