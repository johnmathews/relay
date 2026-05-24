<script setup lang="ts">
// Hub view (`/`) — spec §9.1: the list of registered projects with each
// project's most-recent run status, plus a "Register project"
// affordance that reveals the register form. Empty state when there are
// no projects.

import { storeToRefs } from 'pinia'
import { useProjectsUiStore } from '@/stores/projects'
import ProjectList from '@/components/projects/ProjectList.vue'
import RegisterProjectForm from '@/components/projects/RegisterProjectForm.vue'

const ui = useProjectsUiStore()
const { registerFormOpen } = storeToRefs(ui)
</script>

<template>
  <section class="hub">
    <header class="hub__header">
      <h1 class="hub__title">
        Projects
      </h1>
      <button
        type="button"
        class="hub__register-toggle"
        @click="ui.toggleRegisterForm()"
      >
        {{ registerFormOpen ? 'Close' : 'New project' }}
      </button>
    </header>

    <RegisterProjectForm
      v-if="registerFormOpen"
      class="hub__form"
      @close="ui.closeRegisterForm()"
    />

    <ProjectList />
  </section>
</template>

<style scoped>
.hub {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 1100px;
}

.hub__header {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.hub__title {
  margin: 0;
  flex: 1;
  font-size: 1.4rem;
}

.hub__register-toggle {
  padding: 0.45em 0.9em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.hub__register-toggle:hover {
  border-color: var(--color-accent);
}
</style>
