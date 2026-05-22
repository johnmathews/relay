<script setup lang="ts">
// A `harness_session_ended` timeline row (ADR-39). Shows the harness
// session's stop_reason + the summed input/output/cache-read tokens
// across the message-level usage blocks (ADR-18: messages are opaque
// to relay; we just sum the documented numeric keys).

import { computed } from 'vue'
import type { StreamEvent } from '@/stores/events'

const props = defineProps<{ event: StreamEvent }>()

interface UsageBlock {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
}

interface MessageWithUsage {
  usage?: UsageBlock
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

const totals = computed(() => {
  const messages = props.event.payload.messages
  const list: MessageWithUsage[] = Array.isArray(messages)
    ? (messages as MessageWithUsage[])
    : []
  let inputTokens = 0
  let outputTokens = 0
  let cacheRead = 0
  for (const m of list) {
    const u = m.usage ?? {}
    inputTokens += num(u.input_tokens)
    outputTokens += num(u.output_tokens)
    cacheRead += num(u.cache_read_input_tokens)
  }
  return { inputTokens, outputTokens, cacheRead }
})

const stopReason = computed(() => {
  const r = props.event.payload.stop_reason
  return typeof r === 'string' ? r : 'unknown'
})
</script>

<template>
  <div
    class="usage-row"
    :data-stop-reason="stopReason"
  >
    <span class="badge">{{ stopReason }}</span>
    <span class="tokens">
      Σ in {{ totals.inputTokens }} · out {{ totals.outputTokens }} · cache {{ totals.cacheRead }}
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
