import { describe, it, expect } from 'vitest'
import { routes } from '../src/lib/routes'

describe('router config', () => {
  it('defines the routes by path and name (Phase 4 + chat mode W4)', () => {
    const byName = new Map(routes.map((r) => [r.name, r]))
    const byPath = new Map(routes.map((r) => [r.path, r]))

    // 4 Phase-4 routes + 1 chat-mode route (W4 — chat-mode plan).
    expect(routes).toHaveLength(5)

    expect(byPath.has('/')).toBe(true)
    expect(byPath.has('/projects/:id')).toBe(true)
    expect(byPath.has('/projects/:id/new-run')).toBe(true)
    expect(byPath.has('/runs/:id')).toBe(true)
    expect(byPath.has('/chats/:id')).toBe(true)

    expect(byName.get('hub')?.path).toBe('/')
    expect(byName.get('project')?.path).toBe('/projects/:id')
    expect(byName.get('new-run')?.path).toBe('/projects/:id/new-run')
    expect(byName.get('run-detail')?.path).toBe('/runs/:id')
    expect(byName.get('chat-detail')?.path).toBe('/chats/:id')
  })

  it('lazy-loads every view (component is an import function)', () => {
    for (const r of routes) {
      expect(typeof r.component).toBe('function')
    }
  })
})
