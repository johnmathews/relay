import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import type { PendingTurn, StreamEvent } from '../src/stores/events'

// Stub the markdown renderer so we don't pay shiki/mermaid cost in
// tests (covered by render.spec.ts). The stub exposes the raw `source`
// in the rendered text so assertions still read message bodies.
vi.mock('@/components/files/MarkdownRender.vue', () => ({
  default: {
    name: 'MarkdownRender',
    props: ['source'],
    template: '<div class="stub-md">{{ source }}</div>',
  },
}))

// Stub ToolCallCard — exercised by its own spec; here we only need to
// know "a tool chip rendered for this name".
vi.mock('@/components/runs/ToolCallCard.vue', () => ({
  default: {
    name: 'ToolCallCard',
    props: ['name', 'args', 'result', 'isError', 'durationMs', 'embedded'],
    template:
      '<div class="stub-tool" :data-tool-name="name">{{ name }}</div>',
  },
}))

import ChatTranscript from '../src/components/chat/ChatTranscript.vue'

function ev(
  seq: number,
  kind: string,
  payload: Record<string, unknown>,
): StreamEvent {
  return { seq, kind, payload }
}

function mountTranscript(
  events: StreamEvent[],
  opts: {
    pendingTurns?: PendingTurn[]
    status?: string
  } = {},
): ReturnType<typeof mount> {
  return mount(ChatTranscript, {
    props: {
      events,
      pendingTurns: opts.pendingTurns ?? [],
      status: opts.status ?? 'paused',
    },
    global: { plugins: [createPinia(), PiniaColada] },
    attachTo: document.body,
  })
}

describe('ChatTranscript — turn folding', () => {
  it('renders an empty state when there are no events', () => {
    const w = mountTranscript([])
    expect(w.find('[data-testid="chat-transcript-empty"]').exists()).toBe(true)
    expect(w.text()).toContain('Start the conversation')
  })

  it('renders a pause_resolved as a user turn carrying its answer text', () => {
    const w = mountTranscript([
      ev(1, 'pause_resolved', { answer: 'hello relay' }),
    ])
    const turns = w.findAll('[data-turn-kind]')
    expect(turns).toHaveLength(1)
    expect(turns[0]!.attributes('data-turn-kind')).toBe('user')
    expect(turns[0]!.text()).toContain('hello relay')
  })

  it('skips a pause_resolved with an empty answer (the initial pause)', () => {
    const w = mountTranscript([
      ev(1, 'pause_resolved', { answer: '' }),
      ev(2, 'pause_resolved', { answer: 'real message' }),
    ])
    const turns = w.findAll('[data-turn-kind]')
    expect(turns).toHaveLength(1)
    expect(turns[0]!.text()).toContain('real message')
  })

  it('folds iter_started → assistant_text → iter_ended into one assistant turn', () => {
    const w = mountTranscript([
      ev(1, 'pause_resolved', { answer: 'hi' }),
      ev(2, 'iter_started', { seq: 1, iter_id: 11 }),
      ev(3, 'assistant_text', { kind: 'text', text: 'first reply' }),
      ev(4, 'iter_ended', { seq: 1, iter_id: 11 }),
    ])
    const turns = w.findAll('[data-turn-kind]')
    expect(turns).toHaveLength(2)
    expect(turns[0]!.attributes('data-turn-kind')).toBe('user')
    expect(turns[1]!.attributes('data-turn-kind')).toBe('assistant')
    expect(turns[1]!.text()).toContain('first reply')
  })

  it('drops thinking-kind assistant_text from the chat surface', () => {
    const w = mountTranscript([
      ev(1, 'iter_started', { seq: 1, iter_id: 11 }),
      ev(2, 'assistant_text', { kind: 'thinking', text: 'reasoning...' }),
      ev(3, 'assistant_text', { kind: 'text', text: 'the answer' }),
      ev(4, 'iter_ended', { seq: 1, iter_id: 11 }),
    ])
    expect(w.text()).not.toContain('reasoning')
    expect(w.text()).toContain('the answer')
  })

  it('interleaves tool calls inline between text segments, collapsed by default', async () => {
    const w = mountTranscript([
      ev(1, 'iter_started', { seq: 1, iter_id: 11 }),
      ev(2, 'assistant_text', { kind: 'text', text: 'before tool' }),
      ev(3, 'tool_use_start', {
        tool_id: 't1',
        name: 'Bash',
        args: { command: 'ls -la' },
      }),
      ev(4, 'tool_use_end', {
        tool_id: 't1',
        result: 'README.md',
        is_error: false,
        duration_ms: 42,
      }),
      ev(5, 'assistant_text', { kind: 'text', text: 'after tool' }),
      ev(6, 'iter_ended', { seq: 1, iter_id: 11 }),
    ])
    // Completed historical tool — collapsed default. Header shows the
    // name + one-line preview, but the embedded ToolCallCard body is
    // not mounted until the operator clicks.
    expect(w.find('.stub-tool').exists()).toBe(false)
    const header = w.find('[data-testid="chat-tool-toggle"]')
    expect(header.exists()).toBe(true)
    expect(header.text()).toContain('Bash')
    expect(header.text()).toContain('$ ls -la')
    expect(header.attributes('aria-expanded')).toBe('false')

    await header.trigger('click')
    const tool = w.find('.stub-tool')
    expect(tool.exists()).toBe(true)
    expect(tool.attributes('data-tool-name')).toBe('Bash')
    expect(w.text()).toContain('before tool')
    expect(w.text()).toContain('after tool')
  })

  it('auto-expands an in-flight tool (no tool_use_end yet) in an open turn', () => {
    const w = mountTranscript(
      [
        ev(1, 'iter_started', { seq: 1, iter_id: 11 }),
        ev(2, 'tool_use_start', {
          tool_id: 't1',
          name: 'Bash',
          args: { command: 'sleep 60' },
        }),
      ],
      { status: 'running' },
    )
    const header = w.find('[data-testid="chat-tool-toggle"]')
    expect(header.attributes('aria-expanded')).toBe('true')
    expect(w.find('.stub-tool').exists()).toBe(true)
    expect(header.text()).toContain('running…')
  })

  it('collapses a prior in-flight tool once a newer tool starts', () => {
    const w = mountTranscript(
      [
        ev(1, 'iter_started', { seq: 1, iter_id: 11 }),
        ev(2, 'tool_use_start', {
          tool_id: 't1',
          name: 'Read',
          args: { path: '/a.md' },
        }),
        ev(3, 'tool_use_end', {
          tool_id: 't1',
          result: 'ok',
          duration_ms: 5,
        }),
        ev(4, 'tool_use_start', {
          tool_id: 't2',
          name: 'Bash',
          args: { command: 'ls' },
        }),
      ],
      { status: 'running' },
    )
    const headers = w.findAll('[data-testid="chat-tool-toggle"]')
    expect(headers).toHaveLength(2)
    // First tool: completed, defaults back to collapsed.
    expect(headers[0]!.attributes('aria-expanded')).toBe('false')
    // Second tool: in flight + latest in the open turn → auto-expanded.
    expect(headers[1]!.attributes('aria-expanded')).toBe('true')
  })

  it('renders the pending stream while an iter is open with no canonical text yet', () => {
    const w = mountTranscript(
      [ev(1, 'iter_started', { seq: 1, iter_id: 11 })],
      {
        pendingTurns: [
          { iterId: 11, turnSeq: 0, kind: 'text', text: 'partial…' },
        ],
        status: 'running',
      },
    )
    expect(
      w.find('[data-testid="chat-assistant-pending"]').exists(),
    ).toBe(true)
    expect(w.text()).toContain('partial…')
  })

  it('shows a thinking indicator while an open iter has neither text nor pending', () => {
    const w = mountTranscript(
      [ev(1, 'iter_started', { seq: 1, iter_id: 11 })],
      { status: 'running' },
    )
    expect(
      w.find('[data-testid="chat-assistant-thinking"]').exists(),
    ).toBe(true)
  })

  it('a closed chat with no exchanged messages shows the closed-empty copy', () => {
    const w = mountTranscript([], { status: 'closed' })
    const empty = w.get('[data-testid="chat-transcript-empty"]')
    expect(empty.text()).toContain('ended with no messages')
  })

  it('alternates user → assistant → user across multiple turns', () => {
    const w = mountTranscript([
      ev(1, 'pause_resolved', { answer: 'msg one' }),
      ev(2, 'iter_started', { seq: 1, iter_id: 11 }),
      ev(3, 'assistant_text', { kind: 'text', text: 'reply one' }),
      ev(4, 'iter_ended', { seq: 1, iter_id: 11 }),
      ev(5, 'pause_resolved', { answer: 'msg two' }),
      ev(6, 'iter_started', { seq: 2, iter_id: 12 }),
      ev(7, 'assistant_text', { kind: 'text', text: 'reply two' }),
      ev(8, 'iter_ended', { seq: 2, iter_id: 12 }),
    ])
    const turns = w.findAll('[data-turn-kind]')
    expect(turns).toHaveLength(4)
    expect(turns.map((t) => t.attributes('data-turn-kind'))).toEqual([
      'user',
      'assistant',
      'user',
      'assistant',
    ])
    expect(w.text()).toContain('reply two')
  })
})
