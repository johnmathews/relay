import { describe, it, expect } from 'vitest'
import {
  parseView,
  serializeView,
  smartDefault,
} from '../src/lib/runView'

describe('parseView', () => {
  it('returns null when ?view is absent', () => {
    expect(parseView({})).toBeNull()
  })

  it('parses view=overview', () => {
    expect(parseView({ view: 'overview' })).toEqual({ kind: 'overview' })
  })

  it('parses view=iter:N as { kind: iter, seq }', () => {
    expect(parseView({ view: 'iter:2' })).toEqual({ kind: 'iter', seq: 2 })
  })

  it('parses view=artifact:<path> and URL-decodes nested paths', () => {
    expect(parseView({ view: 'artifact:improvement-plan.md' })).toEqual({
      kind: 'artifact',
      path: 'improvement-plan.md',
    })
    expect(parseView({ view: 'artifact:discussions%2Ffoo.md' })).toEqual({
      kind: 'artifact',
      path: 'discussions/foo.md',
    })
  })

  it('falls back to null on malformed inputs (silent)', () => {
    expect(parseView({ view: 'iter:abc' })).toBeNull()
    expect(parseView({ view: 'iter:' })).toBeNull()
    expect(parseView({ view: 'foo' })).toBeNull()
    expect(parseView({ view: 'artifact:' })).toBeNull()
  })

  it('accepts an array query value (router quirk) by taking the first', () => {
    expect(parseView({ view: ['iter:1', 'iter:2'] })).toEqual({
      kind: 'iter',
      seq: 1,
    })
  })
})

describe('serializeView', () => {
  it('serialises overview', () => {
    expect(serializeView({ kind: 'overview' })).toBe('overview')
  })

  it('serialises iter', () => {
    expect(serializeView({ kind: 'iter', seq: 3 })).toBe('iter:3')
  })

  it('serialises artifact and URL-encodes nested paths', () => {
    expect(serializeView({ kind: 'artifact', path: 'plan.md' })).toBe(
      'artifact:plan.md',
    )
    expect(
      serializeView({ kind: 'artifact', path: 'discussions/foo.md' }),
    ).toBe('artifact:discussions%2Ffoo.md')
  })
})

describe('smartDefault', () => {
  const baseIters = [{ seq: 1, phase: 'planning' }, { seq: 2, phase: 'planning' }]

  it('returns latest iter for running with iters', () => {
    expect(smartDefault({ status: 'running', iters: baseIters })).toEqual({
      kind: 'iter',
      seq: 2,
    })
  })

  it('returns latest iter for awaiting_children with iters', () => {
    expect(
      smartDefault({ status: 'awaiting_children', iters: baseIters }),
    ).toEqual({ kind: 'iter', seq: 2 })
  })

  it('returns overview for running with no iters yet', () => {
    expect(smartDefault({ status: 'running', iters: [] })).toEqual({
      kind: 'overview',
    })
  })

  it('returns overview for terminal statuses', () => {
    for (const s of ['done', 'failed', 'cancelled']) {
      expect(smartDefault({ status: s, iters: baseIters })).toEqual({
        kind: 'overview',
      })
    }
  })

  it('returns overview for paused (Phase 1 — Phase 4 changes this)', () => {
    expect(smartDefault({ status: 'paused', iters: baseIters })).toEqual({
      kind: 'overview',
    })
  })
})
