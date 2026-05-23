import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'

const POST = vi.fn()
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    POST: (...a: unknown[]) => POST(...a),
    GET: (...a: unknown[]) => GET(...a),
  },
}))

// 14c — the artifact-write mutation uses raw `fetch()` (the hand-rolled
// PUT backend route has no Pydantic body model, so openapi-fetch's
// typed `api.PUT` would refuse a body field — see queries.ts comment).
// We stub `globalThis.fetch` per-test to control the PUT outcome.
// Stub the markdown render so we don't pay shiki/mermaid cost in tests
// (covered separately by render.spec.ts).
vi.mock('@/components/files/MarkdownRender.vue', () => ({
  default: {
    name: 'MarkdownRender',
    props: ['source'],
    template: '<div class="stub-md">{{ source }}</div>',
  },
}))

import PauseAnswerForm from '../src/components/runs/PauseAnswerForm.vue'

function ok<T>(data: T): { data: T; error: undefined; response: Response } {
  return {
    data,
    error: undefined,
    response: new Response(null, { status: 200 }),
  }
}
function errResp(
  status: number,
  body: unknown,
): { data: undefined; error: unknown; response: Response } {
  return {
    data: undefined,
    error: body,
    response: new Response(null, { status }),
  }
}

interface FormProps {
  runId: string
  question: string
  /** 14c-era single-path shape — translated to a one-element
   *  `reviewPaths` array internally. Kept for test ergonomics; the
   *  component prop itself is plural (14f / ADR-41). */
  reviewPath?: string | null
  reviewPaths?: string[]
}

function mountForm(
  props: FormProps = {
    runId: 'run-1',
    question: 'Which DB should I use?',
  },
): ReturnType<typeof mount> {
  const { reviewPath, reviewPaths, ...rest } = props
  const paths: string[] =
    reviewPaths != null
      ? reviewPaths
      : typeof reviewPath === 'string' && reviewPath !== ''
        ? [reviewPath]
        : []
  return mount(PauseAnswerForm, {
    props: { ...rest, reviewPaths: paths },
    global: { plugins: [createPinia(), PiniaColada] },
  })
}

/** Build a fake `fetch` Response that resolves with the given JSON. */
function fetchOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function fetchErr(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('PauseAnswerForm — minimal mode (no review_path)', () => {
  beforeEach(() => {
    POST.mockReset()
    GET.mockReset()
  })

  it('renders the pause question from run-detail', () => {
    const w = mountForm()
    expect(w.find('[data-testid="pause-question"]').text()).toBe(
      'Which DB should I use?',
    )
  })

  it('submit calls resume with {answer} and emits resumed on success', async () => {
    POST.mockResolvedValue({
      data: { id: 'run-1', status: 'running' },
      error: undefined,
      response: new Response(null, { status: 200 }),
    })
    const w = mountForm()
    await w.find('[data-testid="pause-answer-input"]').setValue('use sqlite')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(POST).toHaveBeenCalledWith('/api/runs/{run_id}/resume', {
      params: { path: { run_id: 'run-1' } },
      body: { answer: 'use sqlite' },
    })
    expect(w.emitted('resumed')).toBeTruthy()
  })

  it('shows a 409 inline and does NOT emit resumed', async () => {
    POST.mockResolvedValue({
      data: undefined,
      error: { detail: 'run is not paused' },
      response: new Response(null, { status: 409 }),
    })
    const w = mountForm()
    await w.find('[data-testid="pause-answer-input"]').setValue('x')
    await w.find('form').trigger('submit')
    await flushPromises()

    const err = w.find('[data-testid="pause-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('run is not paused')
    expect(w.emitted('resumed')).toBeFalsy()
  })

  it('requires a non-empty answer', async () => {
    const w = mountForm()
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="pause-error"]').text()).toContain(
      'answer is required',
    )
    expect(POST).not.toHaveBeenCalled()
  })
})

// ── 14c — Review pane ────────────────────────────────────────────────
//
// Regression contract: a paused run WITHOUT `review_path` must look
// byte-for-byte the same as the pre-14c form (case 1 is the guard).
// When `review_path` is present the form fetches the artifact, exposes
// a textarea + preview, and lights up Save/Discard; Resume stays
// present and labelled "Resume run", with `disabled` only while a Save
// is in flight (proposal §"Tradeoffs" choice (a)).

describe('PauseAnswerForm — review pane (14c)', () => {
  let originalFetch: typeof globalThis.fetch | undefined
  const fetchMock = vi.fn()

  beforeEach(() => {
    POST.mockReset()
    GET.mockReset()
    fetchMock.mockReset()
    originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch
  })

  afterEach(() => {
    if (originalFetch != null) globalThis.fetch = originalFetch
  })

  it('review pane absent when reviewPath is null', async () => {
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: null,
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-pane"]').exists()).toBe(false)
    // Minimal form is intact.
    expect(w.find('[data-testid="pause-question"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-answer-input"]').exists()).toBe(true)
    expect(GET).not.toHaveBeenCalled()
  })

  it('review pane fetches and renders the artifact on mount', async () => {
    GET.mockResolvedValue(
      ok({
        path: 'plan.md',
        content: '# Original\n',
        size: 11,
        modified: 1,
      }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'Approve plan?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-pane"]').exists()).toBe(true)
    const ta = w.find(
      '[data-testid="pause-review-textarea"]',
    ).element as HTMLTextAreaElement
    expect(ta.value).toBe('# Original\n')
    // The path is rendered in the header.
    expect(w.text()).toContain('plan.md')
  })

  it('Save fires PUT with the textarea content', async () => {
    GET.mockResolvedValue(
      ok({
        path: 'plan.md',
        content: '# Original\n',
        size: 11,
        modified: 1,
      }),
    )
    fetchMock.mockResolvedValue(
      fetchOk({ path: 'plan.md', size: 14, sha256: '9b1e' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w
      .find('[data-testid="pause-review-textarea"]')
      .setValue('# Edited\n')
    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/runs/run-1/artifacts/plan.md')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({
      content: '# Edited\n',
    })
  })

  it('Save success shows "Edited at" badge and clears dirty', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'a', size: 1, modified: 1 }),
    )
    fetchMock.mockResolvedValue(
      fetchOk({ path: 'plan.md', size: 1, sha256: 'x' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('b')
    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    const badge = w.find('[data-testid="pause-review-saved-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toMatch(/Edited at \d/)
    // Save button is back to disabled (dirty cleared).
    const save = w.find('[data-testid="pause-review-save"]')
      .element as HTMLButtonElement
    expect(save.disabled).toBe(true)
  })

  it('Resume disabled while Save in flight', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'a', size: 1, modified: 1 }),
    )
    let resolveFetch: (v: Response) => void = () => {}
    fetchMock.mockReturnValue(
      new Promise<Response>((r) => {
        resolveFetch = r
      }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('b')
    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    const submit = w.find('[data-testid="pause-resume-submit"]')
      .element as HTMLButtonElement
    expect(submit.disabled).toBe(true)

    resolveFetch(fetchOk({ path: 'plan.md', size: 1, sha256: 'x' }))
    await flushPromises()

    expect(submit.disabled).toBe(false)
  })

  it('404 on initial fetch → "Create at this path" banner; empty Save fires PUT', async () => {
    GET.mockResolvedValue(errResp(404, { detail: 'not found' }))
    fetchMock.mockResolvedValue(
      fetchOk({ path: 'new.md', size: 0, sha256: 'e3b0' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'new.md',
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-create"]').exists()).toBe(true)
    // Save button is enabled on 404 even when textarea is empty.
    const save = w.find('[data-testid="pause-review-save"]')
      .element as HTMLButtonElement
    expect(save.disabled).toBe(false)
    expect(w.find('[data-testid="pause-review-save"]').text()).toContain(
      'Create',
    )

    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/runs/run-1/artifacts/new.md')
    expect(JSON.parse(init.body as string)).toEqual({ content: '' })
  })

  it('415 on initial fetch → binary message + download link', async () => {
    GET.mockResolvedValue(errResp(415, { detail: 'binary' }))
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'diagram.png',
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-textarea"]').exists()).toBe(
      false,
    )
    const dl = w.find('[data-testid="pause-review-download"]')
    expect(dl.exists()).toBe(true)
    expect(dl.attributes('href')).toBe(
      '/api/runs/run-1/artifacts/diagram.png',
    )
  })

  it('Save 409 surfaces inline; textarea content preserved', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'a', size: 1, modified: 1 }),
    )
    fetchMock.mockResolvedValue(
      fetchErr(409, { detail: 'path_mismatch' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w
      .find('[data-testid="pause-review-textarea"]')
      .setValue('local edit')
    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    const err = w.find('[data-testid="pause-review-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('path_mismatch')
    // Operator's local edits are preserved (not reset on save failure).
    const ta = w.find('[data-testid="pause-review-textarea"]')
      .element as HTMLTextAreaElement
    expect(ta.value).toBe('local edit')
  })

  it('Discard reloads the loaded baseline', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'original', size: 8, modified: 1 }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('dirty')
    await w.find('[data-testid="pause-review-discard"]').trigger('click')
    await flushPromises()
    const ta = w.find('[data-testid="pause-review-textarea"]')
      .element as HTMLTextAreaElement
    expect(ta.value).toBe('original')
  })
})

// ── 14e — Preview/Diff view-mode toggle ──────────────────────────────
//
// Right pane gains a `[ Preview | Diff ]` segmented control. Diff is
// disabled while the textarea is byte-equal to the loaded baseline;
// when the operator dirties the textarea the Diff tab becomes
// available. Save + Discard return the textarea to clean → the right
// pane snaps back to Preview. The 415 binary path renders no toggle.

describe('PauseAnswerForm — Diff toggle (14e)', () => {
  let originalFetch: typeof globalThis.fetch | undefined
  const fetchMock = vi.fn()

  beforeEach(() => {
    POST.mockReset()
    GET.mockReset()
    fetchMock.mockReset()
    originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch
  })

  afterEach(() => {
    if (originalFetch != null) globalThis.fetch = originalFetch
  })

  it('mounts with Preview active and Diff disabled while clean', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: '# Original\n', size: 11, modified: 1 }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    const toggle = w.find('[data-testid="pause-review-view-toggle"]')
    expect(toggle.exists()).toBe(true)
    const previewBtn = w.find('[data-testid="pause-review-view-preview"]')
      .element as HTMLButtonElement
    const diffBtn = w.find('[data-testid="pause-review-view-diff"]')
      .element as HTMLButtonElement
    expect(previewBtn.getAttribute('aria-selected')).toBe('true')
    expect(diffBtn.disabled).toBe(true)
    // Preview pane is rendered; diff pane is not.
    expect(w.find('[data-testid="pause-review-preview"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(false)
  })

  it('Diff enables once dirty; clicking it switches the right pane', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: '# Original\n', size: 11, modified: 1 }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w
      .find('[data-testid="pause-review-textarea"]')
      .setValue('# Edited\n')
    const diffBtn = w.find('[data-testid="pause-review-view-diff"]')
      .element as HTMLButtonElement
    expect(diffBtn.disabled).toBe(false)
    await w.find('[data-testid="pause-review-view-diff"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-review-preview"]').exists()).toBe(false)
  })

  it('Save success snaps back to Preview and disables Diff', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'a', size: 1, modified: 1 }),
    )
    fetchMock.mockResolvedValue(
      fetchOk({ path: 'plan.md', size: 1, sha256: 'x' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('b')
    await w.find('[data-testid="pause-review-view-diff"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(true)

    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    const diffBtn = w.find('[data-testid="pause-review-view-diff"]')
      .element as HTMLButtonElement
    expect(diffBtn.disabled).toBe(true)
    // Right pane is back to Preview.
    expect(w.find('[data-testid="pause-review-preview"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(false)
  })

  it('Discard returns the right pane to Preview', async () => {
    GET.mockResolvedValue(
      ok({ path: 'plan.md', content: 'original', size: 8, modified: 1 }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'plan.md',
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('dirty')
    await w.find('[data-testid="pause-review-view-diff"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(true)

    await w.find('[data-testid="pause-review-discard"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="pause-review-preview"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-review-diff"]').exists()).toBe(false)
    const diffBtn = w.find('[data-testid="pause-review-view-diff"]')
      .element as HTMLButtonElement
    expect(diffBtn.disabled).toBe(true)
  })

  it('binary (415) renders no view-mode toggle at all', async () => {
    GET.mockResolvedValue(errResp(415, { detail: 'binary' }))
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPath: 'diagram.png',
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-view-toggle"]').exists()).toBe(
      false,
    )
    expect(w.find('[data-testid="pause-review-binary"]').exists()).toBe(true)
  })
})

// ── 14f — Plural review_paths via tab layout (ADR-41) ───────────────
//
// Single-path renders byte-identical to 14c (no tab bar — guarded by
// the first case). N > 1 renders a tab bar; per-tab dirty state is
// independent; Save targets the active tab; Resume is NOT blocked by
// dirty state on non-active tabs (plan §"notes for executing
// session": "abandoned tab should soft-warn but not block Resume").

describe('PauseAnswerForm — multi-path tabs (14f)', () => {
  let originalFetch: typeof globalThis.fetch | undefined
  const fetchMock = vi.fn()

  beforeEach(() => {
    POST.mockReset()
    GET.mockReset()
    fetchMock.mockReset()
    originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch
  })

  afterEach(() => {
    if (originalFetch != null) globalThis.fetch = originalFetch
  })

  it('renders NO tab bar when only one review path is declared', async () => {
    // Byte-identical-to-14c regression: a single-path pause must not
    // silently render a 1-tab bar (plan §"notes for executing session").
    GET.mockResolvedValue(
      ok({ path: 'only.md', content: 'x', size: 1, modified: 1 }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['only.md'],
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-pane"]').exists()).toBe(true)
    expect(w.find('[data-testid="pause-review-tabs"]').exists()).toBe(false)
  })

  it('renders a tab per path when N > 1; first tab active by default', async () => {
    // The component re-fetches when the active tab changes; mock returns
    // the right content per path.
    GET.mockImplementation((_route: string, opts: { params: { path: { file_path: string } } }) => {
      const fp = opts.params.path.file_path
      return Promise.resolve(
        ok({ path: fp, content: `# ${fp}\n`, size: 1, modified: 1 }),
      )
    })
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['a.md', 'b.md'],
    })
    await flushPromises()
    expect(w.find('[data-testid="pause-review-tabs"]').exists()).toBe(true)
    const tabA = w.find('[data-testid="pause-review-tab-a.md"]')
    const tabB = w.find('[data-testid="pause-review-tab-b.md"]')
    expect(tabA.exists()).toBe(true)
    expect(tabB.exists()).toBe(true)
    expect(tabA.attributes('aria-selected')).toBe('true')
    expect(tabB.attributes('aria-selected')).toBe('false')
    const ta = w.find('[data-testid="pause-review-textarea"]')
      .element as HTMLTextAreaElement
    expect(ta.value).toBe('# a.md\n')
  })

  it('switching tabs re-fetches content for the newly-active path', async () => {
    GET.mockImplementation((_route: string, opts: { params: { path: { file_path: string } } }) => {
      const fp = opts.params.path.file_path
      return Promise.resolve(
        ok({ path: fp, content: `# ${fp}\n`, size: 1, modified: 1 }),
      )
    })
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['a.md', 'b.md'],
    })
    await flushPromises()
    await w.find('[data-testid="pause-review-tab-b.md"]').trigger('click')
    await flushPromises()
    const ta = w.find('[data-testid="pause-review-textarea"]')
      .element as HTMLTextAreaElement
    expect(ta.value).toBe('# b.md\n')
    // Active aria-selected flipped.
    expect(
      w.find('[data-testid="pause-review-tab-b.md"]').attributes('aria-selected'),
    ).toBe('true')
  })

  it('per-tab dirty state: a dirty B and clean A renders the * marker on B only', async () => {
    GET.mockImplementation((_route: string, opts: { params: { path: { file_path: string } } }) => {
      const fp = opts.params.path.file_path
      return Promise.resolve(
        ok({ path: fp, content: 'orig', size: 4, modified: 1 }),
      )
    })
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['a.md', 'b.md'],
    })
    await flushPromises()
    // Switch to B, dirty it, switch back to A.
    await w.find('[data-testid="pause-review-tab-b.md"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('edited')
    await w.find('[data-testid="pause-review-tab-a.md"]').trigger('click')
    await flushPromises()

    const tabAText = w
      .find('[data-testid="pause-review-tab-a.md"]')
      .text()
    const tabBText = w
      .find('[data-testid="pause-review-tab-b.md"]')
      .text()
    expect(tabAText).not.toContain('*')
    expect(tabBText).toContain('*')
    // A's textarea still shows its clean baseline (not B's dirty buffer).
    const ta = w.find('[data-testid="pause-review-textarea"]')
      .element as HTMLTextAreaElement
    expect(ta.value).toBe('orig')
  })

  it('Save targets the active tab and clears only that tab\'s dirty state', async () => {
    GET.mockImplementation((_route: string, opts: { params: { path: { file_path: string } } }) => {
      const fp = opts.params.path.file_path
      return Promise.resolve(
        ok({ path: fp, content: 'orig', size: 4, modified: 1 }),
      )
    })
    fetchMock.mockResolvedValue(
      fetchOk({ path: 'a.md', size: 1, sha256: 'x' }),
    )
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['a.md', 'b.md'],
    })
    await flushPromises()
    // Dirty A, switch to B and dirty B too, switch back to A and save.
    await w.find('[data-testid="pause-review-textarea"]').setValue('A-edited')
    await w.find('[data-testid="pause-review-tab-b.md"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('B-edited')
    await w.find('[data-testid="pause-review-tab-a.md"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="pause-review-save"]').trigger('click')
    await flushPromises()

    // PUT was sent to a.md (the active tab), NOT b.md.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/runs/run-1/artifacts/a.md')

    // A is now clean; B remains dirty.
    expect(
      w.find('[data-testid="pause-review-tab-a.md"]').text(),
    ).not.toContain('*')
    expect(w.find('[data-testid="pause-review-tab-b.md"]').text()).toContain(
      '*',
    )
  })

  it('Resume is NOT blocked by unsaved changes on a non-active tab', async () => {
    // The plan rejects "block Resume if any tab is dirty" — an
    // abandoned dirty tab must not strand the operator.
    GET.mockImplementation((_route: string, opts: { params: { path: { file_path: string } } }) => {
      const fp = opts.params.path.file_path
      return Promise.resolve(
        ok({ path: fp, content: 'orig', size: 4, modified: 1 }),
      )
    })
    const w = mountForm({
      runId: 'run-1',
      question: 'q?',
      reviewPaths: ['a.md', 'b.md'],
    })
    await flushPromises()
    // Dirty B but stay on A.
    await w.find('[data-testid="pause-review-tab-b.md"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="pause-review-textarea"]').setValue('dirty')
    await w.find('[data-testid="pause-review-tab-a.md"]').trigger('click')
    await flushPromises()

    const submit = w.find('[data-testid="pause-resume-submit"]')
      .element as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    // The soft warning is visible.
    expect(
      w.find('[data-testid="pause-review-other-dirty"]').exists(),
    ).toBe(true)
  })
})
