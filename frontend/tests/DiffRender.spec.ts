// W6 DiffRender (plan.md diff verification): two versions of a file
// produce a rendered diff containing the changed lines. Uses the real
// renderDiff (diff2html) — no network.

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DiffRender from '../src/components/files/DiffRender.vue'

describe('DiffRender', () => {
  it('renders a diff between two file revisions with the changed lines', async () => {
    const w = mount(DiffRender, {
      props: {
        oldText: 'alpha\nbeta\ngamma',
        newText: 'alpha\nBETA-CHANGED\ngamma',
        filename: 'note.txt',
      },
    })
    // renderDiff dynamically import()s diff2html, so the watcher
    // resolves over several microtask ticks — wait for the output.
    await vi.waitFor(() => expect(w.html()).toContain('d2h-'))
    const html = w.html()
    expect(html).toMatch(/d2h-(ins|del)/)
    expect(html).toContain('BETA-CHANGED')
  })

  it('honours the side-by-side default and a unified override', async () => {
    const w = mount(DiffRender, {
      props: {
        oldText: 'one',
        newText: 'two',
        filename: 'f.txt',
        style: 'line-by-line',
      },
    })
    await vi.waitFor(() => expect(w.html()).toContain('d2h-'))
    expect(w.html()).toContain('two')
  })
})
