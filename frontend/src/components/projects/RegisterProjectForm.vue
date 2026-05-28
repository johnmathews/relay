<script setup lang="ts">
// Register-project form: root_path + display name → POST /api/projects.
// On success it invalidates the projects query (so the hub list
// refreshes), resets, and emits `registered`/`close`. API errors (e.g.
// 4xx) are surfaced inline.
//
// Emits:
//   registered — project successfully registered
//   close      — the form requested to be dismissed

import { ref, computed } from 'vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import DirectoryPicker from '@/components/projects/DirectoryPicker.vue'
import {
  useRegisterProjectMutation,
  ApiError,
} from '@/lib/queries'

const emit = defineEmits<{ registered: []; close: [] }>()

const rootPath = ref('')
const name = ref('')

const register = useRegisterProjectMutation()

const submitting = computed(() => register.isLoading.value)

const errorMessage = computed<string | null>(() => {
  const e: unknown = register.error.value
  if (e == null) return null
  if (e instanceof ApiError || e instanceof Error) return e.message
  return 'Failed to create project.'
})

const canSubmit = computed(
  () =>
    rootPath.value.trim().length > 0 &&
    name.value.trim().length > 0 &&
    !submitting.value,
)

async function onSubmit(): Promise<void> {
  if (!canSubmit.value) return
  try {
    await register.mutateAsync({
      root_path: rootPath.value.trim(),
      name: name.value.trim(),
    })
    // onSuccess (in the mutation) already invalidated the projects query.
    rootPath.value = ''
    name.value = ''
    emit('registered')
    emit('close')
  } catch {
    // Error is reflected via `register.error` → `errorMessage`.
  }
}
</script>

<template>
  <form
    class="register-form"
    @submit.prevent="onSubmit"
  >
    <label class="register-form__field">
      <span class="register-form__label">Root path</span>
      <div class="register-form__path-row">
        <input
          v-model="rootPath"
          type="text"
          name="root_path"
          placeholder="/abs/path/to/project"
          autocomplete="off"
          required
        >
        <DirectoryPicker @select="(p) => (rootPath = p)" />
      </div>
    </label>
    <label class="register-form__field">
      <span class="register-form__label">Name</span>
      <input
        v-model="name"
        type="text"
        name="name"
        placeholder="Display name"
        autocomplete="off"
        required
      >
    </label>
    <p
      v-if="errorMessage"
      class="register-form__error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <div class="register-form__actions">
      <ActionButton
        type="submit"
        :loading="submitting"
        :disabled="!canSubmit"
      >
        Create
      </ActionButton>
      <button
        type="button"
        class="register-form__cancel"
        @click="emit('close')"
      >
        Cancel
      </button>
    </div>
  </form>
</template>

<style scoped>
.register-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 8px;
  padding: 1rem;
  max-width: 480px;
}

.register-form__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.register-form__label {
  font-size: 0.82em;
  color: var(--color-text-dim);
}

.register-form__path-row {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}

.register-form__path-row input {
  flex: 1;
  min-width: 0;
}

.register-form input {
  padding: 0.45em 0.6em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}

.register-form__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.85em;
}

.register-form__actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.register-form__cancel {
  background: none;
  border: none;
  color: var(--color-text-dim);
  font: inherit;
  cursor: pointer;
}

.register-form__cancel:hover {
  color: var(--color-text);
  text-decoration: underline;
}
</style>
