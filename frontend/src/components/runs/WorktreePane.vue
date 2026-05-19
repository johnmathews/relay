<script setup lang="ts">
// W7 Worktree pane (spec §9.1) — DELIBERATELY DEGRADED for the MVP.
//
// Scope decision G2 (`.engineering-team/.../260519-phase-4-dashboard-
// scope.md`): the full spec §9.1 worktree pane is "git status, changed
// files, ability to diff individual files". For the MVP that is
// EXPLICITLY DEFERRED — there is no git-status / per-file-diff endpoint,
// no subprocess surface, and we do NOT fabricate git data. This pane
// shows ONLY the read-only `worktree_path` + `branch` already present on
// the run-detail response (W4 `useRunDetailQuery`), copy-friendly, plus
// a clearly-worded note that live git status / per-file diff is
// post-MVP. It consumes props only — it makes NO network/git call.
//
// EXTENSION POINT (post-MVP): when a `GET /api/runs/{id}/worktree`
// (status + changed files + per-file diff) endpoint lands, add a
// `useWorktreeStatusQuery` hook in lib/queries.ts and render its result
// in the `<!-- post-MVP: worktree status … -->` slot below — the
// path/branch header and the component's shape do NOT need to change
// (that is why the status lives in its own marked section).

import { computed } from 'vue'

const props = defineProps<{
  /** Filesystem path of the run's worktree, or `null` if none. */
  worktreePath: string | null
  /** Git branch the run works on, or `null` if none. */
  branch: string | null
}>()

/** No worktree was provisioned for this run (both fields null). */
const hasWorktree = computed(
  () => props.worktreePath != null || props.branch != null,
)
</script>

<template>
  <section
    class="worktree-pane"
    data-testid="worktree-pane"
  >
    <h2 class="worktree-pane__title">
      Worktree
    </h2>

    <p
      v-if="!hasWorktree"
      class="worktree-pane__empty"
      data-testid="worktree-empty"
    >
      No worktree for this run.
    </p>

    <template v-else>
      <dl class="worktree-pane__meta">
        <div>
          <dt>Path</dt>
          <dd
            class="worktree-pane__mono"
            data-testid="worktree-path"
          >
            {{ worktreePath ?? '—' }}
          </dd>
        </div>
        <div>
          <dt>Branch</dt>
          <dd
            class="worktree-pane__mono"
            data-testid="worktree-branch"
          >
            {{ branch ?? '—' }}
          </dd>
        </div>
      </dl>

      <!-- post-MVP: worktree status (git status, changed files,
           per-file diff) renders here once the endpoint exists. See the
           EXTENSION POINT note in <script>. No stub/fake data by
           decision G2. -->
      <p
        class="worktree-pane__note"
        data-testid="worktree-note"
      >
        Live git status and per-file diffs are not available in this
        release — this is a post-MVP enhancement.
      </p>
    </template>
  </section>
</template>

<style scoped>
.worktree-pane {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.worktree-pane__title {
  margin: 0.5rem 0 0;
  font-size: 1.05rem;
}

.worktree-pane__empty {
  color: var(--color-text-dim);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  padding: 1rem;
  margin: 0;
}

.worktree-pane__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}

.worktree-pane__meta dt {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-dim);
}

.worktree-pane__meta dd {
  margin: 0.15rem 0 0;
}

.worktree-pane__mono {
  font-family: var(--font-mono);
  font-size: 0.85em;
  word-break: break-all;
  user-select: all;
}

.worktree-pane__note {
  color: var(--color-text-dim);
  font-size: 0.85em;
  margin: 0;
}
</style>
