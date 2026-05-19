// W6 FileViewer: dispatches the selected file by type. Markdown →
// MarkdownRender; recognised code ext → CodeRender; binary (415) →
// "binary content (N bytes) — download" link with the raw href;
// 413/404 → friendly messages. Network mocked.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: { GET: (...a: unknown[]) => GET(...a) },
}))
// Stub the heavy render children — we test FileViewer's dispatch, not
// the pipeline (covered by render.spec.ts).
vi.mock('../src/components/files/MarkdownRender.vue', () => ({
  default: { name: 'MarkdownRender', props: ['source'], template: '<div class="stub-md">{{ source }}</div>' },
}))
vi.mock('../src/components/files/CodeRender.vue', () => ({
  default: { name: 'CodeRender', props: ['source', 'lang'], template: '<div class="stub-code" :data-lang="lang">{{ source }}</div>' },
}))

import FileViewer from '../src/components/files/FileViewer.vue'
import { projectFileSource } from '../src/lib/queries'

// W7: FileViewer is source-agnostic — pass the real project source
// (project id 7, as before; its content query hits the mocked client).
function mountViewer(path: string | null): ReturnType<typeof mount> {
  return mount(FileViewer, {
    props: { source: projectFileSource(7), path },
    global: { plugins: [createPinia(), PiniaColada] },
  })
}

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return { data, error: undefined, response: new Response(null, { status: 200 }) }
}
function err(
  status: number,
  body: unknown,
): { data: undefined; error: unknown; response: Response } {
  return { data: undefined, error: body, response: new Response(null, { status }) }
}

describe('FileViewer', () => {
  beforeEach(() => GET.mockReset())

  it('idle when no file is selected', () => {
    const w = mountViewer(null)
    expect(w.text()).toContain('Select a file')
  })

  it('markdown file renders via MarkdownRender', async () => {
    GET.mockResolvedValue(
      ok({ path: 'a.md', content: '# Hi', size: 4, modified: 1 }),
    )
    const w = mountViewer('a.md')
    await flushPromises()
    expect(w.find('.stub-md').exists()).toBe(true)
    expect(w.find('.stub-md').text()).toBe('# Hi')
  })

  it('code file renders via CodeRender with the mapped language', async () => {
    GET.mockResolvedValue(
      ok({ path: 's.py', content: 'x=1', size: 3, modified: 1 }),
    )
    const w = mountViewer('s.py')
    await flushPromises()
    const code = w.find('.stub-code')
    expect(code.exists()).toBe(true)
    expect(code.attributes('data-lang')).toBe('python')
  })

  it('binary (415) shows the download link with the raw href', async () => {
    GET.mockResolvedValue(err(415, { detail: 'binary', size: 2048 }))
    const w = mountViewer('img/logo.png')
    await flushPromises()
    expect(w.text()).toContain('binary content')
    expect(w.text()).toContain('(2048 bytes)')
    const a = w.find('a')
    expect(a.text()).toBe('download')
    expect(a.attributes('href')).toBe('/api/projects/7/files/img/logo.png')
  })

  it('oversized (413) shows a friendly message', async () => {
    GET.mockResolvedValue(err(413, { detail: 'too big' }))
    const w = mountViewer('huge.log')
    await flushPromises()
    expect(w.text()).toContain('too large to display')
  })

  it('absent (404) shows a friendly message', async () => {
    GET.mockResolvedValue(err(404, { detail: 'nope' }))
    const w = mountViewer('gone.txt')
    await flushPromises()
    expect(w.text()).toContain('no longer exists')
  })
})
