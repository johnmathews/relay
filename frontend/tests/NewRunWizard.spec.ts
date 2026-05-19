import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import {
  createRouter,
  createMemoryHistory,
  type Router,
} from 'vue-router'
import { defineComponent, h } from 'vue'

// Behavior-focused: mock the query layer so no backend is needed. We
// drive the wizard and assert (1) the preview gate disables Start until
// a successful preview, (2) changing the prompt re-disables it, (3)
// cancel/preview NEVER issue POST /api/runs (only the create mutation
// does), and (4) Start sends exactly one prompt source + only set
// options and navigates to /runs/:id.

vi.mock('@/lib/queries', async () => {
  const { ref } = await import('vue')

  class FakeApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  }

  // Mutable test state the specs poke at.
  const state = {
    prompts: ref<Array<{ id: number; name: string; version: number }>>([]),
    promptsLoading: ref(false),
    promptsError: ref<unknown>(null),
    preview: ref<{
      preamble: string
      body: string
      prompt: string
      run_dir: string
    } | null>(null),
    previewLoading: ref(false),
    previewError: ref<unknown>(null),
    createMutate:
      vi.fn<(b: Record<string, unknown>) => Promise<{ id: string }>>(),
    createError: ref<unknown>(null),
    createLoading: ref(false),
  }

  return {
    __state: state,
    ApiError: FakeApiError,
    usePromptsQuery: () => ({ data: state.prompts }),
    usePreviewQuery: () => ({ data: state.preview }),
    useCreateRunMutation: () => ({
      mutateAsync: state.createMutate,
      isLoading: state.createLoading,
      error: state.createError,
    }),
    asAsyncState: (q: { data: { value: unknown } }) => {
      if (q.data === state.prompts) {
        return { isLoading: state.promptsLoading, error: state.promptsError }
      }
      return { isLoading: state.previewLoading, error: state.previewError }
    },
  }
})

import * as queries from '@/lib/queries'
import NewRunWizard from '../src/views/NewRunWizard.vue'

const state = (
  queries as unknown as {
    __state: {
      prompts: { value: Array<{ id: number; name: string; version: number }> }
      promptsLoading: { value: boolean }
      promptsError: { value: unknown }
      preview: {
        value:
          | { preamble: string; body: string; prompt: string; run_dir: string }
          | null
      }
      previewLoading: { value: boolean }
      previewError: { value: unknown }
      createMutate: ReturnType<typeof vi.fn>
      createError: { value: unknown }
      createLoading: { value: boolean }
    }
  }
).__state
const ApiError = (
  queries as unknown as {
    ApiError: new (status: number, message: string) => Error
  }
).ApiError

const Stub = defineComponent({ render: () => h('div') })

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'hub', component: Stub },
      { path: '/projects/:id', name: 'project', component: Stub },
      {
        path: '/projects/:id/new-run',
        name: 'new-run',
        component: NewRunWizard,
        props: true,
      },
      { path: '/runs/:id', name: 'run-detail', component: Stub },
    ],
  })
}

async function mountAt(projectId = '5'): Promise<{
  wrapper: Awaited<ReturnType<typeof mount>>
  router: Router
}> {
  const router = makeRouter()
  router.push(`/projects/${projectId}/new-run`)
  await router.isReady()
  const wrapper = mount(
    { template: '<router-view />' },
    { global: { plugins: [router] } },
  )
  await flushPromises()
  return { wrapper, router }
}

describe('NewRunWizard', () => {
  beforeEach(() => {
    state.prompts.value = [
      { id: 11, name: 'Refactor', version: 2 },
      { id: 12, name: 'Add tests', version: 1 },
    ]
    state.promptsLoading.value = false
    state.promptsError.value = null
    state.preview.value = null
    state.previewLoading.value = false
    state.previewError.value = null
    state.createMutate.mockReset()
    state.createError.value = null
    state.createLoading.value = false
  })

  it('renders step 1 with the project prompts', async () => {
    const { wrapper } = await mountAt()
    const list = wrapper.get('[data-testid="prompt-list"]')
    expect(list.text()).toContain('Refactor')
    expect(list.text()).toContain('v2')
    expect(list.text()).toContain('Add tests')
  })

  it('lets the user type an inline prompt instead', async () => {
    const { wrapper } = await mountAt()
    await wrapper.get('input[value="inline"]').setValue()
    await wrapper.get('textarea[name="inline-body"]').setValue('do the thing')
    // Advancing past step 1 requires a non-empty prompt; Next enabled now.
    expect(
      (
        wrapper.get('[data-testid="wizard-next"]')
          .element as HTMLButtonElement
      ).disabled,
    ).toBe(false)
  })

  it('Start is disabled until the preview has loaded successfully', async () => {
    const { wrapper } = await mountAt()
    // Pick an existing prompt, walk to the Start step WITHOUT a preview.
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // → opts
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // → prev
    // Preview step active but no data yet → no `loaded` emit.
    await flushPromises()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // → start
    const startBtn = wrapper.get('[data-testid="wizard-start"]')
      .element as HTMLButtonElement
    expect(startBtn.disabled).toBe(true)
    expect(state.createMutate).not.toHaveBeenCalled()
  })

  it('enables Start after the preview loads, re-disables on prompt change', async () => {
    const { wrapper } = await mountAt()
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    // Preview data lands while on the preview step → StepPreview emits.
    state.preview.value = {
      preamble: 'RELAY PREAMBLE TEXT',
      body: 'THE FULL PROMPT BODY',
      prompt: 'p',
      run_dir: '/.relay/runs/x',
    }
    await flushPromises()
    // Full preamble + body shown, untruncated.
    expect(wrapper.get('[data-testid="preview-preamble"]').text()).toBe(
      'RELAY PREAMBLE TEXT',
    )
    expect(wrapper.get('[data-testid="preview-body"]').text()).toBe(
      'THE FULL PROMPT BODY',
    )
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // → start
    expect(
      (
        wrapper.get('[data-testid="wizard-start"]')
          .element as HTMLButtonElement
      ).disabled,
    ).toBe(false)

    // Change the prompt → Start must re-disable until re-preview.
    await wrapper.get('[data-testid="wizard-back"]').trigger('click') // → prev
    await wrapper.get('[data-testid="wizard-back"]').trigger('click') // → opts
    await wrapper.get('[data-testid="wizard-back"]').trigger('click') // → step1
    // Real usePreviewQuery would have no cached data for the NEW
    // selection key until it refetches — model that.
    state.preview.value = null
    await wrapper
      .get('input[name="existing-prompt"][value="12"]')
      .setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    expect(
      (
        wrapper.get('[data-testid="wizard-start"]')
          .element as HTMLButtonElement
      ).disabled,
    ).toBe(true)
  })

  it('Start posts exactly one prompt source + only set options, then navigates', async () => {
    state.createMutate.mockResolvedValue({ id: 'run-99' })
    const { wrapper, router } = await mountAt('5')
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // opts
    await wrapper
      .get('input[name="max-iters"]')
      .setValue('7')
    // iter-timeout left blank → must NOT be sent.
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // prev
    state.preview.value = {
      preamble: 'P',
      body: 'B',
      prompt: 'p',
      run_dir: 'd',
    }
    await flushPromises()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click') // start
    await wrapper.get('[data-testid="wizard-start"]').trigger('click')
    await flushPromises()

    expect(state.createMutate).toHaveBeenCalledTimes(1)
    expect(state.createMutate).toHaveBeenCalledWith({
      project_id: 5,
      prompt_id: 11,
      max_iters: 7,
    })
    expect(router.currentRoute.value.fullPath).toBe('/runs/run-99')
  })

  it('Cancel navigates away and never calls POST /api/runs', async () => {
    const { wrapper, router } = await mountAt('5')
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-cancel"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/projects/5')
    expect(state.createMutate).not.toHaveBeenCalled()
  })

  it('viewing the preview never calls POST /api/runs', async () => {
    const { wrapper } = await mountAt()
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    state.preview.value = {
      preamble: 'P',
      body: 'B',
      prompt: 'p',
      run_dir: 'd',
    }
    await flushPromises()
    expect(state.createMutate).not.toHaveBeenCalled()
  })

  it('shows an API error from Start inline and stays on the wizard', async () => {
    state.createMutate.mockImplementation(async () => {
      state.createError.value = new ApiError(400, 'project has no harness')
      throw state.createError.value
    })
    const { wrapper, router } = await mountAt('5')
    await wrapper.get('input[name="existing-prompt"][value="11"]').setValue()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    state.preview.value = {
      preamble: 'P',
      body: 'B',
      prompt: 'p',
      run_dir: 'd',
    }
    await flushPromises()
    await wrapper.get('[data-testid="wizard-next"]').trigger('click')
    await wrapper.get('[data-testid="wizard-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain(
      'project has no harness',
    )
    expect(router.currentRoute.value.fullPath).toBe(
      '/projects/5/new-run',
    )
  })
})
