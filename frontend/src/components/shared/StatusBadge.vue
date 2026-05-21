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
  color: #5b9dff;
}

.status-badge--done {
  color: #4ec9a3;
}

.status-badge--failed {
  color: #ff6b6b;
}

.status-badge--paused {
  color: #e0b341;
}

.status-badge--cancelled {
  color: #9aa1ab;
}

/* Amber/orange — distinct from `paused` (yellow) and `running` (blue)
   so a parent suspended on its children reads as a different state at
   a glance. See ADR-34. */
.status-badge--awaiting_children {
  color: #f08a3e;
}

.status-badge--unknown {
  color: #9aa1ab;
}
</style>
