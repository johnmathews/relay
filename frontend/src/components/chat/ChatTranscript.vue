<script setup lang="ts">
// Chat transcript (W4 — docs/proposals/chat-mode.md). Renders a
// chronologically-ordered conversation derived from the same event
// stream that powers `TimelinePane.vue` for task-mode runs, but folded
// into the alternating user/assistant shape a chat UI expects.
//
// Event-to-turn mapping (W2 chat resume path — see `core.resume_run`
// and `_finish_iter`'s chat-mode synthetic-pause branch):
//
//   • `pause_resolved` with non-empty `payload.answer`
//       → ONE user turn. The answer text is the message the operator
//         typed into the composer; W2 persists it as the resume
//         payload and the loop spawns iter N+1 with it as the prompt
//         body. The initial `pause_requested` (empty question) before
//         any resume contributes NO turn — the empty transcript is
//         the natural "start typing" state.
//   • Everything between an `iter_started` and the matching
//     `iter_ended` (matched by `payload.seq` — the iter sequence
//     number stamped on both boundaries)
//       → ONE assistant turn. The turn's body is the concatenated
//         `assistant_text` events (kind === 'text', NOT 'thinking' —
//         the chat surface intentionally hides the model's
//         reasoning, mirroring the convention in consumer chat
//         products; the timeline view stays the surface for that
//         data). `tool_use_start` events are interleaved inline as
//         tool chips so the operator sees what pi did between
//         sentences.
//   • An iter with no `assistant_text` events yet (live, pi is mid-
//     turn) renders the live `PendingTurn` stream from the events
//     store — same source `TimelinePane` reads, same content. When
//     the canonical `assistant_text` lands the pending pseudo-row is
//     dropped automatically by the store (ADR-46).
//
// Reused components (do NOT reinvent — load-bearing per the plan):
//   • MarkdownRender — assistant + user text (XSS-safe via markdown-it
//     html:false + shiki).
//   • ToolCallCard   — every tool invocation. Embedded variant so the
//     transcript doesn't paint card-in-card chrome.
//
// Auto-scroll: pinned-to-bottom mirrors TimelinePane's behaviour with
// the same 50px tolerance. A "Jump to latest" affordance surfaces when
// the user has scrolled up while pi is still streaming.

import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { PendingTurn, StreamEvent } from '@/stores/events'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import ToolCallCard from '@/components/runs/ToolCallCard.vue'

const props = defineProps<{
  /** Ordered, deduped event list from the events store. */
  events: ReadonlyArray<StreamEvent>
  /** Pending (in-flight) assistant turns — ADR-46 streaming deltas. */
  pendingTurns?: ReadonlyArray<PendingTurn>
  /** Current run status — drives the "thinking…" indicator copy. */
  status?: string
}>()

// ── Turn folding ─────────────────────────────────────────────────────

interface UserTurn {
  kind: 'user'
  /** Stable key — the originating `pause_resolved` event's seq. */
  key: string
  text: string
}

/**
 * One assistant turn = a (possibly empty) sequence of text segments
 * interleaved with tool calls, in chronological order. The shape is a
 * flat segment list rather than two arrays so the renderer can place
 * tool chips between paragraphs the way pi actually emitted them — a
 * sentence, a Bash call, another sentence reads as a coherent thought.
 */
interface AssistantTurn {
  kind: 'assistant'
  /** Stable key — the iter_started seq. */
  key: string
  /** Iter seq (matches the boundary events' `payload.seq`). */
  iterSeq: number
  /**
   * Iter DB-id. Pending deltas key by iter_id (`PendingTurn.iterId`),
   * so the renderer needs the same id to associate streaming deltas
   * with the iter while it's live.
   */
  iterId: number | null
  segments: TurnSegment[]
  /**
   * True when this iter is still open (no matching `iter_ended` yet).
   * Drives the live cursor / pending-delta merge.
   */
  open: boolean
}

type TurnSegment =
  | { kind: 'text'; key: string; text: string }
  | {
      kind: 'tool'
      key: string
      name: string
      args: unknown
      result?: unknown
      isError?: boolean
      durationMs?: number
      toolId: string | null
    }

type Turn = UserTurn | AssistantTurn

function asStr(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

const turns = computed<Turn[]>(() => {
  const out: Turn[] = []
  let openAssistant: AssistantTurn | null = null
  const toolByIterAndId = new Map<string, { iter: AssistantTurn; segIdx: number }>()

  for (const ev of props.events) {
    const p = ev.payload
    if (ev.kind === 'pause_resolved') {
      const answer = asStr(p.answer)
      // The initial chat pause has no answer yet (the run starts
      // paused with no resume); only emitted resumes contribute a
      // turn. An empty `answer` would be a bug in the backend but is
      // gracefully skipped here so a malformed event doesn't leave a
      // blank user bubble.
      if (answer === '') continue
      out.push({ kind: 'user', key: `e${ev.seq}`, text: answer })
      continue
    }
    if (ev.kind === 'iter_started') {
      const iterSeq = asNum(p.seq) ?? 0
      const iterId = asNum(p.iter_id) ?? null
      const turn: AssistantTurn = {
        kind: 'assistant',
        key: `e${ev.seq}`,
        iterSeq,
        iterId,
        segments: [],
        open: true,
      }
      out.push(turn)
      openAssistant = turn
      continue
    }
    if (ev.kind === 'iter_ended') {
      if (openAssistant != null) openAssistant.open = false
      openAssistant = null
      continue
    }
    if (openAssistant == null) continue

    if (ev.kind === 'assistant_text') {
      // ADR-18: the protocol distinguishes thinking from final text via
      // `payload.kind`. Chat surface hides thinking — that channel
      // stays available in the timeline view but is noise here.
      if (p.kind === 'thinking') continue
      const text = asStr(p.text)
      if (text === '') continue
      openAssistant.segments.push({
        kind: 'text',
        key: `e${ev.seq}`,
        text,
      })
      continue
    }

    if (ev.kind === 'tool_use_start') {
      const toolId = asStr(p.tool_id) || null
      const seg: TurnSegment = {
        kind: 'tool',
        key: `e${ev.seq}`,
        name: asStr(p.name) || 'tool',
        args: p.args,
        toolId,
      }
      openAssistant.segments.push(seg)
      if (toolId != null) {
        toolByIterAndId.set(`${openAssistant.iterSeq}:${toolId}`, {
          iter: openAssistant,
          segIdx: openAssistant.segments.length - 1,
        })
      }
      continue
    }

    if (ev.kind === 'tool_use_end') {
      const toolId = asStr(p.tool_id) || null
      const key = toolId == null ? null : `${openAssistant.iterSeq}:${toolId}`
      const slot = key == null ? undefined : toolByIterAndId.get(key)
      if (slot != null) {
        const seg = slot.iter.segments[slot.segIdx]
        if (seg != null && seg.kind === 'tool') {
          seg.result = p.result
          seg.isError = typeof p.is_error === 'boolean' ? p.is_error : undefined
          const dur = asNum(p.duration_ms)
          if (dur != null) seg.durationMs = dur
        }
      }
      // Unmatched ends (rare ordering / replay edge) are dropped on the
      // chat surface — the timeline still records them.
      continue
    }
    // Every other event kind (signals, pause_requested, run_started,
    // run_ended, harness_session_ended, artifact_edited, …) is
    // protocol-level noise here; the timeline view is the right place
    // for it. The chat surface keeps a tight signal-to-chrome ratio.
  }
  return out
})

// ── Pending-delta merge ──────────────────────────────────────────────

/**
 * Pending `text`-kind delta strings keyed by iter_id. Multiple
 * (iter_id, turn_seq) pairs collapse into one string per iter so a
 * single live cursor is shown per assistant turn. `thinking` deltas
 * are dropped — the chat surface intentionally hides reasoning.
 */
const pendingByIterId = computed<Map<number, string>>(() => {
  const m = new Map<number, string>()
  for (const t of props.pendingTurns ?? []) {
    if (t.kind !== 'text') continue
    const prev = m.get(t.iterId) ?? ''
    m.set(t.iterId, prev + t.text)
  }
  return m
})

function pendingTextFor(turn: AssistantTurn): string {
  if (!turn.open || turn.iterId == null) return ''
  return pendingByIterId.value.get(turn.iterId) ?? ''
}

// ── Auto-scroll ──────────────────────────────────────────────────────

const PIN_TOLERANCE_PX = 50
const scrollEl = ref<HTMLElement | null>(null)
const isPinned = ref(true)

function distanceFromBottom(el: HTMLElement): number {
  return el.scrollHeight - (el.scrollTop + el.clientHeight)
}

function onScroll(): void {
  const el = scrollEl.value
  if (el == null) return
  isPinned.value = distanceFromBottom(el) <= PIN_TOLERANCE_PX
}

function scrollToBottom(): void {
  const el = scrollEl.value
  if (el == null) return
  el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
}

function jumpToLatest(): void {
  isPinned.value = true
  scrollToBottom()
}

/**
 * On any structural change (a new turn, a new segment in the open
 * assistant turn, a delta appended) advance the scroll if the user is
 * still pinned. Watching a coarse signal (turn count + open turn's
 * segment count + pending length) is enough — finer granularity would
 * trigger per-keystroke for live deltas, which is exactly what we
 * want for the "follow the tail" UX.
 */
const tailSignal = computed(() => {
  const last = turns.value[turns.value.length - 1]
  const openSegs =
    last != null && last.kind === 'assistant' && last.open ? last.segments.length : 0
  let pendingLen = 0
  for (const v of pendingByIterId.value.values()) pendingLen += v.length
  return `${turns.value.length}:${openSegs}:${pendingLen}`
})

watch(tailSignal, async () => {
  await nextTick()
  if (!isPinned.value) return
  scrollToBottom()
})

onMounted(() => {
  // Initial position — for a replayed historical chat, this snaps the
  // scrollbar to the bottom so the latest exchange is in view.
  void nextTick(scrollToBottom)
})

const showJumpAffordance = computed(() => !isPinned.value)

const isLive = computed(() => props.status === 'running')

const isEmpty = computed(() => turns.value.length === 0)

const emptyMessage = computed<string>(() => {
  if (props.status === 'closed') return 'This chat ended with no messages.'
  if (props.status === 'cancelled' || props.status === 'failed') {
    return 'Chat ended before any messages were exchanged.'
  }
  return 'Start the conversation — what should we work on?'
})
</script>

<template>
  <div
    ref="scrollEl"
    class="chat-transcript"
    data-testid="chat-transcript"
    :data-turn-count="turns.length"
    @scroll.passive="onScroll"
  >
    <div
      v-if="isEmpty"
      class="chat-transcript__empty"
      data-testid="chat-transcript-empty"
    >
      <p>{{ emptyMessage }}</p>
    </div>

    <ol
      v-else
      class="chat-transcript__list"
    >
      <li
        v-for="turn in turns"
        :key="turn.key"
        class="chat-transcript__turn"
        :class="`chat-transcript__turn--${turn.kind}`"
        :data-turn-kind="turn.kind"
      >
        <!-- User turn: a right-aligned accent bubble. Markdown is
             still parsed so a paste of a fenced code block reads as
             code; markdown-it html:false keeps it XSS-safe (ADR / W6
             render contract). -->
        <div
          v-if="turn.kind === 'user'"
          class="chat-bubble chat-bubble--user"
        >
          <MarkdownRender :source="turn.text" />
        </div>

        <!-- Assistant turn: left-aligned, full-width, interleaved
             text + tool chips. An open turn with no segments yet
             shows a "thinking…" indicator while pi spools its first
             tokens (the indicator is replaced by the live delta as
             soon as the first token lands). -->
        <div
          v-else
          class="chat-assistant"
        >
          <div class="chat-assistant__meta">
            <span class="chat-assistant__role">relay</span>
            <span class="chat-assistant__iter">turn {{ turn.iterSeq }}</span>
          </div>

          <div
            v-if="turn.segments.length === 0 && pendingTextFor(turn) === '' && turn.open"
            class="chat-assistant__thinking"
            data-testid="chat-assistant-thinking"
            aria-live="polite"
          >
            <span class="chat-assistant__dot" />
            <span class="chat-assistant__dot" />
            <span class="chat-assistant__dot" />
          </div>

          <div
            v-for="seg in turn.segments"
            :key="seg.key"
            class="chat-assistant__segment"
          >
            <MarkdownRender
              v-if="seg.kind === 'text'"
              :source="seg.text"
            />
            <div
              v-else
              class="chat-assistant__tool"
              :data-tool-id="seg.toolId ?? ''"
            >
              <div class="chat-assistant__tool-head">
                <span class="chat-assistant__tool-name">{{ seg.name }}</span>
                <span
                  v-if="seg.isError"
                  class="chat-assistant__tool-badge"
                >error</span>
                <span
                  v-if="seg.durationMs != null"
                  class="chat-assistant__tool-meta"
                >{{ seg.durationMs }}ms</span>
              </div>
              <ToolCallCard
                :name="seg.name"
                :args="seg.args"
                :result="seg.result"
                :is-error="seg.isError"
                :duration-ms="seg.durationMs"
                embedded
              />
            </div>
          </div>

          <!-- ADR-46 streaming deltas: a partial assistant response
               renders as the agent types. Replaced by the canonical
               `assistant_text` event the moment turn_end fires —
               same machinery TimelinePane uses. -->
          <div
            v-if="turn.open && pendingTextFor(turn) !== ''"
            class="chat-assistant__segment chat-assistant__segment--pending"
            data-testid="chat-assistant-pending"
          >
            <MarkdownRender :source="pendingTextFor(turn)" />
            <span
              class="chat-assistant__cursor"
              aria-hidden="true"
            />
          </div>
        </div>
      </li>
    </ol>

    <button
      v-if="showJumpAffordance && isLive"
      type="button"
      class="chat-transcript__jump"
      data-testid="chat-transcript-jump"
      @click="jumpToLatest"
    >
      ↓ Jump to latest
    </button>
  </div>
</template>

<style scoped>
.chat-transcript {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 1.25rem 1rem 1.5rem;
  scroll-behavior: smooth;
}

.chat-transcript__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
  max-width: 880px;
  margin-inline: auto;
}

.chat-transcript__turn {
  display: flex;
  flex-direction: column;
}

.chat-transcript__turn--user {
  align-items: flex-end;
}

.chat-transcript__turn--assistant {
  align-items: stretch;
}

.chat-transcript__empty {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  color: var(--color-text-dim);
  font-size: 0.95rem;
  text-align: center;
}

/* User bubble: distinct chat-bubble shape with the accent tint pulled
   way back (5% mix) so the bubble reads as a "you said this" affordance
   without competing with the assistant message body for visual weight. */
.chat-bubble--user {
  max-width: min(70%, 640px);
  padding: 0.6rem 0.85rem;
  border-radius: 14px 14px 4px 14px;
  background: color-mix(in oklab, var(--color-accent) 16%, var(--color-surface));
  color: var(--color-text);
  font-size: 0.95rem;
  line-height: 1.45;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  word-wrap: break-word;
  overflow-wrap: anywhere;
}

.chat-bubble--user :deep(p:first-child) {
  margin-top: 0;
}

.chat-bubble--user :deep(p:last-child) {
  margin-bottom: 0;
}

.chat-assistant {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  font-size: 0.96rem;
  line-height: 1.55;
}

.chat-assistant__meta {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-dim);
}

.chat-assistant__role {
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-accent);
}

.chat-assistant__iter {
  font-variant-numeric: tabular-nums;
}

.chat-assistant__segment {
  display: block;
}

.chat-assistant__segment--pending {
  position: relative;
}

.chat-assistant__cursor {
  display: inline-block;
  width: 0.55em;
  height: 1.05em;
  vertical-align: text-bottom;
  margin-left: 0.1em;
  background: var(--color-accent);
  animation: chat-cursor-blink 1.05s steps(2, end) infinite;
  border-radius: 1px;
}

@keyframes chat-cursor-blink {
  from { opacity: 1; }
  to { opacity: 0; }
}

.chat-assistant__thinking {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.35rem 0;
  color: var(--color-text-dim);
}

.chat-assistant__dot {
  width: 0.4rem;
  height: 0.4rem;
  border-radius: 50%;
  background: currentcolor;
  animation: chat-dot-bounce 1.1s ease-in-out infinite;
}

.chat-assistant__dot:nth-child(2) { animation-delay: 0.15s; }
.chat-assistant__dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes chat-dot-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
  40% { transform: translateY(-3px); opacity: 1; }
}

.chat-assistant__tool {
  border-left: 2px solid var(--color-accent);
  padding: 0.4rem 0.75rem;
  background: color-mix(in oklab, var(--color-accent) 4%, var(--color-bg));
  border-radius: 0 8px 8px 0;
  font-family: var(--font-mono);
  font-size: 0.85rem;
}

.chat-assistant__tool-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.3rem;
  font-size: 0.78em;
  color: var(--color-text-dim);
}

.chat-assistant__tool-name {
  font-weight: 600;
  color: var(--color-text);
}

.chat-assistant__tool-badge {
  font-size: 0.72em;
  color: var(--color-danger);
  border: 1px solid currentcolor;
  border-radius: 999px;
  padding: 0 0.5em;
}

.chat-assistant__tool-meta {
  margin-left: auto;
}

.chat-transcript__jump {
  position: sticky;
  bottom: 0.5rem;
  margin: 0 auto;
  display: block;
  padding: 0.4rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-size: 0.82rem;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

.chat-transcript__jump:hover,
.chat-transcript__jump:focus-visible {
  outline: none;
  border-color: var(--color-accent);
}
</style>
