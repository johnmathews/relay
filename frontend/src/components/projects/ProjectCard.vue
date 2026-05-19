<script setup lang="ts">
// One project card on the hub. Owns the N+1 "latest run status" query
// for its project (list runs filtered by project_id, limit 1, newest
// first) — accepted at single-user scale per the Phase-4 scope note.
//
// Props:
//   project: Project — the project to render.

import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useRunsQuery, type Project } from '@/lib/queries'

const props = defineProps<{ project: Project }>()

// `GET /api/runs` returns rows newest-first; limit 1 → the latest run.
const latestRuns = useRunsQuery(() => ({
  projectId: props.project.id,
  limit: 1,
}))

const latestStatus = computed<string | null>(
  () => latestRuns.data.value?.[0]?.status ?? null,
)
</script>

<template>
  <article class="project-card">
    <header class="project-card__head">
      <h3 class="project-card__name">
        {{ project.name }}
      </h3>
      <StatusBadge
        v-if="latestStatus"
        :status="latestStatus"
      />
      <span
        v-else
        class="project-card__no-runs"
      >no runs yet</span>
    </header>
    <p class="project-card__path">
      {{ project.root_path }}
    </p>
    <div class="project-card__actions">
      <RouterLink
        class="project-card__action"
        :to="{ name: 'project', params: { id: project.id } }"
      >
        Open
      </RouterLink>
      <RouterLink
        class="project-card__action"
        :to="{ name: 'new-run', params: { id: project.id } }"
      >
        New run
      </RouterLink>
    </div>
  </article>
</template>

<style scoped>
.project-card {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.project-card__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.project-card__name {
  margin: 0;
  font-size: 1rem;
  flex: 1;
}

.project-card__no-runs {
  font-size: 0.78em;
  color: var(--color-text-dim);
}

.project-card__path {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.82em;
  color: var(--color-text-dim);
  word-break: break-all;
}

.project-card__actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.25rem;
}
</style>
