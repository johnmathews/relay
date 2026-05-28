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
  it('renders PauseAnswerForm above the body when status=paused', () => {
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
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(true)
  })

  it('does NOT render PauseAnswerForm when status != paused', () => {
    const w = mountPane()
    expect(w.findComponent({ name: 'PauseAnswerForm' }).exists()).toBe(false)
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
})
