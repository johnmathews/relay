<script setup lang="ts">
// A run-status pill. Renders the status TEXT (never color-only — the
// label is always present for accessibility) with a distinct color
// treatment per known status. Unknown status strings render with a
// neutral fallback treatment and never throw.
//
// Props:
//   status: string  — a run status. Known: running | done | failed |
//                      paused | cancelled | awaiting_children. Any
//                      other string is allowed and falls back to the
//                      "unknown" treatment.

import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const KNOWN = new Set([
  'running',
  'done',
  'failed',
  'paused',
  'cancelled',
  'awaiting_children',
])

/** CSS modifier class; falls back to `--unknown` for any other string. */
const variant = computed(() =>
  KNOWN.has(props.status) ? props.status : 'unknown',
)
</script>

<template>
  <span
    class="status-badge"
    :class="`status-badge--${variant}`"
    :data-status="status"
  >
    {{ status }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-block;
  padding: 0.15em 0.6em;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: lowercase;
  border: 1px solid currentcolor;
  white-space: nowrap;
}

.status-badge--running {
  color: var(--color-accent);
}

.status-badge--done {
  color: var(--color-success);
}

.status-badge--failed {
  color: var(--color-danger);
}

.status-badge--paused {
  color: var(--color-warning);
}

.status-badge--cancelled {
  color: var(--color-text-dim);
}

/* Amber/orange — distinct from `paused` (yellow) and `running` (blue)
   so a parent suspended on its children reads as a different state at
   a glance. See ADR-34. */
.status-badge--awaiting_children {
  color: var(--color-running);
}

.status-badge--unknown {
  color: var(--color-text-dim);
}
</style>
