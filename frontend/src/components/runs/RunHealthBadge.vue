<script setup lang="ts">
// ADR-45 Plan A — live-stream liveness indicator.
//
// Pi runs can sit in a silent thinking phase for several minutes before
// the first AssistantText / ToolUse lands; with only the timeline to
// look at, the dashboard appears frozen and there is no way to tell a
// healthy "still thinking" from a hung session. This badge consumes
// the SSE heartbeat the backend emits on idle (events store
// `lastHeartbeat`) plus a once-per-second client clock to render a
// live-ticking "alive · last activity Xs ago" indicator next to the
// status pill.
//
// Renders nothing for a terminal run (no live stream → no heartbeat →
// a stale clock would be misleading). Renders a "connecting" state
// for a live run that has not yet received its first heartbeat.
//
// Thresholds match the backend cadence (5s default; ADR-45):
//   * fresh  (≤15s since heartbeat received)   → "live"
//   * slow   (>15s and ≤60s)                   → "slow"   (amber)
//   * stalled (>60s)                           → "stalled" (red)

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { HeartbeatSnapshot } from '@/stores/events'

const props = defineProps<{
  status: string
  lastHeartbeat: HeartbeatSnapshot | null
}>()

const TERMINAL = new Set(['done', 'failed', 'cancelled'])
const SLOW_MS = 15_000
const STALLED_MS = 60_000

const isTerminal = computed(() => TERMINAL.has(props.status))

// One ticking clock for the component's lifetime. Refreshed every
// second so the "Xs ago" label advances visibly without remounting.
const nowMs = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => {
    nowMs.value = Date.now()
  }, 1_000)
})
onBeforeUnmount(() => {
  if (timer != null) clearInterval(timer)
})

/** ms since the most recent heartbeat arrived at the client. */
const ageMs = computed(() => {
  const hb = props.lastHeartbeat
  if (hb == null) return Number.POSITIVE_INFINITY
  return Math.max(0, nowMs.value - hb.receivedAt)
})

const state = computed<'connecting' | 'live' | 'slow' | 'stalled'>(() => {
  if (props.lastHeartbeat == null) return 'connecting'
  const age = ageMs.value
  if (age > STALLED_MS) return 'stalled'
  if (age > SLOW_MS) return 'slow'
  return 'live'
})

/**
 * Human-friendly "since last activity" — anchored on the server's
 * `last_event_ts` if available (true wall-clock age of pi output),
 * otherwise on the heartbeat receivedAt (covers the "no events yet
 * this connection" case). Both fall through the same ticking clock.
 */
const sinceLabel = computed(() => {
  const hb = props.lastHeartbeat
  if (hb == null) return ''
  const anchor = hb.lastEventTs ? Date.parse(hb.lastEventTs) : hb.receivedAt
  const secs = Math.max(0, Math.floor((nowMs.value - anchor) / 1_000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  const rem = secs % 60
  return rem === 0 ? `${mins}m ago` : `${mins}m ${rem}s ago`
})

const label = computed((): string => {
  switch (state.value) {
    case 'connecting':
      return 'connecting…'
    case 'live':
      return `live · ${sinceLabel.value}`
    case 'slow':
      return `slow · ${sinceLabel.value}`
    case 'stalled':
      return `stalled · ${sinceLabel.value}`
    default:
      return ''
  }
})
</script>

<template>
  <span
    v-if="!isTerminal"
    class="health-badge"
    :class="`health-badge--${state}`"
    :data-state="state"
    data-testid="run-health-badge"
  >
    <span
      class="health-badge__dot"
      aria-hidden="true"
    />
    {{ label }}
  </span>
</template>

<style scoped>
.health-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4em;
  padding: 0.15em 0.6em;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-transform: lowercase;
  border: 1px solid currentcolor;
  white-space: nowrap;
}

.health-badge__dot {
  display: inline-block;
  width: 0.5em;
  height: 0.5em;
  border-radius: 50%;
  background: currentcolor;
}

.health-badge--connecting {
  color: #9aa1ab;
}

.health-badge--live {
  color: #4ec9a3;
}
.health-badge--live .health-badge__dot {
  animation: health-pulse 1.4s ease-in-out infinite;
}

.health-badge--slow {
  color: #e0b341;
}

.health-badge--stalled {
  color: #ff6b6b;
}

@keyframes health-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.3; }
}
</style>
