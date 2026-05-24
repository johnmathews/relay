import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// Behavior-focused: mock the query layer so we assert the form drives
// the register mutation (POST {root_path, name}), surfaces API errors
// inline, and on success emits close/registered. The mutation's own
// onSuccess invalidates the projects query — that invalidate-on-success
// wiring is asserted in queries.spec.ts against the real Colada cache.

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
  const state = {
    mutateAsync:
      vi.fn<(v: { root_path: string; name: string }) => Promise<unknown>>(),
    error: ref<unknown>(null),
    isLoading: ref(false),
  }
  return {
    __state: state,
    ApiError: FakeApiError,
    useRegisterProjectMutation: () => ({
      mutateAsync: state.mutateAsync,
      isLoading: state.isLoading,
      error: state.error,
    }),
  }
})

import * as queries from '@/lib/queries'
import RegisterProjectForm from '../src/components/projects/RegisterProjectForm.vue'

const state = (
  queries as unknown as {
    __state: {
      mutateAsync: ReturnType<typeof vi.fn>
      error: { value: unknown }
      isLoading: { value: boolean }
    }
  }
).__state
const ApiError = (
  queries as unknown as {
    ApiError: new (status: number, message: string) => Error
  }
).ApiError

describe('RegisterProjectForm', () => {
  beforeEach(() => {
    state.mutateAsync.mockReset()
    state.error.value = null
    state.isLoading.value = false
  })

  it('submits {root_path, name} via the register mutation', async () => {
    state.mutateAsync.mockResolvedValue({ id: 1 })
    const w = mount(RegisterProjectForm)

    await w.get('input[name="root_path"]').setValue('/abs/proj')
    await w.get('input[name="name"]').setValue('My Proj')
    await w.get('form').trigger('submit')
    await flushPromises()

    expect(state.mutateAsync).toHaveBeenCalledWith({
      root_path: '/abs/proj',
      name: 'My Proj',
    })
    expect(w.emitted('registered')).toBeTruthy()
    expect(w.emitted('close')).toBeTruthy()
  })

  it('shows an API error inline and does not emit registered', async () => {
    state.mutateAsync.mockImplementation(async () => {
      state.error.value = new ApiError(
        400,
        'root_path is not a directory',
      ) as unknown
      throw state.error.value
    })
    const w = mount(RegisterProjectForm)

    await w.get('input[name="root_path"]').setValue('/bad')
    await w.get('input[name="name"]').setValue('Bad')
    await w.get('form').trigger('submit')
    await flushPromises()

    expect(w.get('[role="alert"]').text()).toContain(
      'root_path is not a directory',
    )
    expect(w.emitted('registered')).toBeFalsy()
  })

  it('does not submit when fields are empty', async () => {
    const w = mount(RegisterProjectForm)
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(state.mutateAsync).not.toHaveBeenCalled()
  })

  it('mounts the directory picker next to the root_path input and the submit button says Create', async () => {
    const w = mount(RegisterProjectForm)
    // Picker trigger sits in the same row as the root_path input.
    expect(w.find('.register-form__path-row').exists()).toBe(true)
    expect(w.find('.dir-picker__trigger').exists()).toBe(true)
    expect(w.get('button[type="submit"]').text()).toBe('Create')
  })

  it('fills the root_path input when the directory picker emits select', async () => {
    state.mutateAsync.mockResolvedValue({ id: 1 })
    const w = mount(RegisterProjectForm)
    // Emit directly from the picker component to avoid driving its
    // internal fetch — the picker's own behaviour is covered in
    // DirectoryPicker.spec.ts.
    const picker = w.findComponent({ name: 'DirectoryPicker' })
    picker.vm.$emit('select', '/picked/path')
    await flushPromises()
    expect(
      (w.get('input[name="root_path"]').element as HTMLInputElement).value,
    ).toBe('/picked/path')
  })
})
