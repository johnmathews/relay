// W6 FileTree: renders the root listing (dirs-first as the backend
// orders), lazily fetches a directory's children on expand, and emits
// the selected file's path. Network is mocked (no backend).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: { GET: (...a: unknown[]) => GET(...a) },
}))

import FileTree from '../src/components/files/FileTree.vue'
import { projectFileSource } from '../src/lib/queries'

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}

const ROOT = {
  path: '',
  entries: [
    { name: 'src', is_dir: true, size: 0, modified: 1 },
    { name: 'README.md', is_dir: false, size: 12, modified: 2 },
  ],
}
const SRC = {
  path: 'src',
  entries: [{ name: 'main.ts', is_dir: false, size: 9, modified: 3 }],
}

// W7: FileTree is source-agnostic — pass the real project file-browser
// source (its wrapped queries hit the mocked api client, so behaviour is
// unchanged from the pre-W7 `projectId` prop).
function mountTree(): ReturnType<typeof mount> {
  return mount(FileTree, {
    props: { source: projectFileSource(1) },
    global: { plugins: [createPinia(), PiniaColada] },
  })
}

describe('FileTree', () => {
  beforeEach(() => GET.mockReset())

  it('renders root entries dirs-first (backend order preserved)', async () => {
    GET.mockResolvedValue(ok(ROOT))
    const w = mountTree()
    await flushPromises()
    const names = w.findAll('.tree-node__name').map((n) => n.text())
    expect(names).toEqual(['src', 'README.md'])
  })

  it('expanding a directory lazily fetches and shows its children', async () => {
    GET.mockImplementation((...args: unknown[]) => {
      const opts = args[1] as
        | { params?: { query?: { path?: string } } }
        | undefined
      const p = opts?.params?.query?.path
      return Promise.resolve(ok(p === 'src' ? SRC : ROOT))
    })
    const w = mountTree()
    await flushPromises()
    // The src listing has NOT been requested yet (lazy).
    expect(
      GET.mock.calls.some(
        (c) => (c[1] as { params: { query?: { path?: string } } }).params.query?.path === 'src',
      ),
    ).toBe(false)

    // Click the directory row to expand.
    await w.findAll('.tree-node__row')[0].trigger('click')
    await flushPromises()

    expect(
      GET.mock.calls.some(
        (c) => (c[1] as { params: { query?: { path?: string } } }).params.query?.path === 'src',
      ),
    ).toBe(true)
    const names = w.findAll('.tree-node__name').map((n) => n.text())
    expect(names).toContain('main.ts')
  })

  it('selecting a file emits its project-relative path', async () => {
    GET.mockResolvedValue(ok(ROOT))
    const w = mountTree()
    await flushPromises()
    // Second row is README.md (a file).
    await w.findAll('.tree-node__row')[1].trigger('click')
    expect(w.emitted('select')).toBeTruthy()
    expect(w.emitted('select')![0]).toEqual(['README.md'])
  })
})
