<script setup lang="ts">
// A `harness_session_ended` timeline row (ADR-39). Shows the harness
// session's stop_reason + the summed token counts across the
// assistant-message usage blocks. Mirrors the field names that
// `observability/otel.py::_aggregate_usage` reads from the same
// payload (pi shape: input/output/cacheRead/cacheWrite/totalTokens
// per ADR-18, captured fixtures in scratch/pi_derisk_workdir/).

import { computed } from 'vue'
import type { StreamEvent } from '@/stores/events'

interface UsageBlock {
  input?: number
  output?: number
  cacheRead?: number
  cacheWrite?: number
  totalTokens?: number
  cost?: { total?: number }
}

interface MessageWithUsage {
  role?: string
  usage?: UsageBlock
}

const props = defineProps<{ event: StreamEvent }>()

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

const totals = computed(() => {
  const messages = props.event.payload.messages
  const list: MessageWithUsage[] = Array.isArray(messages)
    ? (messages as MessageWithUsage[])
    : []
  let input = 0
  let output = 0
  let cacheRead = 0
  let cacheWrite = 0
  let cost = 0
  let hasCost = false
  for (const m of list) {
    if (m.role !== 'assistant') continue
    const u = m.usage ?? {}
    input += num(u.input)
    output += num(u.output)
    cacheRead += num(u.cacheRead)
    cacheWrite += num(u.cacheWrite)
    const c = u.cost?.total
    if (typeof c === 'number' && Number.isFinite(c)) {
      cost += c
      hasCost = true
    }
  }
  return { input, output, cacheRead, cacheWrite, cost, hasCost }
})

const stopReason = computed(() => {
  const r = props.event.payload.stop_reason
  return typeof r === 'string' ? r : 'unknown'
})

const costLabel = computed(() => {
  if (!totals.value.hasCost) return ''
  return `$${totals.value.cost.toFixed(4)}`
})
</script>

<template>
  <div
    class="usage-row"
    :data-stop-reason="stopReason"
  >
    <span class="badge">{{ stopReason }}</span>
    <span class="tokens">
      Σ in {{ totals.input }} · out {{ totals.output }} ·
      cache r {{ totals.cacheRead }} / w {{ totals.cacheWrite }}<template v-if="totals.hasCost"> · {{ costLabel }}</template>
    </span>
  </div>
</template>

<style scoped>
.usage-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  padding: 0.25rem 0.5rem;
  font-size: 0.85em;
  color: var(--color-text-muted, #888);
  border-left: 2px solid var(--color-border-subtle, #e0e0e0);
}
.badge {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tokens {
  font-variant-numeric: tabular-nums;
}
</style>
