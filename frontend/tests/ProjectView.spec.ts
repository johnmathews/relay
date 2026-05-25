import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, computed } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Project, Run, Prompt } from '../src/lib/queries'

const projectData = ref<Project | null>(null)
const runsData = ref<Run[]>([])
const promptsData = ref<Prompt[]>([])
const versionsData = ref<Prompt[]>([])

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}))

const {
  createPromptMutate,
  updatePromptMutate,
  deletePromptMutate,
  deleteProjectMutate,
  deleteRunMutate,
} = vi.hoisted(() => ({
  createPromptMutate: vi.fn(),
  updatePromptMutate: vi.fn(),
  deletePromptMutate: vi.fn(),
  deleteProjectMutate: vi.fn(),
  deleteRunMutate: vi.fn(),
}))

// Stores the most-recently registered filter getter from useRunsQuery so
// tests can re-evaluate it after reactive state changes (e.g. toggling
// the showChildren checkbox). Must be module-level (not vi.hoisted) so
// that the mock factory closure and the test body share the same binding.
let _runsFiltersGetter: (() => unknown) | null = null

vi.mock('@/lib/queries', () => ({
  // Defined inside the factory (hoisted before top-level bindings).
  ApiError: class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`Request failed with status ${status}`)
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  },
  useProjectQuery: () => ({ data: projectData }),
  useRunsQuery: (filters: unknown) => {
    _runsFiltersGetter =
      typeof filters === 'function'
        ? (filters as () => unknown)
        : () => filters
    return { data: runsData }
  },
  usePromptsQuery: () => ({ data: promptsData }),
  usePromptVersionsQuery: () => ({ data: versionsData }),
  useCreatePromptMutation: () => ({
    mutateAsync: createPromptMutate,
    error: ref(null),
    isLoading: ref(false),
  }),
  useUpdatePromptMutation: () => ({
    mutateAsync: updatePromptMutate,
    error: ref(null),
    isLoading: ref(false),
  }),
  useDeletePromptMutation: () => ({
    mutateAsync: deletePromptMutate,
    error: ref(null),
    isLoading: ref(false),
  }),
  useDeleteProjectMutation: () => ({
    mutateAsync: deleteProjectMutate,
    error: ref(null),
    isLoading: ref(false),
  }),
  useDeleteRunMutation: () => ({
    mutateAsync: deleteRunMutate,
    error: ref(null),
    isLoading: ref(false),
  }),
  asAsyncState: () => ({
    isLoading: computed(() => false),
    error: computed(() => null),
  }),
  // W7: the Files pane now hands FileTree/FileViewer a BrowserSource.
  // The real factory just packages the (here-mocked) file queries; in
  // this view-level test we only care that the project-scoped source is
  // built + passed through, so a light stand-in with the same storeId
  // contract suffices.
  projectFileSource: (projectId: number) => ({
    storeId: `project:${projectId}`,
    useListing: () => ({ data: ref(null), error: ref(null), isPending: ref(false) }),
    useContent: () => ({ data: ref(null), error: ref(null), isPending: ref(false) }),
    rawUrl: (p: string) => `/api/projects/${projectId}/files/${p}`,
  }),
}))

import ProjectView from '../src/views/ProjectView.vue'

// Stub W6's file components + MarkdownRender via global stubs — assert
// they are present + project-scoped, not re-test their internals (their
// own specs cover that). Stubs keep the bound props as DOM attributes so
// we can assert the project scoping.
const FileTreeStub = {
  name: 'FileTree',
  props: ['source', 'ariaLabel'],
  template: '<div class="file-tree-stub" :data-store="source?.storeId" />',
}
const FileViewerStub = {
  name: 'FileViewer',
  props: ['source', 'path'],
  template: '<div class="file-viewer-stub" :data-store="source?.storeId" />',
}
const MarkdownRenderStub = {
  name: 'MarkdownRender',
  props: ['source'],
  template: '<div class="md-stub">{{ source }}</div>',
}

function mountView(): ReturnType<typeof mount> {
  setActivePinia(createPinia())
  return mount(ProjectView, {
    props: { id: '7' },
    global: {
      plugins: [createPinia()],
      stubs: {
        FileTree: FileTreeStub,
        FileViewer: FileViewerStub,
        MarkdownRender: MarkdownRenderStub,
      },
    },
  })
}

describe('ProjectView', () => {
  beforeEach(() => {
    push.mockReset()
    createPromptMutate.mockReset()
    updatePromptMutate.mockReset()
    deletePromptMutate.mockReset()
    deleteProjectMutate.mockReset()
    deleteRunMutate.mockReset()
    _runsFiltersGetter = null
    projectData.value = {
      id: 7,
      name: 'Alpha',
      root_path: '/srv/alpha',
    } as unknown as Project
    runsData.value = []
    promptsData.value = []
    versionsData.value = []
  })

  it('renders the header (name + path) and 3 tabs; New run navigates', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('/srv/alpha')
    expect(w.find('[data-testid="tab-runs"]').exists()).toBe(true)
    expect(w.find('[data-testid="tab-prompts"]').exists()).toBe(true)
    expect(w.find('[data-testid="tab-files"]').exists()).toBe(true)

    await w.get('[data-testid="new-run-button"]').trigger('click')
    expect(push).toHaveBeenCalledWith({
      name: 'new-run',
      params: { id: '7' },
    })
  })

  it('Runs pane lists runs with a StatusBadge and navigates on click', async () => {
    runsData.value = [
      {
        id: 'run-9',
        status: 'running',
        prompt_id: 3,
        started_at: '2026-05-19T10:00:00Z',
      } as unknown as Run,
    ]
    const w = mountView()
    await flushPromises()

    const badge = w.find('.status-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('running')

    await w.get('[data-testid="run-row-run-9"]').trigger('click')
    expect(push).toHaveBeenCalledWith({
      name: 'run-detail',
      params: { id: 'run-9' },
    })
  })

  it('Runs pane shows an empty state with no runs', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('No runs for this project yet.')
  })

  // W8: this W5 assertion was extended — the Prompts pane is now the
  // full CRUD pane (PromptList + detail with Edit/Delete/History),
  // selecting still renders the body via MarkdownRender (same testids:
  // `prompt-row-N`, `prompt-detail`, `.md-stub`). Toggle-off behaviour
  // was intentionally dropped (clicking a row now always selects, the
  // detail column shows the body + actions) — the load-bearing W5
  // assertion (list + body-on-select) is preserved.
  it('Prompts pane lists prompts and shows the body when selected', async () => {
    promptsData.value = [
      {
        id: 1,
        name: 'Build it',
        version: 2,
        body: '# Plan\nDo the thing',
      } as unknown as Prompt,
    ]
    const w = mountView()
    await flushPromises()

    await w.get('[data-testid="tab-prompts"]').trigger('click')
    expect(w.text()).toContain('Build it')
    expect(w.text()).toContain('v2')
    // Body not shown until a prompt is selected.
    expect(w.find('.md-stub').exists()).toBe(false)

    await w.get('[data-testid="prompt-row-1"]').trigger('click')
    expect(w.find('.md-stub').exists()).toBe(true)
    expect(w.find('.md-stub').text()).toContain('Do the thing')
    // W8: the read-only view now also offers CRUD actions.
    expect(w.find('[data-testid="prompt-edit"]').exists()).toBe(true)
    expect(w.find('[data-testid="prompt-history"]').exists()).toBe(true)
    expect(w.find('[data-testid="prompt-delete"]').exists()).toBe(true)
  })

  it('Prompts pane shows an empty state with no prompts (via PromptList)', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="tab-prompts"]').trigger('click')
    expect(w.text()).toContain('No saved prompts for this project.')
  })

  it('W8: New prompt opens the create editor; saving selects the new prompt', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="tab-prompts"]').trigger('click')
    await w.get('[data-testid="new-prompt-button"]').trigger('click')

    const editor = w.find('[data-testid="prompt-editor"]')
    expect(editor.exists()).toBe(true)

    createPromptMutate.mockResolvedValue({
      id: 11,
      name: 'Fresh',
      version: 1,
      body: '# fresh body',
    } as unknown as Prompt)
    await w.get('[data-testid="prompt-name"]').setValue('Fresh')
    await w.get('[data-testid="prompt-body"]').setValue('# fresh body')
    await w.get('[data-testid="prompt-editor"]').trigger('submit')
    await flushPromises()

    expect(createPromptMutate).toHaveBeenCalledWith({
      project_id: 7,
      name: 'Fresh',
      body: '# fresh body',
    })
    // Back to the read-only view, the new prompt selected + rendered.
    expect(w.find('[data-testid="prompt-editor"]').exists()).toBe(false)
    expect(w.find('.md-stub').text()).toContain('# fresh body')
  })

  it('W8: Edit bumps a version (PUT via update mutation); old version still readable in history', async () => {
    promptsData.value = [
      {
        id: 1,
        name: 'Build it',
        version: 1,
        body: 'v1 body',
      } as unknown as Prompt,
    ]
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="tab-prompts"]').trigger('click')
    await w.get('[data-testid="prompt-row-1"]').trigger('click')
    await w.get('[data-testid="prompt-edit"]').trigger('click')

    // Name is the identity key → read-only in edit mode.
    expect(w.find('[data-testid="prompt-name-fixed"]').text()).toBe(
      'Build it',
    )
    updatePromptMutate.mockResolvedValue({
      id: 2,
      name: 'Build it',
      version: 2,
      body: 'v2 body',
    } as unknown as Prompt)
    await w.get('[data-testid="prompt-body"]').setValue('v2 body')
    await w.get('[data-testid="prompt-editor"]').trigger('submit')
    await flushPromises()

    // Editing PUTs the version id (snapshot bump), not a destructive update.
    expect(updatePromptMutate).toHaveBeenCalledWith({
      id: 1,
      body: 'v2 body',
    })

    // History preserves >1 version (old v1 still readable).
    versionsData.value = [
      { id: 1, name: 'Build it', version: 1, body: 'v1 body' } as unknown as Prompt,
      { id: 2, name: 'Build it', version: 2, body: 'v2 body' } as unknown as Prompt,
    ]
    await w.get('[data-testid="prompt-history"]').trigger('click')
    const rows = w.findAll('[data-testid^="version-row-"]')
    expect(rows.length).toBe(2)
    await w.get('[data-testid="version-row-1"]').trigger('click')
    expect(
      w.find('[data-testid="version-body"] .md-stub').text(),
    ).toContain('v1 body')
    // History is read-only — no mutation controls inside it.
    expect(
      w.find('[data-testid="versions-readonly"]').exists(),
    ).toBe(true)
  })

  it('W8: Delete asks to confirm (all versions) then calls the delete mutation', async () => {
    promptsData.value = [
      {
        id: 1,
        name: 'Build it',
        version: 1,
        body: 'b',
      } as unknown as Prompt,
    ]
    deletePromptMutate.mockResolvedValue(undefined)
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="tab-prompts"]').trigger('click')
    await w.get('[data-testid="prompt-row-1"]').trigger('click')
    await w.get('[data-testid="prompt-delete"]').trigger('click')

    const confirm = w.find('[data-testid="prompt-delete-confirm"]')
    expect(confirm.exists()).toBe(true)
    expect(confirm.text().toLowerCase()).toContain('all of its versions')

    await w
      .get('[data-testid="prompt-delete-confirm-button"]')
      .trigger('click')
    await flushPromises()
    expect(deletePromptMutate).toHaveBeenCalledWith(1)
    // Selection cleared → back to the pick-a-prompt empty state.
    expect(w.find('.md-stub').exists()).toBe(false)
  })

  it('W8: Unregister confirms (files NOT deleted copy) → DELETE project → navigates to /', async () => {
    deleteProjectMutate.mockResolvedValue(undefined)
    const w = mountView()
    await flushPromises()

    await w.get('[data-testid="unregister-button"]').trigger('click')
    const confirm = w.find('[data-testid="unregister-confirm"]')
    expect(confirm.exists()).toBe(true)
    // The confirm copy must state files on disk are NOT deleted.
    expect(confirm.text()).toContain(
      'does NOT delete any files on disk',
    )

    await w
      .get('[data-testid="unregister-confirm-button"]')
      .trigger('click')
    await flushPromises()
    expect(deleteProjectMutate).toHaveBeenCalledWith(7)
    expect(push).toHaveBeenCalledWith('/')
  })

  it('hides child runs by default and shows them when the toggle is checked', async () => {
    runsData.value = [
      {
        id: 'parent-1',
        status: 'completed',
        prompt_id: null,
        started_at: '2026-05-21T10:00:00Z',
      } as unknown as Run,
    ]
    const w = mountView()
    await flushPromises()

    // One run visible.
    expect(w.findAll('[data-testid^="run-row-"]')).toHaveLength(1)

    // The runs query was called without includeChildren (i.e. falsy).
    expect(
      (_runsFiltersGetter?.() as Record<string, unknown> | undefined)
        ?.includeChildren,
    ).toBeFalsy()

    // The toggle checkbox exists in the Runs pane.
    const checkbox = w.find('[data-testid="show-children-toggle"]')
    expect(checkbox.exists()).toBe(true)

    // Enable the toggle (setValue on a checkbox sets the checked state).
    await checkbox.setValue(true)
    await w.vm.$nextTick()

    // The filter getter now reflects includeChildren: true (the ref
    // inside the component has been updated by the checkbox v-model).
    expect(
      (_runsFiltersGetter?.() as Record<string, unknown> | undefined)
        ?.includeChildren,
    ).toBe(true)
  })

  it('Files pane mounts FileTree + FileViewer with the project source', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="tab-files"]').trigger('click')
    const tree = w.find('.file-tree-stub')
    const viewer = w.find('.file-viewer-stub')
    expect(tree.exists()).toBe(true)
    expect(viewer.exists()).toBe(true)
    // Both render the project-scoped BrowserSource (storeId encodes the
    // project id — the W7 source abstraction).
    expect(tree.attributes('data-store')).toBe('project:7')
    expect(viewer.attributes('data-store')).toBe('project:7')
  })

  it('Runs multi-select: enter mode, select rows, bulk delete invokes mutation per id', async () => {
    runsData.value = [
      {
        id: 'r-done',
        status: 'done',
        prompt_id: null,
        started_at: '2026-05-19T10:00:00Z',
      } as unknown as Run,
      {
        id: 'r-failed',
        status: 'failed',
        prompt_id: null,
        started_at: '2026-05-19T09:00:00Z',
      } as unknown as Run,
      {
        id: 'r-running',
        status: 'running',
        prompt_id: null,
        started_at: '2026-05-19T08:00:00Z',
      } as unknown as Run,
    ]
    deleteRunMutate.mockResolvedValue(undefined)

    const w = mountView()
    await flushPromises()

    // No checkboxes until select mode is entered.
    expect(w.find('[data-testid="run-check-r-done"]').exists()).toBe(false)
    await w.get('[data-testid="runs-select-mode"]').trigger('click')
    expect(w.find('[data-testid="run-check-r-done"]').exists()).toBe(true)

    // Running rows render a disabled checkbox.
    const runningCheck = w.get('[data-testid="run-check-r-running"]')
    expect(
      (runningCheck.element as HTMLInputElement).disabled,
    ).toBe(true)

    // Select two terminal runs.
    await w.get('[data-testid="run-check-r-done"]').trigger('click')
    await w.get('[data-testid="run-check-r-failed"]').trigger('click')
    const btn = w.get('[data-testid="runs-delete-selected"]')
    expect(btn.text()).toContain('Delete selected (2)')

    // Confirm → both ids passed to the mutation.
    await btn.trigger('click')
    await w
      .get('[data-testid="runs-delete-confirm-button"]')
      .trigger('click')
    await flushPromises()

    expect(deleteRunMutate).toHaveBeenCalledTimes(2)
    const calledWith = deleteRunMutate.mock.calls.map(
      (args: unknown[]) => args[0] as string,
    )
    expect(new Set(calledWith)).toEqual(new Set(['r-done', 'r-failed']))
    // Select mode auto-exits on a clean success.
    expect(w.find('[data-testid="run-check-r-done"]').exists()).toBe(false)
  })

  it('Runs multi-select: row click toggles checkbox in select mode (no nav)', async () => {
    runsData.value = [
      {
        id: 'r-done',
        status: 'done',
        prompt_id: null,
        started_at: '2026-05-19T10:00:00Z',
      } as unknown as Run,
    ]
    const w = mountView()
    await flushPromises()

    await w.get('[data-testid="runs-select-mode"]').trigger('click')
    // Click the row (the inner button). Should toggle selection, not navigate.
    await w.get('[data-testid="run-row-r-done"]').trigger('click')
    expect(push).not.toHaveBeenCalled()
    const check = w.get('[data-testid="run-check-r-done"]')
      .element as HTMLInputElement
    expect(check.checked).toBe(true)
  })

  it('tab switching shows exactly one panel at a time (Runs default)', async () => {
    const w = mountView()
    await flushPromises()

    const visible = (sel: string): boolean => {
      const el = w.find(sel).element as HTMLElement
      return el.style.display !== 'none'
    }
    // Default = Runs.
    expect(visible('[data-testid="panel-runs"]')).toBe(true)
    expect(visible('[data-testid="panel-prompts"]')).toBe(false)
    expect(visible('[data-testid="panel-files"]')).toBe(false)

    await w.get('[data-testid="tab-files"]').trigger('click')
    expect(visible('[data-testid="panel-runs"]')).toBe(false)
    expect(visible('[data-testid="panel-files"]')).toBe(true)
  })
})
