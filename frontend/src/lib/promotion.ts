// Promote-to-task transcript builder (W6 — docs/proposals/chat-mode.md).
//
// Pure helper used by ChatView when the operator clicks "Promote to
// task" in ChatHeader. Folds the chat's event stream into the same
// alternating user/assistant shape ChatTranscript renders, then
// stringifies it as a markdown-ish transcript suitable for prefilling
// the New Run wizard's `prompt_body`.
//
// The event-to-turn mapping intentionally mirrors
// `frontend/src/components/chat/ChatTranscript.vue` so what the user
// sees on screen matches what gets sent to the new task. In particular:
//   • `pause_resolved.payload.answer` with non-empty text → ONE user
//     turn. The initial chat pause (no answer yet) contributes nothing.
//   • Between an `iter_started` and the matching `iter_ended`,
//     concatenate every `assistant_text` event with `payload.kind` !==
//     `"thinking"`. Thinking-kind text is the model's reasoning — the
//     chat surface deliberately hides it (matching consumer chat
//     products) and the promotion prompt does the same so a promoted
//     task carries the visible turns only.
//   • Tool calls and other protocol events (signal_emit, tool_use_*,
//     pause_requested, run_started/ended, …) are protocol-level noise
//     here: they're available in the timeline view but absent from the
//     conversational rendering and absent from the prefill.
//
// The output is a self-contained string the user can edit before
// submitting — the wizard's existing flow handles validation and
// preview unchanged.

/** Minimal event shape the helper needs — matches `StreamEvent`. */
export interface PromotionEvent {
  seq: number
  kind: string
  payload: Record<string, unknown>
}

export interface BuildPromotionPromptOptions {
  /** Ordered, deduped events from the chat run (typically the events store). */
  events: ReadonlyArray<PromotionEvent>
  /** Project display name — appears in the Context line. */
  projectName: string
}

interface UserTurn {
  kind: 'user'
  text: string
}

interface AssistantTurn {
  kind: 'assistant'
  /** Concatenated visible text — thinking-kind segments are dropped. */
  text: string
}

type Turn = UserTurn | AssistantTurn

function asStr(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/**
 * Fold the chat's events into alternating user/assistant turns. Mirrors
 * the logic in ChatTranscript.vue's `turns` computed (W4) — keep the
 * two in sync so the promoted prompt reflects what the operator saw.
 */
function foldTurns(events: ReadonlyArray<PromotionEvent>): Turn[] {
  const out: Turn[] = []
  let openAssistant: { buf: string[] } | null = null

  for (const ev of events) {
    const p = ev.payload
    if (ev.kind === 'pause_resolved') {
      const answer = asStr(p.answer)
      if (answer === '') continue
      out.push({ kind: 'user', text: answer })
      continue
    }
    if (ev.kind === 'iter_started') {
      const buf: string[] = []
      openAssistant = { buf }
      out.push({ kind: 'assistant', text: '' })
      continue
    }
    if (ev.kind === 'iter_ended') {
      if (openAssistant != null) {
        const last = out[out.length - 1]
        if (last != null && last.kind === 'assistant') {
          last.text = openAssistant.buf.join('').trim()
        }
      }
      openAssistant = null
      continue
    }
    if (openAssistant == null) continue
    if (ev.kind === 'assistant_text') {
      // Same thinking-kind filter ChatTranscript applies (ADR-18).
      if (p.kind === 'thinking') continue
      const text = asStr(p.text)
      if (text === '') continue
      openAssistant.buf.push(text)
      continue
    }
    // tool_use_*, signal_emit, pause_requested, run_started/_ended,
    // harness_session_ended, artifact_edited — all protocol-level
    // noise here, dropped from the prefill.
  }

  // If the stream cut off mid-iter (live chat), flush whatever the
  // assistant has emitted so far. The operator can still edit before
  // submitting.
  if (openAssistant != null) {
    const last = out[out.length - 1]
    if (last != null && last.kind === 'assistant') {
      last.text = openAssistant.buf.join('').trim()
    }
  }

  // Drop trailing assistant turns with no visible text — pi was still
  // thinking when the chat was promoted, so there's nothing useful to
  // include for that turn.
  while (
    out.length > 0 &&
    out[out.length - 1]!.kind === 'assistant' &&
    out[out.length - 1]!.text === ''
  ) {
    out.pop()
  }

  return out
}

/**
 * Build the prefilled prompt body for promoting a chat to a task run.
 *
 * Returns a multi-line string. The transcript block is bracketed by
 * `--- Conversation ---` / `--- End conversation ---` so a human
 * reader (and the agent that ingests this as a prompt) can clearly see
 * where the chat history ends and the operator's instructions begin.
 */
export function buildPromotionPrompt(
  opts: BuildPromotionPromptOptions,
): string {
  const turns = foldTurns(opts.events)
  const header = `Context: this task originated from a chat conversation in project ${opts.projectName}.`
  const lines: string[] = []
  lines.push(header)
  lines.push('')
  lines.push('--- Conversation ---')
  if (turns.length === 0) {
    lines.push('(no messages were exchanged before promotion)')
  } else {
    for (const t of turns) {
      const role = t.kind === 'user' ? 'User' : 'Assistant'
      lines.push(`${role}: ${t.text}`)
    }
  }
  lines.push('--- End conversation ---')
  lines.push('')
  lines.push('Continue with the work the chat was building toward.')
  return lines.join('\n')
}

/**
 * sessionStorage key used to hand a prefilled prompt body from the
 * chat view to the new-run wizard. URL query-string handoff is unsafe
 * for long transcripts (browsers cap URL length at ~2KB-32KB depending
 * on stack), so the wizard reads the body from sessionStorage on mount
 * and removes the entry to keep the prefill one-shot. The URL still
 * carries a `?promoteFrom=<runId>` marker so the wizard knows to look.
 */
export const PROMOTION_STORAGE_PREFIX = 'relay:promotion:'

export function promotionStorageKey(runId: string): string {
  return `${PROMOTION_STORAGE_PREFIX}${runId}`
}
