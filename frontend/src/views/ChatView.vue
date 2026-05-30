<script setup lang="ts">
// Chat detail view (`/chats/:id`; W4 — docs/proposals/chat-mode.md).
//
// The conversational counterpart to RunDetailView. Same backend
// resources (a `runs` row with mode="chat", the same events table, the
// same SSE stream), different render shape. Three components compose
// the surface:
//   • ChatHeader     — title, status badge, Close, Promote-to-task.
//   • ChatTranscript — folded user/assistant turns over the events.
//   • ChatInput      — textarea + Send wired to useResumeRunMutation.
//
// Orchestration mirrors RunDetailView's vertical slice (intentional —
// the SSE/replay machinery in `stores/events.ts` is invariant across
// modes; chat mode just renders the same stream differently):
//   1. Fetch run detail via `useRunDetailQuery`.
//   2. Once detail lands, hand its status to `eventsStore.open()`.
//      Terminal → REST replay (no SSE). Otherwise → live SSE via the
//      W1 wrapper (the store does subscribe-replay-cutover).
//   3. The store pings `onLifecycle` on lifecycle events; we refetch
//      detail there. On a terminal refetch we `markTerminal()` so a
//      finished-chat EOF cannot reconnect-storm.
//   4. ChatInput's `sent` → refetch detail + reopen the stream from
//      the current cursor (gap-free, matches PauseAnswerForm's
//      `resumed` flow).
//   5. ChatHeader's `closed` → refetch detail (the events store will
//      route the upcoming `run_ended` event through onLifecycle, but
//      the explicit refetch shortens the perceived latency).
//
// Wrong-view guard: if a TASK-mode run was opened via /chats/:id
// (deep link from a stale source) redirect back to /runs/:id. Mirrors
// RunDetailView's symmetric guard.

import { computed, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import ChatHeader from '@/components/chat/ChatHeader.vue'
import ChatTranscript from '@/components/chat/ChatTranscript.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import {
  useRunDetailQuery,
  useProjectQuery,
  useInvalidate,
  asAsyncState,
  type RunDetail,
} from '@/lib/queries'
import { useEventsStore } from '@/stores/events'
import { useCurrentRunStore } from '@/stores/currentRun'
import {
  buildPromotionPrompt,
  promotionStorageKey,
} from '@/lib/promotion'

const props = defineProps<{ id: string }>()

const router = useRouter()
const detailQuery = useRunDetailQuery(() => props.id)
const detail = computed<RunDetail | null>(
  () => detailQuery.data.value ?? null,
)
const { isLoading, error } = asAsyncState(detailQuery)

const eventsStore = useEventsStore()
const currentRun = useCurrentRunStore()
const invalidate = useInvalidate()

// Wrong-view guard: a non-chat run opened via /chats/:id redirects to
// the task-mode surface. Uses `replace` so the back button skips the
// wrong-view entry. `immediate` so an already-loaded (Colada cache hit)
// detail still triggers the redirect on mount.
watch(
  () => detail.value?.mode,
  (mode) => {
    if (mode != null && mode !== 'chat') {
      void router.replace({ name: 'run-detail', params: { id: props.id } })
    }
  },
  { immediate: true },
)

// Project lookup so the header shows "which project am I in".
const projectQuery = useProjectQuery(() => detail.value?.project_id ?? 0)
const project = computed(() => {
  if (detail.value == null) return null
  const p = projectQuery.data.value
  if (p == null) return null
  return { id: p.id, name: p.name }
})

const TERMINAL = new Set(['done', 'failed', 'cancelled', 'closed'])

async function onLifecycle(): Promise<void> {
  await detailQuery.refetch()
  if (TERMINAL.has(detail.value?.status ?? '')) {
    eventsStore.markTerminal()
  }
}

let opened = false
watch(
  detail,
  (d) => {
    if (d == null || opened) return
    opened = true
    void eventsStore.open(d.id, d.status, {
      invalidate,
      onLifecycle: () => void onLifecycle(),
    })
  },
  { immediate: true },
)

async function onSent(): Promise<void> {
  // The run just transitioned paused → running. Refetch detail so the
  // status badge + input disabled-state update immediately, then
  // reopen the live stream from the current cursor — same gap-free
  // continuation PauseAnswerForm uses.
  await detailQuery.refetch()
  const d = detail.value
  if (d == null) return
  void eventsStore.open(d.id, d.status, {
    invalidate,
    onLifecycle: () => void onLifecycle(),
  })
}

async function onClosed(): Promise<void> {
  // The Close mutation flipped the run to `closed`. The events store
  // will receive the run_ended event over SSE and ping onLifecycle,
  // but a direct refetch makes the transition feel immediate.
  await detailQuery.refetch()
  if (TERMINAL.has(detail.value?.status ?? '')) {
    eventsStore.markTerminal()
  }
}

function onPromote(): void {
  // W6: navigate to the New Run wizard with the chat transcript
  // pre-filled into `prompt_body`. The handoff travels through
  // sessionStorage rather than a query-string param because long
  // transcripts can exceed browser URL length caps (~2KB-32KB
  // depending on stack). The URL still carries a `?promoteFrom=<runId>`
  // marker so the wizard knows to consult sessionStorage on mount; the
  // wizard removes the entry after reading it, keeping the prefill
  // one-shot (a refresh of the wizard URL won't re-populate).
  //
  // Promotion is non-destructive: the chat stays in its current
  // status. The user may want to keep talking and promote again
  // later, so we do NOT close or cancel it here.
  const d = detail.value
  if (d == null || project.value == null) return
  const body = buildPromotionPrompt({
    events: eventList.value,
    projectName: project.value.name,
  })
  try {
    sessionStorage.setItem(promotionStorageKey(d.id), body)
  } catch {
    // sessionStorage can be disabled (private mode, quota); the wizard
    // will fall back to an empty prompt and the user can paste/type.
    // We still navigate so the affordance isn't silently broken.
  }
  void router.push({
    name: 'new-run',
    params: { id: String(project.value.id) },
    query: { promoteFrom: d.id },
  })
}

const eventList = computed(() => eventsStore.events)
const pendingTurns = computed(() => eventsStore.pendingTurns)

onBeforeUnmount(() => {
  eventsStore.reset()
  currentRun.reset()
})
</script>

<template>
  <section class="chat-view">
    <AsyncBoundary
      :loading="isLoading"
      :error="error"
    >
      <template v-if="detail">
        <ChatHeader
          :run-id="detail.id"
          :status="detail.status"
          :project="project"
          @closed="onClosed"
          @promote="onPromote"
        />
        <ChatTranscript
          :events="eventList"
          :pending-turns="pendingTurns"
          :status="detail.status"
        />
        <ChatInput
          :run-id="detail.id"
          :status="detail.status"
          @sent="onSent"
        />
      </template>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  height: 100%;
  background: var(--color-bg);
}

.chat-view :deep(.async-boundary) {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
</style>
