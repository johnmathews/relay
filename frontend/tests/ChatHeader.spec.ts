import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { Router } from 'vue-router'

// ── Mocks ────────────────────────────────────────────────────────────
// ChatHeader uses `useCloseChatMutation` from @/lib/queries; we stub
// it to a controllable promise so the close path is observable
// without hitting the real fetch client.
const closeMutate = vi.fn()
vi.mock('@/lib/queries', () => ({
  useCloseChatMutation: () => ({
    mutateAsync: closeMutate,
    isLoading: ref(false),
  }),
  ApiError: class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super('api error')
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  },
}))

import ChatHeader from '../src/components/chat/ChatHeader.vue'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/',
        name: 'hub',
        component: { template: '<div />' },
      },
      {
        path: '/projects/:id',
        name: 'project',
        component: { template: '<div data-testid="project-stub" />' },
        props: true,
      },
      {
        path: '/projects/:id/new-run',
        name: 'new-run',
        component: { template: '<div />' },
        props: true,
      },
    ],
  })
}

async function mountHeader(
  props: {
    runId?: string
    status?: string
    project?: { id: number; name: string } | null
  } = {},
): Promise<{ w: ReturnType<typeof mount>; router: Router }> {
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  // `??` collapses an explicit `null` into the default — use the `in`
  // check so callers can pass `null` to test the loading state.
  const project: { id: number; name: string } | null =
    'project' in props
      ? (props.project ?? null)
      : { id: 42, name: 'Alpha' }
  const w = mount(ChatHeader, {
    props: {
      runId: props.runId ?? 'chat-1',
      status: props.status ?? 'paused',
      project,
    },
    global: { plugins: [createPinia(), PiniaColada, router] },
    attachTo: document.body,
  })
  return { w, router }
}

describe('ChatHeader', () => {
  beforeEach(() => {
    closeMutate.mockReset()
  })

  it('renders the project name and status badge', async () => {
    const { w } = await mountHeader({
      status: 'paused',
      project: { id: 42, name: 'Alpha' },
    })
    await flushPromises()
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('paused')
  })

  it('renders nothing for the project title while the project is null', async () => {
    // The chat detail query may still be in flight on first paint;
    // the header should fall back to a placeholder ('…') and hide the
    // back-link instead of throwing.
    const { w } = await mountHeader({ project: null })
    await flushPromises()
    expect(w.find('[data-testid="chat-header-back"]').exists()).toBe(false)
    expect(w.text()).toContain('…')
  })

  it('the Close button shows for paused / running, hides on terminal', async () => {
    const { w: paused } = await mountHeader({ status: 'paused' })
    expect(paused.find('[data-testid="chat-header-close"]').exists()).toBe(true)

    const { w: running } = await mountHeader({ status: 'running' })
    expect(running.find('[data-testid="chat-header-close"]').exists()).toBe(true)

    const { w: closed } = await mountHeader({ status: 'closed' })
    expect(closed.find('[data-testid="chat-header-close"]').exists()).toBe(false)

    const { w: failed } = await mountHeader({ status: 'failed' })
    expect(failed.find('[data-testid="chat-header-close"]').exists()).toBe(false)
  })

  it('clicking Close calls the mutation and emits closed', async () => {
    closeMutate.mockResolvedValue({ id: 'chat-1', status: 'closed' })
    const { w } = await mountHeader({ status: 'paused' })
    await w.get('[data-testid="chat-header-close"]').trigger('click')
    await flushPromises()
    expect(closeMutate).toHaveBeenCalledWith('chat-1')
    expect(w.emitted('closed')).toBeTruthy()
  })

  it('surfaces the close error inline when the mutation rejects', async () => {
    closeMutate.mockRejectedValue(new Error('boom'))
    const { w } = await mountHeader({ status: 'paused' })
    await w.get('[data-testid="chat-header-close"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="chat-header-error"]').exists()).toBe(true)
    expect(w.text()).toContain('Failed to close the chat.')
    // The promote button stays visible — close failure shouldn't lock
    // the operator out of other affordances.
    expect(w.find('[data-testid="chat-header-promote"]').exists()).toBe(true)
  })

  it('clicking Promote emits the promote event for the parent to handle', async () => {
    // The parent (ChatView) catches `promote` and navigates to the
    // New Run wizard with the transcript prefilled via sessionStorage.
    // The header itself does not navigate — keeping it a leaf
    // component means tests for the navigation live in ChatView's spec.
    const { w } = await mountHeader({ status: 'paused' })
    await w.get('[data-testid="chat-header-promote"]').trigger('click')
    expect(w.emitted('promote')).toBeTruthy()
    expect(w.emitted('promote')!.length).toBe(1)
  })

  it('Promote is visible regardless of status (closed chats can still seed a task)', async () => {
    // A chat that's already closed is still a useful source of
    // context for a new task — the operator may want to re-engage on
    // a different thread without reopening the chat.
    const { w } = await mountHeader({ status: 'closed' })
    expect(w.find('[data-testid="chat-header-promote"]').exists()).toBe(true)
  })

  it('the back button routes to the parent project', async () => {
    const { w, router } = await mountHeader({
      project: { id: 42, name: 'Alpha' },
    })
    await w.get('[data-testid="chat-header-back"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('project')
    expect(router.currentRoute.value.params).toEqual({ id: '42' })
  })
})
