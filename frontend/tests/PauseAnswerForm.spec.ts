import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const POST = vi.fn()
vi.mock('@/api/client', () => ({
  api: { POST: (...a: unknown[]) => POST(...a) },
}))

import PauseAnswerForm from '../src/components/runs/PauseAnswerForm.vue'

function mountForm(): ReturnType<typeof mount> {
  return mount(PauseAnswerForm, {
    props: { runId: 'run-1', question: 'Which DB should I use?' },
    global: { plugins: [createPinia(), PiniaColada] },
  })
}

describe('PauseAnswerForm', () => {
  beforeEach(() => POST.mockReset())

  it('renders the pause question from run-detail', () => {
    const w = mountForm()
    expect(w.find('[data-testid="pause-question"]').text()).toBe(
      'Which DB should I use?',
    )
  })

  it('submit calls resume with {answer} and emits resumed on success', async () => {
    POST.mockResolvedValue({
      data: { id: 'run-1', status: 'running' },
      error: undefined,
      response: new Response(null, { status: 200 }),
    })
    const w = mountForm()
    await w.find('[data-testid="pause-answer-input"]').setValue('use sqlite')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/resume', {
      params: { path: { run_id: 'run-1' } },
      body: { answer: 'use sqlite' },
    })
    expect(w.emitted('resumed')).toBeTruthy()
  })

  it('shows a 409 inline and does NOT emit resumed', async () => {
    POST.mockResolvedValue({
      data: undefined,
      error: { detail: 'run is not paused' },
      response: new Response(null, { status: 409 }),
    })
    const w = mountForm()
    await w.find('[data-testid="pause-answer-input"]').setValue('x')
    await w.find('form').trigger('submit')
    await flushPromises()

    const err = w.find('[data-testid="pause-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('run is not paused')
    expect(w.emitted('resumed')).toBeFalsy()
  })

  it('requires a non-empty answer', async () => {
    const w = mountForm()
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="pause-error"]').text()).toContain(
      'answer is required',
    )
    expect(POST).not.toHaveBeenCalled()
  })
})
