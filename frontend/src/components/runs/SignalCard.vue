<script setup lang="ts">
// A `signal_emit` timeline row (spec §3.2 payload `{kind, args}`). The
// parser detecting a handoff/done/pause/phase_start signal is a
// load-bearing moment in a run, so per spec §9.1 / plan.md these rows
// are visually distinctive (banner color) and carry a stable anchor id
// (`#signal-<seq>`) so they are directly linkable.
//
// Args render via EventPayloadView — multi-line fields (e.g. a pause
// `question`) become readable instead of a single escaped `\n`-laden
// JSON line.

import { computed } from 'vue'
import EventPayloadView from './EventPayloadView.vue'

const props = defineProps<{
  /** The event seq — used for the linkable anchor id. */
  seq: number
  /** The signal kind, e.g. `handoff` | `done` | `pause` | `phase_start`. */
  signalKind: string
  /** The signal args object. */
  args: unknown
  /**
   * When true, render without the outer border / background / head row.
   * The timeline step-card already supplies the container chrome plus
   * the kind label in its header; rendering a second outer banner inside
   * it produced visible card-in-card nesting. The anchor id is kept on
   * the root either way so `#signal-<seq>` deep links still resolve.
   */
  embedded?: boolean
}>()

const anchorId = computed(() => `signal-${props.seq}`)

const hasArgs = computed(() => {
  if (props.args == null) return false
  if (typeof props.args === 'object') {
    return Object.keys(props.args as Record<string, unknown>).length > 0
  }
  return true
})
</script>

<template>
  <div
    :id="anchorId"
    class="signal-card"
    :class="{ 'signal-card--embedded': embedded }"
    data-testid="signal-card"
  >
    <div class="signal-card__head">
      <span
        v-if="!embedded"
        class="signal-card__tag"
      >signal</span>
      <span
        v-if="!embedded"
        class="signal-card__kind"
      >{{ signalKind }}</span>
      <a
        class="signal-card__anchor"
        :href="`#${anchorId}`"
        aria-label="Link to this signal"
      >#</a>
    </div>
    <EventPayloadView
      v-if="hasArgs"
      class="signal-card__args"
      :payload="args"
    />
  </div>
</template>

<style scoped>
.signal-card {
  border: 1px solid var(--color-warning);
  border-left-width: 4px;
  border-radius: 6px;
  padding: 0.55rem 0.75rem;
  background: var(--color-warning-bg);
}

/* Embedded inside a timeline step-card's body: drop the outer chrome
   (the step-card supplies it) so we don't render a banner inside a
   banner. The anchor row collapses to a single right-aligned link. */
.signal-card--embedded {
  border: none;
  border-radius: 0;
  padding: 0;
  background: transparent;
}
.signal-card--embedded .signal-card__head {
  justify-content: flex-end;
  margin-bottom: 0.25rem;
}
.signal-card--embedded .signal-card__args {
  margin-top: 0;
}

.signal-card__head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.signal-card__tag {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-warning);
  font-weight: 700;
}

.signal-card__kind {
  font-weight: 600;
  font-family: var(--font-mono);
}

.signal-card__anchor {
  margin-left: auto;
  color: var(--color-text-dim);
  font-size: 0.85em;
}

.signal-card__args {
  margin-top: 0.4rem;
}
</style>
