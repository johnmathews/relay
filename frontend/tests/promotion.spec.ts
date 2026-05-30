import { describe, it, expect } from 'vitest'
import {
  buildPromotionPrompt,
  promotionStorageKey,
  PROMOTION_STORAGE_PREFIX,
  type PromotionEvent,
} from '../src/lib/promotion'

// Helpers that mirror the on-wire `StreamEvent` shape so tests read
// like real timelines (W6 — docs/proposals/chat-mode.md). The fold
// here intentionally mirrors ChatTranscript.vue; if these tests start
// failing because of a transcript-shape change, the two files must
// move together.
let nextSeq = 1
function ev(kind: string, payload: Record<string, unknown>): PromotionEvent {
  return { seq: nextSeq++, kind, payload }
}
function user(text: string): PromotionEvent {
  return ev('pause_resolved', { answer: text })
}
function iterStart(seq: number, iterId: number): PromotionEvent {
  return ev('iter_started', { seq, iter_id: iterId })
}
function iterEnd(seq: number, iterId: number): PromotionEvent {
  return ev('iter_ended', { seq, iter_id: iterId })
}
function assistantText(text: string, kind = 'text'): PromotionEvent {
  return ev('assistant_text', { text, kind })
}

describe('buildPromotionPrompt', () => {
  it('renders an empty-conversation marker when no turns are present', () => {
    nextSeq = 1
    const out = buildPromotionPrompt({ events: [], projectName: 'Alpha' })
    expect(out).toContain('chat conversation in project Alpha')
    expect(out).toContain('--- Conversation ---')
    expect(out).toContain('(no messages were exchanged before promotion)')
    expect(out).toContain('--- End conversation ---')
    expect(out).toContain('Continue with the work the chat was building toward.')
  })

  it('folds alternating user + assistant turns', () => {
    nextSeq = 1
    const events: PromotionEvent[] = [
      user('first message'),
      iterStart(1, 11),
      assistantText('first '),
      assistantText('reply'),
      iterEnd(1, 11),
      user('second message'),
      iterStart(2, 12),
      assistantText('second reply'),
      iterEnd(2, 12),
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    const expected = [
      'Context: this task originated from a chat conversation in project Alpha.',
      '',
      '--- Conversation ---',
      'User: first message',
      'Assistant: first reply',
      'User: second message',
      'Assistant: second reply',
      '--- End conversation ---',
      '',
      'Continue with the work the chat was building toward.',
    ].join('\n')
    expect(out).toBe(expected)
  })

  it('drops thinking-kind assistant text from the transcript', () => {
    // The chat surface hides thinking; the promoted prompt does too —
    // the agent that picks up this prompt should not be primed by the
    // prior model's chain of thought.
    nextSeq = 1
    const events: PromotionEvent[] = [
      user('think about this'),
      iterStart(1, 11),
      assistantText('hmm, let me consider…', 'thinking'),
      assistantText('Here is the answer.'),
      iterEnd(1, 11),
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('Assistant: Here is the answer.')
    expect(out).not.toContain('hmm, let me consider')
  })

  it('skips a pause_resolved with empty answer (initial chat pause)', () => {
    // The chat run starts in `paused` with a synthetic pause_requested
    // whose first resolution is the user's first real message. An
    // empty answer would be a malformed event; the chat surface skips
    // it and so does the prefill.
    nextSeq = 1
    const events: PromotionEvent[] = [
      ev('pause_resolved', { answer: '' }),
      user('actual first message'),
      iterStart(1, 11),
      assistantText('reply'),
      iterEnd(1, 11),
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('User: actual first message')
    expect(out.match(/User:/g)).toHaveLength(1)
  })

  it('drops protocol-level events (tool calls, signals, run_*)', () => {
    nextSeq = 1
    const events: PromotionEvent[] = [
      ev('run_started', { prompt_body: '' }),
      user('do a thing'),
      iterStart(1, 11),
      ev('tool_use_start', { tool_id: 't1', name: 'Bash', args: { cmd: 'ls' } }),
      ev('tool_use_end', { tool_id: 't1', result: 'a\nb', is_error: false }),
      assistantText('Did it.'),
      ev('signal_emit', { kind: 'unit_done', args: {} }),
      iterEnd(1, 11),
      ev('pause_requested', { question: '' }),
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('User: do a thing')
    expect(out).toContain('Assistant: Did it.')
    expect(out).not.toContain('tool_use')
    expect(out).not.toContain('Bash')
    expect(out).not.toContain('signal_emit')
  })

  it('flushes a live (still-open) assistant turn at the tail', () => {
    // The operator may click Promote while pi is still streaming. We
    // include the partial response so the operator's mental context
    // matches the prefill they're about to submit.
    nextSeq = 1
    const events: PromotionEvent[] = [
      user('quick question'),
      iterStart(1, 11),
      assistantText('working on '),
      assistantText('it…'),
      // No iter_ended yet — chat is mid-turn.
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('Assistant: working on it…')
  })

  it('drops a trailing assistant turn with no visible text yet', () => {
    // iter_started has fired but pi has not flushed any text. There's
    // nothing meaningful to send — leave it out so the prompt doesn't
    // end with a bare "Assistant: ".
    nextSeq = 1
    const events: PromotionEvent[] = [
      user('one'),
      iterStart(1, 11),
      assistantText('replied'),
      iterEnd(1, 11),
      user('two'),
      iterStart(2, 12),
      // pi is still thinking; no assistant_text yet.
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('User: two')
    // The empty assistant turn should be elided.
    expect(out).not.toMatch(/Assistant:\s*$/m)
  })

  it('treats string-coerced payload fields safely', () => {
    // Defensive: a malformed event with non-string `answer` / `text`
    // must not throw. The chat surface uses the same `asStr` helper;
    // we mirror it here.
    nextSeq = 1
    const events: PromotionEvent[] = [
      ev('pause_resolved', { answer: 42 }), // skipped — not a string
      user('valid'),
      iterStart(1, 11),
      ev('assistant_text', { text: null, kind: 'text' }), // skipped
      assistantText('actual reply'),
      iterEnd(1, 11),
    ]
    const out = buildPromotionPrompt({ events, projectName: 'Alpha' })
    expect(out).toContain('User: valid')
    expect(out).toContain('Assistant: actual reply')
  })

  it('escapes the project name verbatim (no markdown processing)', () => {
    // The helper is a string builder, not a renderer. Special chars in
    // a project name pass through unchanged so the operator sees what
    // they typed in their relay project registration.
    nextSeq = 1
    const out = buildPromotionPrompt({
      events: [],
      projectName: 'foo/bar (v2)',
    })
    expect(out).toContain('in project foo/bar (v2).')
  })
})

describe('promotionStorageKey', () => {
  it('namespaces the runId under the relay:promotion: prefix', () => {
    expect(promotionStorageKey('chat-1')).toBe(
      `${PROMOTION_STORAGE_PREFIX}chat-1`,
    )
    expect(PROMOTION_STORAGE_PREFIX).toBe('relay:promotion:')
  })
})
