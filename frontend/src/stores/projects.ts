// Ephemeral UI state for the projects feature ONLY.
//
// Spec §9.2: Pinia stores hold ephemeral UI state; server data lives in
// the Pinia Colada cache (see `lib/queries.ts`). So this store does NOT
// fetch or cache projects — it only tracks UI toggles (here: whether the
// register-project form is revealed on the hub). It is intentionally
// small; that is correct, not under-built.

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useProjectsUiStore = defineStore('projects-ui', () => {
  /** Whether the "Register project" form is revealed on the hub. */
  const registerFormOpen = ref(false)

  function openRegisterForm(): void {
    registerFormOpen.value = true
  }

  function closeRegisterForm(): void {
    registerFormOpen.value = false
  }

  function toggleRegisterForm(): void {
    registerFormOpen.value = !registerFormOpen.value
  }

  return {
    registerFormOpen,
    openRegisterForm,
    closeRegisterForm,
    toggleRegisterForm,
  }
})
