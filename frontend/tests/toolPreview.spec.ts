import { describe, it, expect } from 'vitest'
import { toolPreview, truncatePreview } from '../src/lib/toolPreview'

describe('toolPreview', () => {
  it('formats bash commands with $ prefix and collapsed whitespace', () => {
    expect(toolPreview('Bash', { command: 'ls   -la\n  /tmp' })).toBe(
      '$ ls -la /tmp',
    )
  })

  it('formats read with ← path', () => {
    expect(toolPreview('Read', { file_path: '/a/b.md' })).toBe('← /a/b.md')
    expect(toolPreview('read', { path: '/a/b.md' })).toBe('← /a/b.md')
  })

  it('formats write/edit with → path', () => {
    expect(toolPreview('Write', { file_path: '/x.py' })).toBe('→ /x.py')
    expect(toolPreview('Edit', { path: '/x.py' })).toBe('→ /x.py')
  })

  it('formats grep/glob with their pattern glyph', () => {
    expect(toolPreview('Grep', { pattern: 'TODO' })).toBe('? TODO')
    expect(toolPreview('Glob', { pattern: '*.ts' })).toBe('* *.ts')
  })

  it('formats task/agent with description or prompt', () => {
    expect(toolPreview('Task', { description: 'review PR' })).toBe('review PR')
    expect(toolPreview('Agent', { prompt: 'find the bug' })).toBe(
      'find the bug',
    )
  })

  it('falls back to first arg key for unknown tools', () => {
    expect(toolPreview('Unknown', { url: 'https://x' })).toBe(
      'url: https://x',
    )
  })

  it('returns empty string when args is absent or has no useful key', () => {
    expect(toolPreview('Read', null)).toBe('')
    expect(toolPreview('Read', {})).toBe('')
    expect(toolPreview('Bash', { command: '' })).toBe('')
  })

  it('truncates long previews with an ellipsis', () => {
    const long = 'x'.repeat(200)
    const out = toolPreview('Bash', { command: long })
    expect(out.endsWith('…')).toBe(true)
    expect(out.length).toBeLessThanOrEqual(140)
  })

  it('truncatePreview is a no-op below the cap', () => {
    expect(truncatePreview('short')).toBe('short')
  })
})
