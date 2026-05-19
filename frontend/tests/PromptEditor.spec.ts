import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { Prompt } from '../src/lib/queries'

const { createMutate, updateMutate } = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
}))

vi.mock('@/lib/queries', async () => {
  // Refs + ApiError live INSIDE the factory: it runs lazily on first
  // import (so we can `await import('vue')`), unlike `vi.hoisted`.
  const { ref: vref } = await import('vue')
  const createError = vref<unknown>(null)
  const updateError = vref<unknown>(null)
  const createLoading = vref(false)
  const updateLoading = vref(false)
  class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(
        body != null &&
        typeof body === 'object' &&
        'detail' in body &&
        typeof (body as { detail: unknown }).detail === 'string'
          ? (body as { detail: string }).detail
          : `Request failed with status ${status}`,
      )
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError,
    __state: { createError, updateError, createLoading, updateLoading },
    useCreatePromptMutation: () => ({
      mutateAsync: createMutate,
      error: createError,
      isLoading: createLoading,
    }),
    useUpdatePromptMutation: () => ({
      mutateAsync: updateMutate,
      error: updateError,
      isLoading: updateLoading,
    }),
  }
})

import PromptEditor from '../src/components/prompts/PromptEditor.vue'
import * as queries from '../src/lib/queries'

const { ApiError } = queries
const state = (queries as unknown as {
  __state: {
    createError: { value: unknown }
    updateError: { value: unknown }
    createLoading: { value: boolean }
    updateLoading: { value: boolean }
  }
}).__state
const createError = state.createError
const updateError = state.updateError
const createLoading = state.createLoading
const updateLoading = state.updateLoading

const MarkdownRenderStub = {
  name: 'MarkdownRender',
  props: ['source'],
  template: '<div class="md-stub">{{ source }}</div>',
}

function mountEditor(
  props: {
    mode: 'create' | 'edit'
    projectId: number
    prompt?: Prompt | null
  },
): ReturnType<typeof mount> {
  return mount(PromptEditor, {
    props,
    global: { stubs: { MarkdownRender: MarkdownRenderStub } },
  })
}

describe('PromptEditor', () => {
  beforeEach(() => {
    createMutate.mockReset()
    updateMutate.mockReset()
    createError.value = null
    updateError.value = null
    createLoading.value = false
    updateLoading.value = false
  })

  it('create mode posts {project_id, name, body} and emits saved', async () => {
    const result = { id: 5, name: 'P', version: 1 } as unknown as Prompt
    createMutate.mockResolvedValue(result)
    const w = mountEditor({ mode: 'create', projectId: 7 })

    await w.get('[data-testid="prompt-name"]').setValue('P')
    await w.get('[data-testid="prompt-body"]').setValue('# hello')
    await w.get('[data-testid="prompt-editor"]').trigger('submit')
    await flushPromises()

    expect(createMutate).toHaveBeenCalledWith({
      project_id: 7,
      name: 'P',
      body: '# hello',
    })
    expect(updateMutate).not.toHaveBeenCalled()
    const saved = w.emitted('saved')
    expect(saved).toBeTruthy()
    expect((saved![0]![0] as Prompt).id).toBe(5)
  })

  it('edit mode PUTs /api/prompts/{id} via the update mutation (snapshot bump), name fixed', async () => {
    const result = { id: 9, name: 'P', version: 2 } as unknown as Prompt
    updateMutate.mockResolvedValue(result)
    const w = mountEditor({
      mode: 'edit',
      projectId: 7,
      prompt: { id: 4, name: 'P', body: 'old' } as unknown as Prompt,
    })

    // Name is the identity key in edit mode → shown read-only, no input.
    expect(w.find('[data-testid="prompt-name"]').exists()).toBe(false)
    expect(w.find('[data-testid="prompt-name-fixed"]').text()).toBe('P')
    // The "edits version, history preserved" note is surfaced.
    expect(w.find('[data-testid="version-note"]').exists()).toBe(true)
    expect(w.text()).toContain('Saving creates a new version')

    await w.get('[data-testid="prompt-body"]').setValue('new body')
    await w.get('[data-testid="prompt-editor"]').trigger('submit')
    await flushPromises()

    expect(updateMutate).toHaveBeenCalledWith({ id: 4, body: 'new body' })
    expect(createMutate).not.toHaveBeenCalled()
    expect((w.emitted('saved')![0]![0] as Prompt).id).toBe(9)
  })

  it('shows a 409 duplicate-name error inline on create', async () => {
    createMutate.mockImplementation(async () => {
      createError.value = new ApiError(409, {
        detail: 'prompt name already exists',
      })
      throw createError.value
    })
    const w = mountEditor({ mode: 'create', projectId: 7 })
    await w.get('[data-testid="prompt-name"]').setValue('dup')
    await w.get('[data-testid="prompt-body"]').setValue('x')
    await w.get('[data-testid="prompt-editor"]').trigger('submit')
    await flushPromises()

    const err = w.find('[data-testid="prompt-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('prompt name already exists')
    expect(w.emitted('saved')).toBeFalsy()
  })

  it('toggles the markdown preview of the body', async () => {
    const w = mountEditor({ mode: 'create', projectId: 7 })
    await w.get('[data-testid="prompt-body"]').setValue('# Heading')
    // Preview hidden initially.
    expect(
      (w.get('[data-testid="prompt-preview"]').element as HTMLElement)
        .style.display,
    ).toBe('none')
    await w.get('[data-testid="preview-toggle"]').trigger('click')
    expect(
      (w.get('[data-testid="prompt-preview"]').element as HTMLElement)
        .style.display,
    ).not.toBe('none')
    expect(w.find('.md-stub').text()).toContain('# Heading')
  })
})
