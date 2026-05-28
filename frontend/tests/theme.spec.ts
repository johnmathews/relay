import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { applyInitialTheme, useTheme } from '../src/lib/theme'

describe('theme controller', () => {
  beforeEach(() => {
    try {
      localStorage.removeItem('relay.theme')
    } catch {
      // ignore
    }
    document.documentElement.removeAttribute('data-theme')
  })

  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
  })

  it('defaults to auto when nothing is persisted', () => {
    applyInitialTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('auto')
    const { choice } = useTheme()
    expect(choice.value).toBe('auto')
  })

  it('reads a persisted choice on init', () => {
    localStorage.setItem('relay.theme', 'light')
    applyInitialTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    const { choice } = useTheme()
    expect(choice.value).toBe('light')
  })

  it('cycle goes auto → light → dark → auto and persists', () => {
    applyInitialTheme()
    const { choice, cycle } = useTheme()
    expect(choice.value).toBe('auto')

    cycle()
    expect(choice.value).toBe('light')
    expect(localStorage.getItem('relay.theme')).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    cycle()
    expect(choice.value).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    cycle()
    expect(choice.value).toBe('auto')
    expect(document.documentElement.getAttribute('data-theme')).toBe('auto')
  })

  it('set writes the choice through to <html> and storage', () => {
    applyInitialTheme()
    const { set, choice } = useTheme()
    set('dark')
    expect(choice.value).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('relay.theme')).toBe('dark')
  })

  it('ignores invalid persisted values and falls back to auto', () => {
    localStorage.setItem('relay.theme', 'sepia')
    applyInitialTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('auto')
  })
})
