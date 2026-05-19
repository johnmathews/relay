import { describe, it, expect } from 'vitest'
import { routes } from '../src/lib/routes'

describe('router config', () => {
  it('defines the four Phase 4 routes by path and name', () => {
    const byName = new Map(routes.map((r) => [r.name, r]))
    const byPath = new Map(routes.map((r) => [r.path, r]))

    expect(routes).toHaveLength(4)

    expect(byPath.has('/')).toBe(true)
    expect(byPath.has('/projects/:id')).toBe(true)
    expect(byPath.has('/projects/:id/new-run')).toBe(true)
    expect(byPath.has('/runs/:id')).toBe(true)

    expect(byName.get('hub')?.path).toBe('/')
    expect(byName.get('project')?.path).toBe('/projects/:id')
    expect(byName.get('new-run')?.path).toBe('/projects/:id/new-run')
    expect(byName.get('run-detail')?.path).toBe('/runs/:id')
  })

  it('lazy-loads every view (component is an import function)', () => {
    for (const r of routes) {
      expect(typeof r.component).toBe('function')
    }
  })
})
