import { describe, it, expect } from 'vitest'
import {
  classifyEvent,
  classifyPending,
  KIND_CATEGORIES,
  KIND_LABEL,
  KIND_MEMBERS,
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

  it('routes signal_emit to signal (signals chip is now sentinel-only)', () => {
    expect(classifyEvent(ev('signal_emit'))).toBe('signal')
  })

  it('routes iter/run/harness lifecycle kinds to boundary', () => {
    for (const k of [
      'iter_started',
      'iter_ended',
      'run_started',
      'run_ended',
      'harness_session_ended',
    ]) {
      expect(classifyEvent(ev(k))).toBe('boundary')
    }
  })

  it('routes pause + fanout/child coordination kinds to pause', () => {
    for (const k of [
      'pause_requested',
      'pause_resolved',
      'subagent_dispatch',
      'subagent_return',
      'child_runs_resolved',
    ]) {
      expect(classifyEvent(ev(k))).toBe('pause')
    }
  })

  it('routes artifact_edited to artifact (extracted from the old `other`)', () => {
    expect(classifyEvent(ev('artifact_edited'))).toBe('artifact')
  })

  it('falls back to other only for truly unknown / future kinds', () => {
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

describe('category metadata', () => {
  it('every chip category has a label and a non-empty member list', () => {
    for (const c of KIND_CATEGORIES) {
      expect(typeof KIND_LABEL[c]).toBe('string')
      expect(KIND_LABEL[c].length).toBeGreaterThan(0)
      expect(KIND_MEMBERS[c].length).toBeGreaterThan(0)
    }
  })

  it('exposes 8 chip categories in canonical display order', () => {
    expect([...KIND_CATEGORIES]).toEqual([
      'assistant',
      'thinking',
      'tool',
      'signal',
      'boundary',
      'pause',
      'artifact',
      'other',
    ])
  })
})
