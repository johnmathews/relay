<script setup lang="ts">
// Standardizes the loading → error → data UI pattern so every view
// (W3/W4/W5) renders async state consistently instead of reinventing it.
//
// Precedence: loading (first load) → error → default slot. A background
// revalidation should NOT set `loading` (pass an "is first load" flag),
// so stale data stays visible while refreshing.
//
// Props:
//   loading?: boolean       — show the spinner (default false)
//   error?: unknown         — truthy → show the error slot/message
// Slots:
//   default          — the loaded content
//   loading          — optional custom loading UI (overrides spinner)
//   error="{ error }" — optional custom error UI (overrides message)

import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ loading?: boolean; error?: unknown }>(),
  { loading: false, error: undefined },
)

const hasError = computed(() => props.error != null && props.error !== false)

/** Best-effort human message for the default error UI. */
const errorMessage = computed(() => {
  const e = props.error
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  return 'Something went wrong.'
})
</script>

<template>
  <div class="async-boundary">
    <slot
      v-if="loading"
      name="loading"
    >
      <div
        class="async-boundary__loading"
        role="status"
      >
        <span
          class="async-boundary__spinner"
          aria-hidden="true"
        />
        Loading…
      </div>
    </slot>
    <slot
      v-else-if="hasError"
      name="error"
      :error="error"
    >
      <div
        class="async-boundary__error"
        role="alert"
      >
        {{ errorMessage }}
      </div>
    </slot>
    <slot v-else />
  </div>
</template>

<style scoped>
.async-boundary__loading,
.async-boundary__error {
  display: flex;
  align-items: center;
  gap: 0.5em;
  padding: 1rem 0;
  color: var(--color-text-dim);
}

.async-boundary__error {
  color: #ff6b6b;
}

.async-boundary__spinner {
  width: 1em;
  height: 1em;
  border: 2px solid currentcolor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: async-boundary-spin 0.7s linear infinite;
}

@keyframes async-boundary-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
