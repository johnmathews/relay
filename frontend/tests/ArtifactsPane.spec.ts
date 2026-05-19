// W7 ArtifactsPane: mounts the SHARED FileTree+FileViewer wired to the
// run-artifacts source (ADR-25). Mocked listing → tree entries; a
// markdown artifact renders via the markdown pipeline; a binary (415) →
// the download link with the artifacts raw href; a root 404 ("no
// artifacts for run") → the friendly empty state. Network mocked.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: { GET: (...a: unknown[]) => GET(...a) },
}))
// Stub the heavy render children — assert ArtifactsPane reuses the W6
// markdown pipeline (render internals are covered by render.spec.ts).
vi.mock('../src/components/files/MarkdownRender.vue', () => ({
  default: {
    name: 'MarkdownRender',
    props: ['source'],
    template: '<div class="stub-md">{{ source }}</div>',
  },
}))
vi.mock('../src/components/files/CodeRender.vue', () => ({
  default: {
    name: 'CodeRender',
    props: ['source', 'lang'],
    template: '<div class="stub-code" :data-lang="lang">{{ source }}</div>',
  },
}))

import ArtifactsPane from '../src/components/runs/ArtifactsPane.vue'

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return { data, error: undefined, response: new Response(null, { status: 200 }) }
}
function err(
  status: number,
  body: unknown,
): { data: undefined; error: unknown; response: Response } {
  return { data: undefined, error: body, response: new Response(null, { status }) }
}

const ROOT = {
  path: '',
  entries: [
    { name: 'improvement-plan.md', is_dir: false, size: 20, modified: 1 },
    { name: 'diagram.png', is_dir: false, size: 4096, modified: 2 },
  ],
}

function mountPane(): ReturnType<typeof mount> {
  return mount(ArtifactsPane, {
    props: { runId: 'run-1' },
    global: { plugins: [createPinia(), PiniaColada] },
  })
}

describe('ArtifactsPane', () => {
  beforeEach(() => GET.mockReset())

  it('renders the artifacts tree entries from the mocked listing', async () => {
    GET.mockResolvedValue(ok(ROOT))
    const w = mountPane()
    await flushPromises()
    const names = w.findAll('.tree-node__name').map((n) => n.text())
    expect(names).toEqual(['improvement-plan.md', 'diagram.png'])
  })

  it('selecting a markdown artifact renders via the markdown pipeline', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}/artifacts') return Promise.resolve(ok(ROOT))
      // content endpoint
      return Promise.resolve(
        ok({
          path: 'improvement-plan.md',
          content: '# Improvement Plan\n\n- step one',
          size: 28,
          modified: 1,
        }),
      )
    })
    const w = mountPane()
    await flushPromises()
    // First entry is the markdown artifact.
    await w.findAll('.tree-node__row')[0].trigger('click')
    await flushPromises()
    const md = w.find('.stub-md')
    expect(md.exists()).toBe(true)
    // Rendered through the markdown component (not raw escaped text).
    expect(md.text()).toContain('# Improvement Plan')
    expect(md.text()).toContain('step one')
  })

  it('a binary artifact (415) shows the download link with the artifacts raw href', async () => {
    GET.mockImplementation((path: string) => {
      if (path === '/api/runs/{run_id}/artifacts') return Promise.resolve(ok(ROOT))
      return Promise.resolve(err(415, { detail: 'binary', size: 4096 }))
    })
    const w = mountPane()
    await flushPromises()
    // Second entry is the binary (diagram.png).
    await w.findAll('.tree-node__row')[1].trigger('click')
    await flushPromises()
    expect(w.text()).toContain('binary content')
    expect(w.text()).toContain('(4096 bytes)')
    const a = w.find('.file-viewer__binary a')
    expect(a.attributes('href')).toBe(
      '/api/runs/run-1/artifacts/diagram.png',
    )
  })

  it('root 404 ("no artifacts for run") → friendly empty state, no tree', async () => {
    GET.mockResolvedValue(err(404, { detail: 'no artifacts for run' }))
    const w = mountPane()
    await flushPromises()
    expect(w.find('[data-testid="artifacts-empty"]').exists()).toBe(true)
    expect(w.text()).toContain('This run has no artifacts yet.')
    expect(w.find('.file-tree').exists()).toBe(false)
  })
})
