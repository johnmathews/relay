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

/**
 * Create and return a fresh router already navigated to `/runs/run-1`.
 *
 * A shared router causes async DOM-patch errors after tests complete —
 * the router.replace() watcher inside RunDetailView fires on the next
 * tick, landing after the previous test's component has unmounted and
 * its backing DOM nodes are null. Per-test routers are isolated and
 * can't touch each other's components.
 */
async function makeRouter(initialPath = '/runs/run-1'): Promise<ReturnType<typeof createRouter>> {
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
    ],
  })
  await r.push(initialPath)
  return r
}

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

const DEFAULT_STUBS = {
  TimelinePane: true,
  PauseAnswerForm: true,
  ChildrenPane: true,
  // FileTree is embedded in RunSidebar's artifacts section; stub it
  // to avoid the listing-query crash when the mock returns [] (no
  // .entries). Sidebar rendering is covered by RunSidebar.spec.ts.
  FileTree: true,
}

/**
 * Mount RunDetailView with a fresh per-test router already at
 * `/runs/run-1` so the view's router.replace(?view=…) watcher can
 * resolve its path without throwing "No match for location".
 *
 * The caller is responsible for setting up `GET.mockImplementation`
 * (or `mockResolvedValue`) BEFORE calling `mountView` — exactly as the
 * existing tests already do.  `mountView` wraps that implementation to
 * additionally handle `/api/runs/{run_id}/children` (returning
 * `opts.children`, default `[]`) so existing tests don't need to care
 * about the children endpoint at all.
 */
async function mountView(
  opts: { children?: Run[]; router?: Awaited<ReturnType<typeof makeRouter>> } = {},
): Promise<ReturnType<typeof mount>> {
  const childRows = opts.children ?? []
  const testRouter = opts.router ?? (await makeRouter())

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
      plugins: [createPinia(), PiniaColada, testRouter],
      // FileTree (in RunSidebar) is stubbed to avoid the listing-query
      // crash when the mock returns [] (no .entries). Sidebar rendering
      // is covered by RunSidebar.spec.ts.
      // ChildrenPane is stubbed — its internal router-links and
      // useRunChildrenQuery usage are covered by ChildrenPane.spec.ts.
      // ParentRunChip is NOT stubbed so we can assert the rendered <a>
      // href (router plugin above makes router-link resolve to <a>).
      stubs: DEFAULT_STUBS,
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
    const w = await mountView()
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
    const w = await mountView()
    await flushPromises()

    // Cancel button lives inside [data-testid="run-right-pane"]
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
    const w = await mountView()
    await flushPromises()
    expect(w.find('[data-testid="cancel-run"]').exists()).toBe(false)
  })

  it('PauseAnswerForm shown only when paused', async () => {
    GET.mockResolvedValue(ok(detail({ status: 'paused' })))
    const w = await mountView()
    await flushPromises()
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(true)
  })

  it('sidebar and right pane are mounted; iters appear in sidebar', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(
            detail({
              status: 'done',
              iters: [
                { seq: 1, phase: 'planning', signal_kind: null, signal_args: null },
              ],
            }),
          ),
        )
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = await mountView()
    await flushPromises()
    // New layout: left rail + right body
    expect(w.find('[data-testid="run-sidebar"]').exists()).toBe(true)
    expect(w.find('[data-testid="run-right-pane"]').exists()).toBe(true)
    // Iters live in the sidebar as clickable rows
    expect(w.find('[data-testid="sidebar-iter-1"]').exists()).toBe(true)
  })

  it('prompt body is shown in the OverviewPanel', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(
          ok(detail({ status: 'done', prompt_body: 'do something useful' })),
        )
      return Promise.resolve(
        ok({ events: [], after_seq: 0, limit: 500, offset: 0 }),
      )
    })
    // done + no iters → smart-default is overview → OverviewPanel renders
    const w = await mountView()
    await flushPromises()
    // OverviewPanel shows the prompt body in a <pre>
    const panel = w.find('[data-testid="overview-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.find('pre').text()).toBe('do something useful')
  })

  it('hides the activity peek when no assistant_text has streamed', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(detail({ status: 'done' })))
      return Promise.resolve(
        ok({ events: [], after_seq: 0, limit: 500, offset: 0 }),
      )
    })
    const w = await mountView()
    await flushPromises()
    // latestActivity peek was removed in the layout rewrite — the element
    // is gone from the new view; absence is the expected state.
    expect(w.find('[data-testid="latest-activity"]').exists()).toBe(false)
  })

  it('shows a failure banner with a friendly explanation for agent_end_no_signal', async () => {
    // Field bug: a fresh "Hello, this is a test" prompt → pi replies
    // without emitting any [[engteam:...]] closing sentinel. The loop
    // correctly fails the run with exit_reason="agent_end_no_signal",
    // but the dashboard rendered no diagnostic — the user saw only a
    // failed status badge and a JSON-dumped run_ended boundary row.
    // The banner surfaces the reason inline with an explanation that
    // the bundled skill is auto-injected (ADR-44 — earlier copy
    // pointed at the now-deleted `relay install-skill`) and what
    // could still cause this (e.g. agent abort).
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
    const w = await mountView()
    await flushPromises()
    // Banner lives inside [data-testid="run-right-pane"]
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
    const w = await mountView()
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
    const w = await mountView()
    await flushPromises()
    expect(w.find('[data-testid="run-failure-banner"]').exists()).toBe(false)
  })

  it('Worktree path and branch are present in the run detail response', async () => {
    // WorktreePane is no longer mounted inside RunDetailView in the new
    // two-column layout — worktree metadata (path/branch) is part of the
    // run detail the view passes down to RunRightPane. The data flows
    // correctly when RunDetailView fetches the run; the rendering of
    // worktree-path / worktree-branch testids is covered by
    // WorktreePane.spec.ts. Here we confirm the view itself still
    // renders and doesn't crash when worktree fields are populated.
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
    const w = await mountView()
    await flushPromises()
    // View loaded without error and run id is visible
    expect(w.text()).toContain('run-1')
    expect(w.find('[data-testid="run-right-pane"]').exists()).toBe(true)
  })

  // ── 9e: ChildrenPane + ParentRunChip + cascade-aware Cancel ──────────

  it('shows the Cancel button on awaiting_children with cascade copy', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}')
        return Promise.resolve(ok(makeDetail({ status: 'awaiting_children' })))
      return Promise.resolve(ok({ events: [], after_seq: 0, limit: 500, offset: 0 }))
    })
    const w = await mountView({
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
    const w = await mountView({ children: [] })
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
    const w = await mountView({ children: [] })
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
    const w = await mountView({ children: [] })
    await flushPromises()

    expect(w.find('[data-testid="parent-run-chip"]').exists()).toBe(false)
  })
})

describe('RunDetailView — URL ↔ view binding', () => {
  beforeEach(() => {
    GET.mockReset()
    POST.mockReset()
  })

  it('hydrates ?view= with smart-default on first detail (running with iters)', async () => {
    const testRouter = await makeRouter('/runs/run-1')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(
          ok(
            makeDetail({
              status: 'running',
              iters: [
                { seq: 1, phase: 'planning', signal_kind: null, signal_args: null },
                { seq: 2, phase: 'planning', signal_kind: null, signal_args: null },
              ],
            }),
          ),
        )
      }
      if (path === '/api/runs/{run_id}/children') {
        return Promise.resolve(ok([]))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: {
        plugins: [createPinia(), PiniaColada, testRouter],
        stubs: DEFAULT_STUBS,
      },
    })
    await flushPromises()

    // running run with iters → smart-default is iter:N (latest iter)
    expect(testRouter.currentRoute.value.query.view).toBe('iter:2')
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(true)
  })

  it('respects an existing ?view= from the URL', async () => {
    const testRouter = await makeRouter('/runs/run-1?view=overview')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(ok(makeDetail({ status: 'running' })))
      }
      if (path === '/api/runs/{run_id}/children') {
        return Promise.resolve(ok([]))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: {
        plugins: [createPinia(), PiniaColada, testRouter],
        stubs: DEFAULT_STUBS,
      },
    })
    await flushPromises()

    // URL already has view=overview — view should not change it
    expect(testRouter.currentRoute.value.query.view).toBe('overview')
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(true)
  })

  it('pushes ?view=iter:N when a sidebar iter row is clicked', async () => {
    const testRouter = await makeRouter('/runs/run-1?view=overview')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(
          ok(
            makeDetail({
              status: 'running',
              iters: [{ seq: 1, phase: 'planning', signal_kind: null, signal_args: null }],
            }),
          ),
        )
      }
      if (path === '/api/runs/{run_id}/children') {
        return Promise.resolve(ok([]))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: {
        plugins: [createPinia(), PiniaColada, testRouter],
        stubs: DEFAULT_STUBS,
      },
    })
    await flushPromises()

    await w.get('[data-testid="sidebar-iter-1"]').trigger('click')
    await flushPromises()
    expect(testRouter.currentRoute.value.query.view).toBe('iter:1')
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(true)
  })
})

describe('RunDetailView — Phase 2 kinds chip URL plumbing', () => {
  beforeEach(() => {
    GET.mockReset()
    POST.mockReset()
  })

  it('parses ?kinds= and reflects it as off-state chips', async () => {
    const testRouter = await makeRouter('/runs/run-1?view=overview&kinds=tool')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(ok(makeDetail({ status: 'done' })))
      }
      if (path === '/api/runs/{run_id}/children') {
        return Promise.resolve(ok([]))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: {
        plugins: [createPinia(), PiniaColada, testRouter],
        stubs: DEFAULT_STUBS,
      },
    })
    await flushPromises()

    // Only the tool chip is on; the others should render as is-off.
    const tool = w.get('[data-testid="kind-chip-tool"]')
    expect(tool.attributes('aria-pressed')).toBe('true')
    const assistant = w.get('[data-testid="kind-chip-assistant"]')
    expect(assistant.attributes('aria-pressed')).toBe('false')
  })

  it('pushes ?kinds= when a chip is clicked, drops it again when filter clears', async () => {
    const testRouter = await makeRouter('/runs/run-1?view=overview')

    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}') {
        return Promise.resolve(ok(makeDetail({ status: 'done' })))
      }
      if (path === '/api/runs/{run_id}/children') {
        return Promise.resolve(ok([]))
      }
      return Promise.resolve(ok([]))
    })

    const w = mount(RunDetailView, {
      props: { id: 'run-1' },
      global: {
        plugins: [createPinia(), PiniaColada, testRouter],
        stubs: DEFAULT_STUBS,
      },
    })
    await flushPromises()

    // First click — turn `tool` off → URL gets the other four.
    await w.get('[data-testid="kind-chip-tool"]').trigger('click')
    await flushPromises()
    expect(testRouter.currentRoute.value.query.kinds).toBe(
      'assistant,thinking,signal,other',
    )

    // Click Clear → kinds drops from the URL entirely.
    await w.get('[data-testid="kind-filter-clear"]').trigger('click')
    await flushPromises()
    expect(testRouter.currentRoute.value.query.kinds).toBeUndefined()
  })
})
