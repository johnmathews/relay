// W7 WorktreePane: DEGRADED for MVP (scope decision G2). Shows the
// read-only worktree_path + branch from run-detail props plus a
// post-MVP note; nulls → empty state. It is props-only — it must make
// NO network/git call.

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// If the pane ever imported the api client this mock would catch a
// network call; asserting GET was never invoked proves it stays
// props-only (no git/status endpoint — decision G2).
const GET = vi.fn()
vi.mock('@/api/client', () => ({
  api: { GET: (...a: unknown[]) => GET(...a) },
}))

import WorktreePane from '../src/components/runs/WorktreePane.vue'

function mountPane(
  props: { worktreePath: string | null; branch: string | null },
): ReturnType<typeof mount> {
  return mount(WorktreePane, { props })
}

describe('WorktreePane', () => {
  it('shows worktree path + branch read-only with the post-MVP note', () => {
    const w = mountPane({
      worktreePath: '/srv/wt/run-1',
      branch: 'relay/run-1',
    })
    expect(w.find('[data-testid="worktree-path"]').text()).toBe(
      '/srv/wt/run-1',
    )
    expect(w.find('[data-testid="worktree-branch"]').text()).toBe(
      'relay/run-1',
    )
    expect(w.find('[data-testid="worktree-note"]').text()).toContain(
      'post-MVP',
    )
    expect(w.find('[data-testid="worktree-empty"]').exists()).toBe(false)
    expect(GET).not.toHaveBeenCalled()
  })

  it('null path + branch → empty state', () => {
    const w = mountPane({ worktreePath: null, branch: null })
    expect(w.find('[data-testid="worktree-empty"]').exists()).toBe(true)
    expect(w.text()).toContain('No worktree for this run.')
    expect(w.find('[data-testid="worktree-path"]').exists()).toBe(false)
    expect(GET).not.toHaveBeenCalled()
  })

  it('shows the meta block when only one of path/branch is present', () => {
    const w = mountPane({ worktreePath: '/srv/wt/x', branch: null })
    expect(w.find('[data-testid="worktree-empty"]').exists()).toBe(false)
    expect(w.find('[data-testid="worktree-path"]').text()).toBe('/srv/wt/x')
    expect(w.find('[data-testid="worktree-branch"]').text()).toBe('—')
    expect(GET).not.toHaveBeenCalled()
  })
})
