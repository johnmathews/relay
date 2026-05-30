import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const POST = vi.fn()
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    POST: (...a: unknown[]) => POST(...a),
    GET: (...a: unknown[]) => GET(...a),
  },
}))

import ChatInput from '../src/components/chat/ChatInput.vue'

function mountInput(
  props: { runId?: string; status?: string } = {},
): ReturnType<typeof mount> {
  return mount(ChatInput, {
    props: {
      runId: props.runId ?? 'chat-1',
      status: props.status ?? 'paused',
    },
    global: { plugins: [createPinia(), PiniaColada] },
    attachTo: document.body,
  })
}

function okResp<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}

describe('ChatInput — composer mechanics', () => {
  beforeEach(() => {
    POST.mockReset()
    GET.mockReset()
  })

  it('Send is disabled when the textarea is empty', () => {
    const w = mountInput()
    const btn = w.get('[data-testid="chat-input-send"]')
      .element as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('Send becomes enabled once the user types and the run is paused', async () => {
    const w = mountInput({ status: 'paused' })
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('hello')
    const btn = w.get('[data-testid="chat-input-send"]')
      .element as HTMLButtonElement
    expect(btn.disabled).toBe(false)
  })

  it('Send is disabled while the run is running (turn in flight)', async () => {
    const w = mountInput({ status: 'running' })
    await w.get('[data-testid="chat-input-textarea"]').setValue('hello')
    const btn = w.get('[data-testid="chat-input-send"]')
      .element as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(w.get('[data-testid="chat-input-hint"]').text()).toContain(
      'Waiting',
    )
  })

  it('the textarea + Send are disabled when the chat is closed', () => {
    const w = mountInput({ status: 'closed' })
    const ta = w.get('[data-testid="chat-input-textarea"]')
      .element as HTMLTextAreaElement
    const btn = w.get('[data-testid="chat-input-send"]')
      .element as HTMLButtonElement
    expect(ta.disabled).toBe(true)
    expect(btn.disabled).toBe(true)
    expect(w.get('[data-testid="chat-input-hint"]').text()).toContain('ended')
  })

  it('Enter submits the draft via useResumeRunMutation', async () => {
    POST.mockResolvedValue(okResp({ id: 'chat-1', status: 'running' }))
    const w = mountInput()
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('first message')
    await ta.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/resume', {
      params: { path: { run_id: 'chat-1' } },
      body: { answer: 'first message' },
    })
    expect(w.emitted('sent')).toBeTruthy()
    // Textarea is cleared after a successful send.
    expect(
      (ta.element as HTMLTextAreaElement).value,
    ).toBe('')
  })

  it('Shift+Enter inserts a newline and does NOT submit', async () => {
    const w = mountInput()
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('line one')
    await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
    await flushPromises()
    expect(POST).not.toHaveBeenCalled()
    expect(w.emitted('sent')).toBeFalsy()
  })

  it('Enter during IME composition does NOT submit', async () => {
    const w = mountInput()
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('日本')
    // `isComposing` is read off the underlying KeyboardEvent; pass via
    // the second arg to `trigger` so the synthetic event carries it.
    await ta.trigger('keydown', { key: 'Enter', isComposing: true })
    await flushPromises()
    expect(POST).not.toHaveBeenCalled()
  })

  it('surfaces a 409 inline and keeps the textarea text intact', async () => {
    POST.mockResolvedValue({
      data: undefined,
      error: { detail: 'run is not paused' },
      response: new Response(null, { status: 409 }),
    })
    const w = mountInput()
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('hello')
    await ta.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(w.text()).toContain('Cannot send right now')
    // Draft preserved so the user can retry without re-typing.
    expect((ta.element as HTMLTextAreaElement).value).toBe('hello')
    expect(w.emitted('sent')).toBeFalsy()
  })

  it('whitespace-only drafts do not submit', async () => {
    const w = mountInput()
    const ta = w.get('[data-testid="chat-input-textarea"]')
    await ta.setValue('   \n   ')
    const btn = w.get('[data-testid="chat-input-send"]')
      .element as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    await ta.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(POST).not.toHaveBeenCalled()
  })
})
