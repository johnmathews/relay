<script setup lang="ts">
// Chat header (W4 — docs/proposals/chat-mode.md). Sticky bar at the top
// of the chat view: a back-link to the project, the status badge, the
// project name, a "Close chat" button (POST /api/runs/:id/close), and a
// stubbed "Promote to task" button (W6 — wires the navigation +
// transcript prefill). Close is visible only while the chat is still
// live (paused / running); on a terminal status it disappears so the
// already-closed chat doesn't offer a no-op button.

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, useCloseChatMutation } from '@/lib/queries'
import StatusBadge from '@/components/shared/StatusBadge.vue'

const props = defineProps<{
  /** Chat run id (POST /api/runs/:id/close target). */
  runId: string
  /** Current run status — drives the Close button's visibility. */
  status: string
  /**
   * Project the chat is rooted in, or null while the project query is
   * still in flight. Displayed as the chat's title so the user knows
   * which workspace pi will see.
   */
  project: { id: number; name: string } | null
}>()

const emit = defineEmits<{
  /** Fired after close so the parent can refetch detail. */
  closed: []
  /** Fired on the (stubbed) promote-to-task click. W6 hooks the
   *  prefill; W4 just records intent. */
  promote: []
}>()

const router = useRouter()
const close = useCloseChatMutation()
const closeError = ref<string | null>(null)

/** Hidden once the run reaches any terminal status. `paused` /
 *  `running` (and the rare `awaiting_children` — not produced by chat
 *  mode today but guarded defensively) keep it visible. */
const canClose = computed(
  () =>
    props.status === 'paused' ||
    props.status === 'running' ||
    props.status === 'awaiting_children',
)

async function onClose(): Promise<void> {
  closeError.value = null
  try {
    await close.mutateAsync(props.runId)
    emit('closed')
  } catch (e) {
    closeError.value =
      e instanceof ApiError
        ? e.message
        : 'Failed to close the chat.'
  }
}

function onPromote(): void {
  // W6 will navigate to the new-run wizard with the transcript
  // pre-filled into prompt_body. For W4 we record the intent (the
  // parent listens and shows a one-time toast); no navigation yet.
  emit('promote')
}

function onBack(): void {
  if (props.project == null) return
  void router.push({ name: 'project', params: { id: props.project.id } })
}
</script>

<template>
  <header
    class="chat-header"
    data-testid="chat-header"
  >
    <div class="chat-header__left">
      <button
        v-if="project"
        type="button"
        class="chat-header__back"
        data-testid="chat-header-back"
        :aria-label="`Back to project ${project.name}`"
        @click="onBack"
      >
        ←
      </button>
      <div class="chat-header__title-stack">
        <span class="chat-header__eyebrow">chat</span>
        <h2 class="chat-header__title">
          {{ project?.name ?? '…' }}
        </h2>
      </div>
      <StatusBadge :status="status" />
    </div>

    <div class="chat-header__actions">
      <button
        type="button"
        class="chat-header__action chat-header__action--ghost"
        data-testid="chat-header-promote"
        @click="onPromote"
      >
        Promote to task
      </button>
      <button
        v-if="canClose"
        type="button"
        class="chat-header__action"
        data-testid="chat-header-close"
        :disabled="close.isLoading.value"
        :aria-busy="close.isLoading.value"
        @click="onClose"
      >
        <span v-if="close.isLoading.value">Closing…</span>
        <span v-else>Close chat</span>
      </button>
    </div>

    <div
      v-if="closeError"
      class="chat-header__error"
      role="alert"
      data-testid="chat-header-error"
    >
      {{ closeError }}
    </div>
  </header>
</template>

<style scoped>
.chat-header {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas:
    'left actions'
    'error error';
  align-items: center;
  gap: 0.4rem 1rem;
  padding: 0.65rem 1rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
  position: sticky;
  top: 0;
  z-index: 2;
  backdrop-filter: blur(6px);
}

.chat-header__left {
  grid-area: left;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}

.chat-header__back {
  background: none;
  border: none;
  font: inherit;
  font-size: 1.1rem;
  color: var(--color-text-dim);
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: 6px;
  line-height: 1;
}

.chat-header__back:hover,
.chat-header__back:focus-visible {
  outline: none;
  color: var(--color-text);
  background: color-mix(in oklab, var(--color-text) 6%, transparent);
}

.chat-header__title-stack {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.1;
}

.chat-header__eyebrow {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-text-dim);
}

.chat-header__title {
  margin: 0;
  font-size: 1.02rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-header__actions {
  grid-area: actions;
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.chat-header__action {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.4rem 0.85rem;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.86rem;
  cursor: pointer;
  transition: background-color 120ms ease, border-color 120ms ease;
}

.chat-header__action:hover,
.chat-header__action:focus-visible {
  outline: none;
  border-color: var(--color-accent);
  background: color-mix(in oklab, var(--color-accent) 8%, var(--color-bg));
}

.chat-header__action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.chat-header__action--ghost {
  background: transparent;
  border-color: transparent;
  color: var(--color-text-dim);
}

.chat-header__action--ghost:hover,
.chat-header__action--ghost:focus-visible {
  background: color-mix(in oklab, var(--color-text) 6%, transparent);
  color: var(--color-text);
}

.chat-header__error {
  grid-area: error;
  color: var(--color-danger);
  font-size: 0.85rem;
}
</style>
