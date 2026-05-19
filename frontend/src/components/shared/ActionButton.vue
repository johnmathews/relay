<script setup lang="ts">
// A button with explicit `loading` and `disabled` states, used by forms
// and actions across the app. While `loading` it is disabled and shows a
// busy affordance; the default slot is the label.
//
// Props:
//   loading?: boolean   — show busy state + disable (default false)
//   disabled?: boolean  — disable without busy state (default false)
//   type?: 'button' | 'submit'  — native button type (default 'button')
// Slots:
//   default — the button label.
// Emits:
//   click — forwarded native click (only when enabled).

const props = withDefaults(
  defineProps<{
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit'
  }>(),
  { loading: false, disabled: false, type: 'button' },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

function onClick(ev: MouseEvent): void {
  if (props.loading || props.disabled) return
  emit('click', ev)
}
</script>

<template>
  <button
    class="action-button"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading"
    @click="onClick"
  >
    <span
      v-if="loading"
      class="action-button__spinner"
      aria-hidden="true"
    />
    <slot />
  </button>
</template>

<style scoped>
.action-button {
  display: inline-flex;
  align-items: center;
  gap: 0.5em;
  padding: 0.45em 0.9em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-accent);
  color: #07101f;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.action-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.action-button__spinner {
  width: 0.85em;
  height: 0.85em;
  border: 2px solid currentcolor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: action-button-spin 0.7s linear infinite;
}

@keyframes action-button-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
