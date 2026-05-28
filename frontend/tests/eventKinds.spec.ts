import { describe, it, expect } from 'vitest'
import {
  classifyEvent,
  classifyPending,
  categoryToRowType,
  KIND_CATEGORIES,
} from '../src/lib/eventKinds'
import type { PendingTurn, StreamEvent } from '../src/stores/events'

function ev(kind: string, payload: Record<string, unknown> = {}): StreamEvent {
  return { seq: 1, kind, payload }
}

describe('classifyEvent', () => {
  it('routes assistant_text (text) to assistant', () => {
    expect(classifyEvent(ev('assistant_text', { kind: 'text' }))).toBe('assistant')
    // payload.kind absent still reads as assistant (text is the default).
    expect(classifyEvent(ev('assistant_text', {}))).toBe('assistant')
  })

  it('routes assistant_text (thinking) to thinking', () => {
    expect(classifyEvent(ev('assistant_text', { kind: 'thinking' }))).toBe('thinking')
  })

  it('routes tool_use_start/end to tool', () => {
    expect(classifyEvent(ev('tool_use_start'))).toBe('tool')
    expect(classifyEvent(ev('tool_use_end'))).toBe('tool')
  })

  it('routes structural / boundary kinds to signal', () => {
    for (const k of [
      'signal_emit',
      'iter_started',
      'iter_ended',
      'run_started',
      'run_ended',
      'subagent_dispatch',
      'subagent_return',
      'child_runs_resolved',
      'harness_session_ended',
      'pause_requested',
      'pause_resolved',
    ]) {
      expect(classifyEvent(ev(k))).toBe('signal')
    }
  })

  it('falls back to other for everything else', () => {
    expect(classifyEvent(ev('artifact_edited'))).toBe('other')
    expect(classifyEvent(ev('a_future_kind_we_have_not_invented'))).toBe('other')
  })
})

describe('classifyPending', () => {
  function pt(kind: 'text' | 'thinking'): PendingTurn {
    return { iterId: 1, turnSeq: 1, kind, text: 'hi' }
  }

  it('maps text pending turns to assistant', () => {
    expect(classifyPending(pt('text'))).toBe('assistant')
  })

  it('maps thinking pending turns to thinking', () => {
    expect(classifyPending(pt('thinking'))).toBe('thinking')
  })
})

describe('categoryToRowType', () => {
  it('maps every chip category 1:1 except other → generic', () => {
    expect(categoryToRowType('assistant')).toBe('assistant')
    expect(categoryToRowType('thinking')).toBe('thinking')
    expect(categoryToRowType('tool')).toBe('tool')
    expect(categoryToRowType('signal')).toBe('signal')
    expect(categoryToRowType('other')).toBe('generic')
  })

  it('covers the full KIND_CATEGORIES vocabulary (no orphans)', () => {
    // If a future chip category is added without a matching prefs row
    // type, this assertion catches the gap.
    for (const c of KIND_CATEGORIES) {
      expect(typeof categoryToRowType(c)).toBe('string')
    }
  })
})
