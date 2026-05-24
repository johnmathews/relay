import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import DirectoryPicker from '../src/components/projects/DirectoryPicker.vue'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  ;(globalThis as { fetch: unknown }).fetch = fetchMock
})

afterEach(() => {
  ;(globalThis as { fetch?: unknown }).fetch = undefined
})

function fetchOk<T>(body: T): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

describe('DirectoryPicker', () => {
  it('opens on trigger click and fetches the home directory by default', async () => {
    fetchMock.mockResolvedValue(
      fetchOk({
        path: '/Users/john',
        parent: '/Users',
        entries: [
          { name: 'projects', path: '/Users/john/projects' },
          { name: 'docs', path: '/Users/john/docs' },
        ],
      }),
    )
    const w = mount(DirectoryPicker)
    expect(w.find('.dir-picker__panel').exists()).toBe(false)

    await w.get('.dir-picker__trigger').trigger('click')
    await flushPromises()

    expect(w.find('.dir-picker__panel').exists()).toBe(true)
    const url = fetchMock.mock.calls[0]![0] as string
    expect(url).toBe('/api/system/browse?path=~')
    const entries = w.findAll('.dir-picker__entry').map((e) => e.text())
    expect(entries).toEqual(['📁 projects', '📁 docs'])
  })

  it('navigates into a subdirectory when an entry is clicked', async () => {
    fetchMock
      .mockResolvedValueOnce(
        fetchOk({
          path: '/Users/john',
          parent: '/Users',
          entries: [
            { name: 'projects', path: '/Users/john/projects' },
          ],
        }),
      )
      .mockResolvedValueOnce(
        fetchOk({
          path: '/Users/john/projects',
          parent: '/Users/john',
          entries: [{ name: 'relay', path: '/Users/john/projects/relay' }],
        }),
      )

    const w = mount(DirectoryPicker)
    await w.get('.dir-picker__trigger').trigger('click')
    await flushPromises()
    await w.get('.dir-picker__entry').trigger('click')
    await flushPromises()

    const second = fetchMock.mock.calls[1]![0] as string
    expect(second).toBe(
      '/api/system/browse?path=' + encodeURIComponent('/Users/john/projects'),
    )
    expect(w.text()).toContain('relay')
    expect(w.find('code.dir-picker__path').text()).toBe('/Users/john/projects')
  })

  it('emits `select` with the current path when "Select this folder" is clicked', async () => {
    fetchMock.mockResolvedValue(
      fetchOk({
        path: '/Users/john/projects/relay',
        parent: '/Users/john/projects',
        entries: [],
      }),
    )
    const w = mount(DirectoryPicker)
    await w.get('.dir-picker__trigger').trigger('click')
    await flushPromises()

    await w.get('.dir-picker__select').trigger('click')

    expect(w.emitted('select')).toEqual([['/Users/john/projects/relay']])
    // Closes after select.
    expect(w.find('.dir-picker__panel').exists()).toBe(false)
  })

  it('surfaces a friendly error when the fetch fails', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }))
    const w = mount(DirectoryPicker)
    await w.get('.dir-picker__trigger').trigger('click')
    await flushPromises()
    expect(w.find('.dir-picker__error').text()).toContain('HTTP 500')
    // No entries rendered on error.
    expect(w.findAll('.dir-picker__entry')).toHaveLength(0)
  })

  it('does not show the "up" button at filesystem root', async () => {
    fetchMock.mockResolvedValue(
      fetchOk({ path: '/', parent: null, entries: [] }),
    )
    const w = mount(DirectoryPicker)
    await w.get('.dir-picker__trigger').trigger('click')
    await flushPromises()
    expect(w.find('.dir-picker__up').exists()).toBe(false)
  })
})
