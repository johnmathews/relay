import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const GET = vi.fn()
const POST = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    GET: (...a: unknown[]) => GET(...a),
    POST: (...a: unknown[]) => POST(...a),
  },
}))

import RunDetailView from '../src/views/RunDetailView.vue'

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

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}

function detail(over: Record<string, unknown> = {}): Record<string, unknown> {
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

function mountView(): ReturnType<typeof mount> {
  return mount(RunDetailView, {
    props: { id: 'run-1' },
    global: {
      plugins: [createPinia(), PiniaColada],
      // ArtifactsPane mounts the shared FileTree (which fires its own
      // listing query); stub it here — its behaviour is covered by
      // ArtifactsPane.spec.ts. WorktreePane is light + network-free so
      // we let it render to assert the W7 wiring end-to-end.
      stubs: {
        TimelinePane: true,
        PauseAnswerForm: true,
        ArtifactsPane: true,
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
})
