/* eslint-disable vue/one-component-per-file -- inline test harness
   components, not application SFCs. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

// Mock the typed api client so no backend is needed. Each method returns
// the openapi-fetch shape `{ data, error, response }`.
const GET = vi.fn()
const POST = vi.fn()
const PUT = vi.fn()
const DELETE = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    GET: (...a: unknown[]) => GET(...a),
    POST: (...a: unknown[]) => POST(...a),
    PUT: (...a: unknown[]) => PUT(...a),
    DELETE: (...a: unknown[]) => DELETE(...a),
  },
}))

import {
  keys,
  useProjectsQuery,
  useRegisterProjectMutation,
  useCreateRunMutation,
  useCancelRunMutation,
  useResumeRunMutation,
  useCreatePromptMutation,
  useUpdatePromptMutation,
  useDeletePromptMutation,
  useDeleteProjectMutation,
  usePromptVersionsQuery,
  useFileListingQuery,
  useFileContentQuery,
  fileRawUrl,
  useArtifactListingQuery,
  useArtifactContentQuery,
  artifactRawUrl,
  projectFileSource,
  runArtifactSource,
  ApiError,
} from '../src/lib/queries'
import { useQueryCache } from '@pinia/colada'

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return { data, error: undefined, response: new Response(null, { status: 200 }) }
}

describe('queries layer', () => {
  beforeEach(() => {
    GET.mockReset()
    POST.mockReset()
    PUT.mockReset()
    DELETE.mockReset()
  })

  it('keys factory establishes the documented prefix scheme', () => {
    expect(keys.projects()).toEqual(['projects'])
    expect(keys.project(7)).toEqual(['projects', 7])
    expect(keys.runs()).toEqual(['runs'])
    expect(keys.runList({ projectId: 3, limit: 1 })).toEqual([
      'runs',
      { projectId: 3, limit: 1 },
    ])
    expect(keys.runDetail('r1')).toEqual(['runs', 'detail', 'r1'])
    // W4 events-replay key — nested under ['runs', …].
    expect(
      keys.runEvents('r1', { afterSeq: 0, limit: 500, offset: 0 }),
    ).toEqual(['runs', 'events', 'r1', { afterSeq: 0, limit: 500, offset: 0 }])
    // W3 prompts resource.
    expect(keys.prompts()).toEqual(['prompts'])
    expect(keys.promptList(5)).toEqual(['prompts', { projectId: 5 }])
    expect(keys.promptDetail(9)).toEqual(['prompts', 'detail', 9])
    // W8 read-only version-history key — nested under ['prompts', …].
    expect(keys.promptVersions(9)).toEqual(['prompts', 'versions', 9])
    expect(
      keys.preview({ projectId: 5, source: { promptId: 9 } }),
    ).toEqual(['prompts', 'preview', { projectId: 5, source: { promptId: 9 } }])
    // W6 files resource — listings + content nest under files(id).
    expect(keys.files(3)).toEqual(['files', 3])
    expect(keys.fileTree(3, '')).toEqual(['files', 3, 'tree', ''])
    expect(keys.fileTree(3, 'src')).toEqual(['files', 3, 'tree', 'src'])
    expect(keys.fileContent(3, 'a/b.ts')).toEqual([
      'files',
      3,
      'content',
      'a/b.ts',
    ])
    // W7 artifacts resource — listings + content nest under
    // artifacts(runId) for prefix invalidation (ADR-25).
    expect(keys.artifacts('r1')).toEqual(['artifacts', 'r1'])
    expect(keys.artifactTree('r1', '')).toEqual([
      'artifacts',
      'r1',
      'tree',
      '',
    ])
    expect(keys.artifactTree('r1', 'sub')).toEqual([
      'artifacts',
      'r1',
      'tree',
      'sub',
    ])
    expect(keys.artifactContent('r1', 'plan.md')).toEqual([
      'artifacts',
      'r1',
      'content',
      'plan.md',
    ])
  })

  it('artifact-listing query omits path at the root and sends it otherwise', async () => {
    GET.mockResolvedValue(ok({ path: '', entries: [] }))
    const Comp = defineComponent({
      setup() {
        const root = useArtifactListingQuery('r1', '')
        const sub = useArtifactListingQuery('r1', 'logs')
        return { root, sub }
      },
      render: () => h('div'),
    })
    mount(Comp, { global: { plugins: [createPinia(), PiniaColada] } })
    await flushPromises()
    const calls = GET.mock.calls.filter(
      (c) => c[0] === '/api/runs/{run_id}/artifacts',
    )
    const queries = calls.map(
      (c) => (c[1] as { params: { query?: { path?: string } } }).params.query,
    )
    expect(queries).toContainEqual({})
    expect(queries).toContainEqual({ path: 'logs' })
  })

  it('artifact-content query is disabled until a path is set; 404 → ApiError', async () => {
    GET.mockResolvedValue({
      data: undefined,
      error: { detail: 'no artifacts for run' },
      response: new Response(null, { status: 404 }),
    })
    const Comp = defineComponent({
      setup() {
        const none = useArtifactContentQuery('r1', null)
        const some = useArtifactContentQuery('r1', 'plan.md')
        return { none, some }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    const contentCalls = GET.mock.calls.filter(
      (c) => c[0] === '/api/runs/{run_id}/artifacts/{file_path}',
    )
    expect(contentCalls).toHaveLength(1)
    const e = (
      w.vm as unknown as { some: { error: { value: unknown } } }
    ).some.error.value
    expect(e).toBeInstanceOf(ApiError)
    expect((e as ApiError).status).toBe(404)
  })

  it('artifactRawUrl encodes run id + path segments for the download href', () => {
    expect(artifactRawUrl('run 1', 'dir/a b.bin')).toBe(
      '/api/runs/run%201/artifacts/dir/a%20b.bin',
    )
  })

  it('source factories expose the BrowserSource contract + storeId scope', () => {
    const proj = projectFileSource(3)
    const art = runArtifactSource('r1')
    expect(proj.storeId).toBe('project:3')
    expect(art.storeId).toBe('run:r1')
    expect(proj.rawUrl('a.txt')).toBe('/api/projects/3/files/a.txt')
    expect(art.rawUrl('a.txt')).toBe('/api/runs/r1/artifacts/a.txt')
    expect(typeof proj.useListing).toBe('function')
    expect(typeof art.useContent).toBe('function')
  })

  it('file-listing query omits path at the root and sends it otherwise', async () => {
    GET.mockResolvedValue(ok({ path: '', entries: [] }))
    const Comp = defineComponent({
      setup() {
        const root = useFileListingQuery(7, '')
        const sub = useFileListingQuery(7, 'src')
        return { root, sub }
      },
      render: () => h('div'),
    })
    mount(Comp, { global: { plugins: [createPinia(), PiniaColada] } })
    await flushPromises()
    const calls = GET.mock.calls.filter(
      (c) => c[0] === '/api/projects/{project_id}/files',
    )
    const queries = calls.map(
      (c) => (c[1] as { params: { query?: { path?: string } } }).params.query,
    )
    expect(queries).toContainEqual({})
    expect(queries).toContainEqual({ path: 'src' })
  })

  it('file-content query is disabled until a path is set; 415 → ApiError', async () => {
    GET.mockResolvedValue({
      data: undefined,
      error: { detail: 'binary' },
      response: new Response(null, { status: 415 }),
    })
    const Comp = defineComponent({
      setup() {
        const none = useFileContentQuery(7, null)
        const some = useFileContentQuery(7, 'x.bin')
        return { none, some }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    // Disabled query never hit the network for the null path.
    const contentCalls = GET.mock.calls.filter(
      (c) => c[0] === '/api/projects/{project_id}/files/{file_path}',
    )
    expect(contentCalls).toHaveLength(1)
    const e = (
      w.vm as unknown as { some: { error: { value: unknown } } }
    ).some.error.value
    expect(e).toBeInstanceOf(ApiError)
    expect((e as ApiError).status).toBe(415)
  })

  it('fileRawUrl encodes path segments for the binary-download href', () => {
    expect(fileRawUrl(7, 'dir/sub/a b.png')).toBe(
      '/api/projects/7/files/dir/sub/a%20b.png',
    )
  })

  it('create-run mutation invalidates the runs query on success', async () => {
    GET.mockResolvedValue(ok([]))
    POST.mockResolvedValue(ok({ id: 'run-1', project_id: 5 }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const create = useCreateRunMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { create }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        create: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).create.mutateAsync({ project_id: 5, prompt_id: 9 })
    await flushPromises()
    await nextTick()

    expect(POST).toHaveBeenCalledWith('/api/runs', {
      body: { project_id: 5, prompt_id: 9 },
    })
    const calledWith = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calledWith).toContainEqual({ key: ['runs'] })
  })

  it('register mutation invalidates the projects query on success', async () => {
    GET.mockResolvedValue(ok([]))
    POST.mockResolvedValue(ok({ id: 1, name: 'p', root_path: '/p' }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null

    const Comp = defineComponent({
      setup() {
        const projects = useProjectsQuery()
        const reg = useRegisterProjectMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { projects, reg }
      },
      render() {
        return h('div')
      },
    })

    const pinia = createPinia()
    const w = mount(Comp, { global: { plugins: [pinia, PiniaColada] } })
    await flushPromises()

    await (
      w.vm as unknown as {
        reg: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).reg.mutateAsync({ root_path: '/p', name: 'p' })
    await flushPromises()
    await nextTick()

    expect(POST).toHaveBeenCalledWith('/api/projects', {
      body: { root_path: '/p', name: 'p' },
    })
    expect(invalidateSpy).toHaveBeenCalled()
    const calledWith = (invalidateSpy as unknown as { mock: { calls: unknown[][] } })
      .mock.calls.map((c) => c[0])
    expect(calledWith).toContainEqual({ key: ['projects'] })
  })

  it('cancel mutation invalidates runDetail + runs on success', async () => {
    GET.mockResolvedValue(ok([]))
    POST.mockResolvedValue(ok({ id: 'run-9', status: 'cancelled' }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const cancel = useCancelRunMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { cancel }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        cancel: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).cancel.mutateAsync('run-9')
    await flushPromises()
    await nextTick()

    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/cancel', {
      params: { path: { run_id: 'run-9' } },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['runs', 'detail', 'run-9'] })
    expect(calls).toContainEqual({ key: ['runs'] })
  })

  it('resume mutation sends {answer} and invalidates runDetail + runs', async () => {
    GET.mockResolvedValue(ok([]))
    POST.mockResolvedValue(ok({ id: 'run-9', status: 'running' }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const resume = useResumeRunMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { resume }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        resume: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).resume.mutateAsync({ runId: 'run-9', answer: 'go' })
    await flushPromises()
    await nextTick()

    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/resume', {
      params: { path: { run_id: 'run-9' } },
      body: { answer: 'go' },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['runs', 'detail', 'run-9'] })
    expect(calls).toContainEqual({ key: ['runs'] })
  })

  it('create-prompt mutation POSTs body + invalidates the prompts prefix', async () => {
    GET.mockResolvedValue(ok([]))
    POST.mockResolvedValue(ok({ id: 1, name: 'P', version: 1 }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const create = useCreatePromptMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { create }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        create: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).create.mutateAsync({ project_id: 7, name: 'P', body: '# x' })
    await flushPromises()
    await nextTick()

    expect(POST).toHaveBeenCalledWith('/api/prompts', {
      body: { project_id: 7, name: 'P', body: '# x' },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['prompts'] })
  })

  it('update-prompt mutation PUTs /api/prompts/{id} (snapshot bump) + invalidates prompts', async () => {
    GET.mockResolvedValue(ok([]))
    PUT.mockResolvedValue(ok({ id: 2, name: 'P', version: 2 }))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const update = useUpdatePromptMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { update }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        update: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).update.mutateAsync({ id: 1, body: '# edited' })
    await flushPromises()
    await nextTick()

    // Edits PUT the id (snapshot-bump on the server) with body only.
    expect(PUT).toHaveBeenCalledWith('/api/prompts/{prompt_id}', {
      params: { path: { prompt_id: 1 } },
      body: { body: '# edited' },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['prompts'] })
  })

  it('delete-prompt mutation DELETEs by id + invalidates the prompts prefix', async () => {
    GET.mockResolvedValue(ok([]))
    DELETE.mockResolvedValue(ok(undefined))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const del = useDeletePromptMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { del }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        del: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).del.mutateAsync(1)
    await flushPromises()
    await nextTick()

    expect(DELETE).toHaveBeenCalledWith('/api/prompts/{prompt_id}', {
      params: { path: { prompt_id: 1 } },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['prompts'] })
  })

  it('delete-project mutation DELETEs by id + invalidates the projects prefix', async () => {
    GET.mockResolvedValue(ok([]))
    DELETE.mockResolvedValue(ok(undefined))

    let invalidateSpy: ReturnType<typeof vi.fn> | null = null
    const Comp = defineComponent({
      setup() {
        const del = useDeleteProjectMutation()
        const cache = useQueryCache()
        invalidateSpy = vi.fn(cache.invalidateQueries)
        cache.invalidateQueries = invalidateSpy as typeof cache.invalidateQueries
        return { del }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    await (
      w.vm as unknown as {
        del: { mutateAsync: (v: unknown) => Promise<unknown> }
      }
    ).del.mutateAsync(7)
    await flushPromises()
    await nextTick()

    expect(DELETE).toHaveBeenCalledWith('/api/projects/{project_id}', {
      params: { path: { project_id: 7 } },
    })
    const calls = (
      invalidateSpy as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls.map((c) => c[0])
    expect(calls).toContainEqual({ key: ['projects'] })
  })

  it('prompt-versions query unwraps {versions} and is disabled until an id is set', async () => {
    GET.mockResolvedValue(
      ok({
        versions: [
          { id: 1, name: 'P', version: 1, body: 'v1' },
          { id: 2, name: 'P', version: 2, body: 'v2' },
        ],
      }),
    )
    const Comp = defineComponent({
      setup() {
        const none = usePromptVersionsQuery(null)
        const some = usePromptVersionsQuery(1)
        return { none, some }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    const calls = GET.mock.calls.filter(
      (c) => c[0] === '/api/prompts/{prompt_id}/versions',
    )
    // Disabled (null id) never hit the network; only the real id did.
    expect(calls).toHaveLength(1)
    expect(calls[0]?.[1]).toEqual({ params: { path: { prompt_id: 1 } } })
    const data = (
      w.vm as unknown as { some: { data: { value: unknown } } }
    ).some.data.value
    expect(data).toEqual([
      { id: 1, name: 'P', version: 1, body: 'v1' },
      { id: 2, name: 'P', version: 2, body: 'v2' },
    ])
  })

  it('unwrap throws ApiError carrying status + body on error response', async () => {
    GET.mockResolvedValue({
      data: undefined,
      error: { detail: 'boom' },
      response: new Response(null, { status: 404 }),
    })

    const Comp = defineComponent({
      setup() {
        const q = useProjectsQuery()
        return { q }
      },
      render: () => h('div'),
    })
    const w = mount(Comp, {
      global: { plugins: [createPinia(), PiniaColada] },
    })
    await flushPromises()
    const captured: unknown = (
      w.vm as unknown as { q: { error: { value: unknown } } }
    ).q.error.value
    expect(captured).toBeInstanceOf(Error)
    expect((captured as { status: number }).status).toBe(404)
    expect((captured as Error).message).toBe('boom')
  })
})
