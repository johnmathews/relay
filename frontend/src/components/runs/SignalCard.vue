<script setup lang="ts">
// A `signal_emit` timeline row (spec §3.2 payload `{kind, args}`). The
// parser detecting a handoff/done/pause/phase_start signal is a
// load-bearing moment in a run, so per spec §9.1 / plan.md these rows
// are visually distinctive (banner color) and carry a stable anchor id
// (`#signal-<seq>`) so they are directly linkable.
//
// Rendering is intentionally minimal (text + <pre> for args) — the real
// markdown pipeline is W6.

import { computed } from 'vue'

const props = defineProps<{
  /** The event seq — used for the linkable anchor id. */
  seq: number
  /** The signal kind, e.g. `handoff` | `done` | `pause` | `phase_start`. */
  signalKind: string
  /** The signal args object. */
  args: unknown
}>()

const anchorId = computed(() => `signal-${props.seq}`)

const argsText = computed(() => {
  if (props.args == null) return ''
  try {
    return JSON.stringify(props.args, null, 2)
  } catch {
    return String(props.args)
  }
})
</script>

<template>
  <div
    :id="anchorId"
    class="signal-card"
    data-testid="signal-card"
  >
    <div class="signal-card__head">
      <span class="signal-card__tag">signal</span>
      <span class="signal-card__kind">{{ signalKind }}</span>
      <a
        class="signal-card__anchor"
        :href="`#${anchorId}`"
        aria-label="Link to this signal"
      >#</a>
    </div>
    <pre
      v-if="argsText !== ''"
      class="signal-card__args"
    >{{ argsText }}</pre>
  </div>
</template>

<style scoped>
.signal-card {
  border: 1px solid #e0b341;
  border-left-width: 4px;
  border-radius: 6px;
  padding: 0.55rem 0.75rem;
  background: rgba(224, 179, 65, 0.08);
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
  color: #e0b341;
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
  margin: 0.4rem 0 0;
  padding: 0.5rem;
  border-radius: 4px;
  background: var(--color-bg);
  font-family: var(--font-mono);
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
