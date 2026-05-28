<script setup lang="ts">
import { computed } from 'vue'
import { useTheme } from '@/lib/theme'

const { choice, resolved, cycle } = useTheme()

const label = computed(() => {
  switch (choice.value) {
    case 'light':
      return 'Light'
    case 'dark':
      return 'Dark'
    default:
      return `Auto (${resolved.value})`
  }
})

const glyph = computed(() => {
  switch (choice.value) {
    case 'light':
      return '☀'
    case 'dark':
      return '☾'
    default:
      return '◐'
  }
})

const title = computed(
  () =>
    `Theme: ${label.value} — click to cycle (auto → light → dark)`,
)
</script>

<template>
  <button
    type="button"
    class="theme-toggle"
    :title="title"
    :aria-label="title"
    data-testid="theme-toggle"
    @click="cycle"
  >
    <span
      class="theme-toggle__glyph"
      aria-hidden="true"
    >{{ glyph }}</span>
    <span class="theme-toggle__label">{{ label }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.6rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text);
  font: inherit;
  font-size: 0.85em;
  cursor: pointer;
}

.theme-toggle:hover {
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
}

.theme-toggle__glyph {
  font-size: 1em;
  line-height: 1;
}

.theme-toggle__label {
  letter-spacing: 0.02em;
}
</style>
