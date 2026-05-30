import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, computed, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { Router } from 'vue-router'
import type { RunDetail } from '../src/lib/queries'

// ── Mocks ────────────────────────────────────────────────────────────

const detailData = ref<RunDetail | null>(null)
const refetchDetail = vi.fn(async () => {})
const projectData = ref<{ id: number; name: string } | null>(null)
const eventsList = ref<unknown[]>([])
const pendingTurns = ref<unknown[]>([])
const openSpy = vi.fn(
  async (_runId: string, _status: string, _opts?: unknown) => {},
)
const markTerminalSpy = vi.fn()
const resetSpy = vi.fn()
const resumeMutate = vi.fn()
const closeMutate = vi.fn()

vi.mock('@/lib/queries', () => ({
  useRunDetailQuery: () => ({
    data: detailData,
    refetch: refetchDetail,
    isPending: computed(() => false),
    error: computed(() => null),
  }),
  useProjectQuery: () => ({
    data: projectData,
    isPending: computed(() => false),
    error: computed(() => null),
  }),
  asAsyncState: () => ({
    isLoading: computed(() => detailData.value == null),
    error: computed(() => null),
  }),
  useInvalidate: () => () => Promise.resolve(),
  useResumeRunMutation: () => ({
    mutateAsync: resumeMutate,
    isLoading: ref(false),
  }),
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

// Pinia auto-unwraps refs on store-property access. Our manual mock
// has to do the same — bare `events: eventsList` returns the Ref, and
// `computed(() => eventsStore.events).value` would be a Ref, not an
// array (the consumer's ChatTranscript spreads it). Use getters so the
// ref is unwrapped each access AND mutation from the test still
// triggers the consumer's computed.
const lastHeartbeatRef = ref(null)
vi.mock('@/stores/events', () => ({
  useEventsStore: () => ({
    open: openSpy,
    markTerminal: markTerminalSpy,
    reset: resetSpy,
    get events() {
      return eventsList.value
    },
    get pendingTurns() {
      return pendingTurns.value
    },
    get lastHeartbeat() {
      return lastHeartbeatRef.value
    },
  }),
}))

vi.mock('@/stores/currentRun', () => ({
  useCurrentRunStore: () => ({ reset: vi.fn() }),
}))

vi.mock('@/components/files/MarkdownRender.vue', () => ({
  default: {
    name: 'MarkdownRender',
    props: { source: { type: String, default: '' } },
    template: '<div class="stub-md">{{ source }}</div>',
  },
}))

vi.mock('@/components/runs/ToolCallCard.vue', () => ({
  default: {
    name: 'ToolCallCard',
    props: {
      name: { type: String, default: '' },
      args: { type: null, default: null },
      result: { type: null, default: null },
      isError: { type: Boolean, default: false },
      durationMs: { type: Number, default: null },
      embedded: { type: Boolean, default: false },
    },
    template: '<div class="stub-tool">{{ name }}</div>',
  },
}))

import ChatView from '../src/views/ChatView.vue'

// ── Helpers ──────────────────────────────────────────────────────────

function makeChatDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: 'chat-1',
    project_id: 42,
    status: 'paused',
    mode: 'chat',
    started_at: '2026-05-30T12:00:00Z',
    ended_at: null,
    iters: [],
    ...overrides,
  } as unknown as RunDetail
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/runs/:id',
        name: 'run-detail',
        component: { template: '<div data-testid="task-view-stub" />' },
        props: true,
      },
      {
        path: '/chats/:id',
        name: 'chat-detail',
        component: ChatView,
        props: true,
      },
      {
        path: '/projects/:id',
        name: 'project',
        component: { template: '<div />' },
        props: true,
      },
      {
        path: '/projects/:id/new-run',
        name: 'new-run',
        component: { template: '<div data-testid="new-run-stub" />' },
        props: true,
      },
    ],
  })
}

async function mountView(): Promise<{
  w: ReturnType<typeof mount>
  router: Router
}> {
  setActivePinia(createPinia())
  const router = makeRouter()
  await router.push('/chats/chat-1')
  await router.isReady()
  const w = mount(ChatView, {
    props: { id: 'chat-1' },
    global: {
      plugins: [createPinia(), PiniaColada, router],
    },
    attachTo: document.body,
  })
  return { w, router }
}

// ── Tests ────────────────────────────────────────────────────────────

describe('ChatView — integration', () => {
  beforeEach(() => {
    detailData.value = null
    projectData.value = null
    eventsList.value = []
    pendingTurns.value = []
    refetchDetail.mockClear()
    openSpy.mockClear()
    markTerminalSpy.mockClear()
    resetSpy.mockClear()
    resumeMutate.mockReset()
    closeMutate.mockReset()
  })

  it('mounts the header, transcript, and composer once detail lands', async () => {
    detailData.value = makeChatDetail()
    projectData.value = { id: 42, name: 'Alpha' }
    const { w } = await mountView()
    await flushPromises()

    expect(w.find('[data-testid="chat-header"]').exists()).toBe(true)
    expect(w.find('[data-testid="chat-transcript"]').exists()).toBe(true)
    expect(w.find('[data-testid="chat-input-form"]').exists()).toBe(true)
    expect(w.text()).toContain('Alpha')
  })

  it('opens the events stream with the chat run id + status', async () => {
    detailData.value = makeChatDetail({ status: 'paused' })
    projectData.value = { id: 42, name: 'Alpha' }
    await mountView()
    await flushPromises()
    expect(openSpy).toHaveBeenCalled()
    const args = openSpy.mock.calls[0]!
    expect(args[0]).toBe('chat-1')
    expect(args[1]).toBe('paused')
  })

  it('renders existing user + assistant turns from the events list', async () => {
    detailData.value = makeChatDetail()
    projectData.value = { id: 42, name: 'Alpha' }
    eventsList.value = [
      { seq: 1, kind: 'pause_resolved', payload: { answer: 'first msg' } },
      { seq: 2, kind: 'iter_started', payload: { seq: 1, iter_id: 11 } },
      {
        seq: 3,
        kind: 'assistant_text',
        payload: { kind: 'text', text: 'first reply' },
      },
      { seq: 4, kind: 'iter_ended', payload: { seq: 1, iter_id: 11 } },
    ]
    const { w } = await mountView()
    await flushPromises()
    expect(w.text()).toContain('first msg')
    expect(w.text()).toContain('first reply')
  })

  it('shows new assistant text when a fresh event is delivered live', async () => {
    detailData.value = makeChatDetail()
    projectData.value = { id: 42, name: 'Alpha' }
    eventsList.value = [
      { seq: 1, kind: 'pause_resolved', payload: { answer: 'hi' } },
    ]
    const { w } = await mountView()
    await flushPromises()
    expect(w.text()).not.toContain('streamed reply')

    eventsList.value = [
      ...eventsList.value,
      { seq: 2, kind: 'iter_started', payload: { seq: 1, iter_id: 11 } },
      {
        seq: 3,
        kind: 'assistant_text',
        payload: { kind: 'text', text: 'streamed reply' },
      },
      { seq: 4, kind: 'iter_ended', payload: { seq: 1, iter_id: 11 } },
    ]
    await nextTick()
    await flushPromises()
    expect(w.text()).toContain('streamed reply')
  })

  it('submitting a message calls the resume mutation and reopens the stream', async () => {
    detailData.value = makeChatDetail({ status: 'paused' })
    projectData.value = { id: 42, name: 'Alpha' }
    resumeMutate.mockResolvedValue({ id: 'chat-1', status: 'running' })
    const { w } = await mountView()
    await flushPromises()
    openSpy.mockClear()

    await w
      .get('[data-testid="chat-input-textarea"]')
      .setValue('a fresh message')
    await w
      .get('[data-testid="chat-input-textarea"]')
      .trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(resumeMutate).toHaveBeenCalledWith({
      runId: 'chat-1',
      answer: 'a fresh message',
    })
    expect(refetchDetail).toHaveBeenCalled()
    expect(openSpy).toHaveBeenCalled()
  })

  it('a task-mode run opened via /chats/:id redirects to /runs/:id', async () => {
    detailData.value = makeChatDetail({ mode: 'task' })
    const { router } = await mountView()
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('run-detail')
    expect(router.currentRoute.value.params).toEqual({ id: 'chat-1' })
  })

  it('clicking Close calls useCloseChatMutation', async () => {
    detailData.value = makeChatDetail({ status: 'paused' })
    projectData.value = { id: 42, name: 'Alpha' }
    closeMutate.mockResolvedValue({ id: 'chat-1', status: 'closed' })
    const { w } = await mountView()
    await flushPromises()
    await w.get('[data-testid="chat-header-close"]').trigger('click')
    await flushPromises()
    expect(closeMutate).toHaveBeenCalledWith('chat-1')
    expect(refetchDetail).toHaveBeenCalled()
  })

  it('the Close button is hidden on a terminal chat', async () => {
    detailData.value = makeChatDetail({ status: 'closed' })
    projectData.value = { id: 42, name: 'Alpha' }
    const { w } = await mountView()
    await flushPromises()
    expect(w.find('[data-testid="chat-header-close"]').exists()).toBe(false)
  })

  it('Promote-to-task navigates to the new-run wizard with the transcript in sessionStorage', async () => {
    // W6 — the click stashes a built prefill body keyed by chat run
    // id in sessionStorage (URL would risk length limits for long
    // transcripts), then routes to /projects/:id/new-run with a
    // marker query param so the wizard knows to consult storage.
    detailData.value = makeChatDetail()
    projectData.value = { id: 42, name: 'Alpha' }
    eventsList.value = [
      { seq: 1, kind: 'pause_resolved', payload: { answer: 'first msg' } },
      { seq: 2, kind: 'iter_started', payload: { seq: 1, iter_id: 11 } },
      {
        seq: 3,
        kind: 'assistant_text',
        payload: { kind: 'text', text: 'first reply' },
      },
      { seq: 4, kind: 'iter_ended', payload: { seq: 1, iter_id: 11 } },
    ]
    sessionStorage.removeItem('relay:promotion:chat-1')
    const { w, router } = await mountView()
    await flushPromises()
    await w.get('[data-testid="chat-header-promote"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('new-run')
    expect(router.currentRoute.value.params).toEqual({ id: '42' })
    expect(router.currentRoute.value.query.promoteFrom).toBe('chat-1')

    const stored = sessionStorage.getItem('relay:promotion:chat-1')
    expect(stored).not.toBeNull()
    expect(stored).toContain('User: first msg')
    expect(stored).toContain('Assistant: first reply')
    expect(stored).toContain('in project Alpha')
  })

  it('Promote leaves the chat run in its current state (non-destructive)', async () => {
    // The plan calls out: promoting a chat does NOT close or cancel
    // the chat. The user might want to keep talking and promote
    // again later — verify the close mutation is not called on
    // promote.
    detailData.value = makeChatDetail({ status: 'paused' })
    projectData.value = { id: 42, name: 'Alpha' }
    const { w } = await mountView()
    await flushPromises()
    closeMutate.mockClear()
    await w.get('[data-testid="chat-header-promote"]').trigger('click')
    await flushPromises()
    expect(closeMutate).not.toHaveBeenCalled()
    expect(markTerminalSpy).not.toHaveBeenCalled()
  })
})
