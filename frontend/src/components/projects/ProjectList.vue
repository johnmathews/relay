<script setup lang="ts">
// The hub's list of registered project cards. Owns the projects query;
// per-card latest-run status is fetched by each ProjectCard. Renders
// loading/error via AsyncBoundary and an empty state when there are no
// projects.

import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import ProjectCard from '@/components/projects/ProjectCard.vue'
import { useProjectsQuery, asAsyncState } from '@/lib/queries'

const projects = useProjectsQuery()
const { isLoading, error } = asAsyncState(projects)
</script>

<template>
  <AsyncBoundary
    :loading="isLoading"
    :error="error"
  >
    <p
      v-if="!projects.data.value || projects.data.value.length === 0"
      class="project-list__empty"
    >
      No projects registered yet
    </p>
    <div
      v-else
      class="project-list"
    >
      <ProjectCard
        v-for="p in projects.data.value"
        :key="p.id"
        :project="p"
      />
    </div>
  </AsyncBoundary>
</template>

<style scoped>
.project-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.project-list__empty {
  color: var(--color-text-dim);
  padding: 2rem 0;
}
</style>
