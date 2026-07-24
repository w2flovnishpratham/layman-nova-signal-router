import { describe, expect, it } from 'vitest'
import { APP_ROUTES, DEFAULT_APP_ROUTE, appPath, isImplemented, routeFromPath } from './appRoutes'

describe('authenticated route resolution', () => {
  it('resolves bare /app to the default route', () => {
    expect(routeFromPath('/app')).toBe(DEFAULT_APP_ROUTE)
    expect(routeFromPath('/app/')).toBe(DEFAULT_APP_ROUTE)
    expect(DEFAULT_APP_ROUTE).toBe('trading')
  })

  it('resolves each known nested route', () => {
    for (const route of APP_ROUTES) {
      expect(routeFromPath(`/app/${route}`)).toBe(route)
    }
  })

  it('resolves a nested sub-route (strategies/new)', () => {
    expect(routeFromPath('/app/strategies/new')).toBe('strategies/new')
  })

  it('resolves an unknown /app/* path safely to the default', () => {
    expect(routeFromPath('/app/nope')).toBe(DEFAULT_APP_ROUTE)
    expect(routeFromPath('/app/a/b/c')).toBe(DEFAULT_APP_ROUTE)
  })

  it('builds canonical paths', () => {
    expect(appPath('dashboard')).toBe('/app/dashboard')
    expect(appPath('strategies/new')).toBe('/app/strategies/new')
  })

  it('marks only the genuinely built screens as implemented', () => {
    expect(isImplemented('trading')).toBe(true)
    expect(isImplemented('dashboard')).toBe(true)
    expect(isImplemented('strategies')).toBe(true)
    expect(isImplemented('signals')).toBe(true) // backed by GET /api/signals
    expect(isImplemented('webhooks')).toBe(true) // backed by GET /api/webhooks/overview
    expect(isImplemented('risk')).toBe(true) // backed by GET /api/risk/overview
    expect(isImplemented('credentials')).toBe(true) // backed by GET /api/credentials/overview
    expect(isImplemented('reports')).toBe(true) // backed by GET /api/reports
    expect(isImplemented('strategies/new')).toBe(true) // backed by the personal-Pine workflow
    expect(isImplemented('settings')).toBe(true) // backed by GET/PUT /api/preferences
    // Everything else must render a truthful placeholder, not a fake screen.
    expect(isImplemented('automations')).toBe(false)
  })
})
