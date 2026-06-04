import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/client', () => ({
  api: {
    GET: vi.fn(() => Promise.resolve({ data: [], error: undefined, response: new Response(null, { status: 200 }) })),
    POST: vi.fn(),
  },
}))

import RunRightPane from '../src/components/runs/layout/RunRightPane.vue'
import type { Iter } from '../src/lib/queries'
import { api } from '@/api/client'

const baseDetail = {
  id: 'run-1',
  status: 'running',
  started_at: '2026-05-19T10:00:00Z',
  ended_at: null,
  max_iters: 5,
  prompt_id: 7,
  prompt_body: 'do the thing',
  parent_run_id: null,
  iters: [{ seq: 1, phase: 'planning', signal_kind: null, signal_args: null, exit_reason: null }] as unknown as Iter[],
}

function makeRouter(): ReturnType<typeof createRouter> {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs/:id', name: 'run-detail', component: { template: '<div/>' } },
    ],
  })
}

function mountPane(over: Record<string, unknown> = {}): ReturnType<typeof mount> {
  return mount(RunRightPane, {
    props: {
      detail: baseDetail,
      selection: { kind: 'overview' },
      events: [],
      pendingTurns: [],
      lastHeartbeat: null,
      childCount: 0,
      cancelLabel: 'Cancel run',
      cancelling: false,
      pauseQuestion: '',
      pauseReviewPaths: [],
      followLive: false,
      followLiveVisible: false,
      ...over,
    },
    global: { plugins: [createPinia(), PiniaColada, makeRouter()] },
  })
}

describe('RunRightPane — header', () => {
  it('renders run id, status badge, started-at, iter count, phase', () => {
    const w = mountPane()
    expect(w.text()).toContain('run-1')
    expect(w.text()).toContain('1 / 5')
    expect(w.text()).toContain('planning')
  })

  it('shows Cancel button only when cancellable', () => {
    const running = mountPane({ detail: { ...baseDetail, status: 'running' } })
    expect(running.find('[data-testid="cancel-run"]').exists()).toBe(true)

    const done = mountPane({ detail: { ...baseDetail, status: 'done' } })
    expect(done.find('[data-testid="cancel-run"]').exists()).toBe(false)
  })

  it('emits cancel on Cancel-button click', async () => {
    const w = mountPane()
    await w.get('[data-testid="cancel-run"]').trigger('click')
    expect(w.emitted('cancel')).toBeTruthy()
  })
})

describe('RunRightPane — follow-live pin', () => {
  it('renders the pin button only when followLiveVisible', () => {
    const off = mountPane({ followLiveVisible: false })
    expect(off.find('[data-testid="follow-live-pin"]').exists()).toBe(false)

    const on = mountPane({ followLiveVisible: true })
    expect(on.find('[data-testid="follow-live-pin"]').exists()).toBe(true)
  })

  it('reflects followLive state via aria-pressed and label', () => {
    const off = mountPane({ followLiveVisible: true, followLive: false })
    const btnOff = off.get('[data-testid="follow-live-pin"]')
    expect(btnOff.attributes('aria-pressed')).toBe('false')
    expect(btnOff.text()).toContain('Follow live')

    const on = mountPane({ followLiveVisible: true, followLive: true })
    const btnOn = on.get('[data-testid="follow-live-pin"]')
    expect(btnOn.attributes('aria-pressed')).toBe('true')
    expect(btnOn.text()).toContain('Following live')
  })

  it('emits toggle-follow-live on click', async () => {
    const w = mountPane({ followLiveVisible: true, followLive: false })
    await w.get('[data-testid="follow-live-pin"]').trigger('click')
    expect(w.emitted('toggle-follow-live')).toBeTruthy()
  })

  it('renders the pin alongside Cancel when both are visible', () => {
    const w = mountPane({ followLiveVisible: true })
    expect(w.find('[data-testid="cancel-run"]').exists()).toBe(true)
    expect(w.find('[data-testid="follow-live-pin"]').exists()).toBe(true)
  })
})

describe('RunRightPane — body routing', () => {
  it('renders OverviewPanel for kind=overview', () => {
    const w = mountPane({ selection: { kind: 'overview' } })
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(true)
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-panel"]').exists()).toBe(false)
  })

  it('renders IterTimelinePanel for kind=iter', () => {
    const w = mountPane({ selection: { kind: 'iter', seq: 1 } })
    expect(w.find('[data-testid="iter-timeline-panel"]').exists()).toBe(true)
    expect(w.find('[data-testid="overview-panel"]').exists()).toBe(false)
  })

  it('renders ArtifactPanel for kind=artifact', async () => {
    const w = mountPane({
      selection: { kind: 'artifact', path: 'plan.md' },
    })
    await flushPromises()
    expect(w.find('[data-testid="artifact-panel"]').exists()).toBe(true)
  })
})

describe('RunRightPane — paused', () => {
  it('renders PauseBanner wrapping PauseAnswerForm when status=paused', () => {
    const paused = {
      ...baseDetail,
      status: 'paused',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: 'pause',
          signal_args: { question: 'approve?' },
        },
      ],
    }
    const w = mountPane({
      detail: paused,
      pauseQuestion: 'approve?',
      pauseReviewPaths: [],
    })
    const banner = w.find('[data-testid="pause-banner"]')
    expect(banner.exists()).toBe(true)
    // Form is rendered inside the banner wrapper — the 14c/14e/14f
    // contract is preserved through the wrap, not rewritten.
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(true)
    expect(banner.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(
      true,
    )
  })

  it('places the PauseBanner between the run header and the body', () => {
    const paused = {
      ...baseDetail,
      status: 'paused',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: 'pause',
          signal_args: { question: 'approve?' },
        },
      ],
    }
    const w = mountPane({
      detail: paused,
      pauseQuestion: 'approve?',
      pauseReviewPaths: ['plan.md'],
    })
    const root = w.get('[data-testid="run-right-pane"]')
    const children = Array.from(root.element.children)
    const headerIdx = children.findIndex((el) =>
      el.classList.contains('right-pane__header'),
    )
    const bannerIdx = children.findIndex(
      (el) => el.getAttribute('data-testid') === 'pause-banner',
    )
    const bodyIdx = children.findIndex((el) =>
      el.classList.contains('right-pane__body'),
    )
    expect(headerIdx).toBeGreaterThanOrEqual(0)
    expect(bannerIdx).toBeGreaterThan(headerIdx)
    expect(bodyIdx).toBeGreaterThan(bannerIdx)
  })

  it('does NOT render PauseBanner when status != paused', () => {
    const w = mountPane()
    expect(w.find('[data-testid="pause-banner"]').exists()).toBe(false)
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(false)
  })
})

describe('RunRightPane — tool-call drawer wiring', () => {
  it('does NOT mount the drawer in the DOM when closed (no zombie focus trap)', async () => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
    mountPane()
    await flushPromises()
    // The drawer is teleported to <body>; if it were rendered, the
    // `[data-testid="tool-drawer"]` element would land there.
    expect(
      document.body.querySelector('[data-testid="tool-drawer"]'),
    ).toBeNull()
  })
})

describe('RunRightPane — failure banner', () => {
  it('renders the failure banner with agent_end_no_signal hint', () => {
    const failed = {
      ...baseDetail,
      status: 'failed',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: null,
          signal_args: null,
          exit_reason: 'agent_end_no_signal',
        },
      ],
    }
    const w = mountPane({ detail: failed })
    const banner = w.get('[data-testid="run-failure-banner"]')
    expect(banner.attributes('data-reason')).toBe('agent_end_no_signal')
    expect(banner.text()).toContain('engineering-team')
    expect(banner.text()).toContain('closing sentinel')
  })

  it('renders the failure banner with timeout hint', () => {
    const failed = {
      ...baseDetail,
      status: 'failed',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: null,
          signal_args: null,
          exit_reason: 'timeout',
        },
      ],
    }
    const w = mountPane({ detail: failed })
    const banner = w.get('[data-testid="run-failure-banner"]')
    expect(banner.attributes('data-reason')).toBe('timeout')
    expect(banner.text()).toContain('iter_timeout')
  })

  it('renders marker_error inline when present', () => {
    const failed = {
      ...baseDetail,
      status: 'cancelled',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: null,
          signal_args: { marker_error: 'expected ", got newline at line 3' },
          exit_reason: 'cancelled',
        },
      ],
    }
    const w = mountPane({ detail: failed })
    const banner = w.get('[data-testid="run-failure-banner"]')
    expect(banner.text()).toContain('Marker error:')
    expect(banner.text()).toContain('expected')
  })

  it('does NOT render failure banner for done', () => {
    const w = mountPane({ detail: { ...baseDetail, status: 'done' } })
    expect(w.find('[data-testid="run-failure-banner"]').exists()).toBe(false)
  })

  it('hides the failure banner after clicking dismiss and persists per run', async () => {
    const failed = {
      ...baseDetail,
      id: 'run-dismiss-1',
      status: 'failed',
      iters: [
        {
          seq: 1,
          phase: 'planning',
          signal_kind: null,
          signal_args: null,
          exit_reason: 'agent_end_no_signal',
        },
      ],
    }
    try {
      localStorage.removeItem(
        'relay.failureBanner.dismissed:run-dismiss-1',
      )
    } catch {
      // ignore
    }
    const w = mountPane({ detail: failed })
    expect(w.find('[data-testid="run-failure-banner"]').exists()).toBe(true)
    await w.get('[data-testid="dismiss-failure-banner"]').trigger('click')
    expect(w.find('[data-testid="run-failure-banner"]').exists()).toBe(false)
    expect(
      localStorage.getItem('relay.failureBanner.dismissed:run-dismiss-1'),
    ).toBe('1')

    // A fresh mount with the same run id reads the dismissed flag back.
    const w2 = mountPane({ detail: failed })
    expect(w2.find('[data-testid="run-failure-banner"]').exists()).toBe(
      false,
    )
  })
})

describe('RunRightPane — reopen affordance (WU5)', () => {
  it('shows Reopen-as-paused button for failed+no-signal run', () => {
    const w = mountPane({
      detail: {
        ...baseDetail,
        status: 'failed',
        ended_at: '2026-06-04T15:53:55Z',
        iters: [{
          seq: 1, phase: 'development', signal_kind: null,
          signal_args: null, exit_reason: 'agent_end_no_signal',
        }] as unknown as Iter[],
      },
    })
    expect(w.find('[data-testid="reopen-run"]').exists()).toBe(true)
  })

  it('hides Reopen-as-paused button for failed+agent_end_no_signal_autopause iter (unreachable in normal operation; predicate is tight)', () => {
    // Per ADR-53: WU4 autopause produces LoopResult("paused", …) → run.status =
    // "paused", so the `status !== 'failed'` guard fires first and this
    // combination is unreachable. Even if engineered into existence,
    // iter.exit_reason is never "agent_end_no_signal_autopause" — the suffix
    // lives on LoopResult.reason only. The predicate is tightened to reject it.
    const w = mountPane({
      detail: {
        ...baseDetail,
        status: 'failed',
        ended_at: '2026-06-04T15:53:55Z',
        iters: [{
          seq: 1, phase: null, signal_kind: 'pause',
          signal_args: { id: 'x' },
          exit_reason: 'agent_end_no_signal_autopause',
        }] as unknown as Iter[],
      },
    })
    expect(w.find('[data-testid="reopen-run"]').exists()).toBe(false)
  })

  it('hides Reopen button for failed+timeout run', () => {
    const w = mountPane({
      detail: {
        ...baseDetail,
        status: 'failed',
        iters: [{
          seq: 1, phase: null, signal_kind: null,
          signal_args: null, exit_reason: 'timeout',
        }] as unknown as Iter[],
      },
    })
    expect(w.find('[data-testid="reopen-run"]').exists()).toBe(false)
  })

  it('hides Reopen button for done run', () => {
    const w = mountPane({ detail: { ...baseDetail, status: 'done' } })
    expect(w.find('[data-testid="reopen-run"]').exists()).toBe(false)
  })

  it('hides Reopen button for running run', () => {
    const w = mountPane({ detail: { ...baseDetail, status: 'running' } })
    expect(w.find('[data-testid="reopen-run"]').exists()).toBe(false)
  })

  it('calls POST /api/runs/{id}/reopen on click', async () => {
    (api.POST as ReturnType<typeof vi.fn>).mockReset()
    ;(api.POST as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { id: 'run-1', status: 'paused' },
      error: undefined,
      response: new Response(null, { status: 200 }),
    })
    const w = mountPane({
      detail: {
        ...baseDetail,
        status: 'failed',
        iters: [{
          seq: 1, phase: null, signal_kind: null,
          signal_args: null, exit_reason: 'agent_end_no_signal',
        }] as unknown as Iter[],
      },
    })
    await w.get('[data-testid="reopen-run"]').trigger('click')
    await flushPromises()
    expect(api.POST).toHaveBeenCalledWith(
      '/api/runs/{run_id}/reopen',
      expect.objectContaining({
        params: { path: { run_id: 'run-1' } },
      }),
    )
  })

  it('renders inline error message when reopen mutation rejects', async () => {
    ;(api.POST as ReturnType<typeof vi.fn>).mockReset()
    ;(api.POST as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: undefined,
      error: { detail: 'run run-1 is not failed (status=\'done\')' },
      response: new Response(null, { status: 409 }),
    })
    const w = mountPane({
      detail: {
        ...baseDetail,
        status: 'failed',
        iters: [{
          seq: 1, phase: null, signal_kind: null,
          signal_args: null, exit_reason: 'agent_end_no_signal',
        }] as unknown as Iter[],
      },
    })
    await w.get('[data-testid="reopen-run"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="reopen-error"]').exists()).toBe(true)
  })
})
