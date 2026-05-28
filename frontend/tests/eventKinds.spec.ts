import { describe, it, expect } from 'vitest'
import {
  classifyEvent,
  classifyPending,
  parseKinds,
  serializeKinds,
  KIND_CATEGORIES,
  type KindCategory,
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

describe('parseKinds', () => {
  it('returns null when ?kinds is absent', () => {
    expect(parseKinds({})).toBeNull()
  })

  it('returns null when ?kinds is empty string', () => {
    expect(parseKinds({ kinds: '' })).toBeNull()
  })

  it('parses a proper subset into a Set', () => {
    const got = parseKinds({ kinds: 'tool,signal' })
    expect(got).not.toBeNull()
    expect(got!.has('tool')).toBe(true)
    expect(got!.has('signal')).toBe(true)
    expect(got!.has('assistant')).toBe(false)
    expect(got!.size).toBe(2)
  })

  it('drops unknown tokens silently', () => {
    const got = parseKinds({ kinds: 'tool,xxx,signal,yyy' })
    expect(got).not.toBeNull()
    expect(Array.from(got!).sort()).toEqual(['signal', 'tool'])
  })

  it('returns null when all categories are present (= absent semantics)', () => {
    expect(parseKinds({ kinds: KIND_CATEGORIES.join(',') })).toBeNull()
  })

  it('returns null when no recognised tokens', () => {
    expect(parseKinds({ kinds: 'foo,bar' })).toBeNull()
  })

  it('takes the first value of an array query (router quirk)', () => {
    const got = parseKinds({ kinds: ['tool', 'signal'] })
    expect(Array.from(got!)).toEqual(['tool'])
  })
})

describe('serializeKinds', () => {
  it('returns undefined for null (URL drops the param)', () => {
    expect(serializeKinds(null)).toBeUndefined()
  })

  it('returns undefined for the empty set', () => {
    expect(serializeKinds(new Set())).toBeUndefined()
  })

  it('returns undefined when every category is in the set', () => {
    expect(serializeKinds(new Set(KIND_CATEGORIES))).toBeUndefined()
  })

  it('serialises in canonical order regardless of insertion order', () => {
    const s = new Set<KindCategory>(['signal', 'tool', 'assistant'])
    expect(serializeKinds(s)).toBe('assistant,tool,signal')
  })
})

describe('parseKinds ∘ serializeKinds round-trip', () => {
  const cases: ReadonlyArray<ReadonlyArray<KindCategory>> = [
    ['tool'],
    ['tool', 'signal'],
    ['assistant', 'thinking', 'other'],
    ['assistant', 'thinking', 'tool', 'signal'], // proper subset
  ]
  for (const subset of cases) {
    it(`round-trips ${subset.join(',')}`, () => {
      const s = new Set<KindCategory>(subset)
      const ser = serializeKinds(s)
      expect(ser).not.toBeUndefined()
      const back = parseKinds({ kinds: ser })
      expect(back).not.toBeNull()
      expect(Array.from(back!).sort()).toEqual([...subset].sort())
    })
  }
})
