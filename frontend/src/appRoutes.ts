import { useEffect, useState } from 'react'
import { currentPath, navigate } from './navigation'

// Nested authenticated routes. The public landing stays at "/"; everything under
// "/app" is the authenticated application. Routes are derived from the URL (never
// from in-memory view state), so refresh and Back/Forward work on every page.

export const APP_ROUTES = [
  'dashboard',
  'trading',
  'setup',
  'strategies',
  'strategies/new',
  'signals',
  'automations',
  'webhooks',
  'credentials',
  'risk',
  'reports',
  'settings',
] as const

export type AppRoute = (typeof APP_ROUTES)[number]

/** The route /app redirects to when no sub-route is given. */
export const DEFAULT_APP_ROUTE: AppRoute = 'trading'

/** Routes backed by real, working screens today. Everything else is a truthful placeholder. */
export const IMPLEMENTED_ROUTES: AppRoute[] = ['trading', 'dashboard', 'strategies', 'signals', 'webhooks', 'risk', 'setup', 'credentials', 'reports']

export function appPath(route: AppRoute): string {
  return `/app/${route}`
}

/** Resolve a pathname to a known route, falling back to the default for /app and
    for any unknown /app/* path (so a bad URL never dead-ends). */
export function routeFromPath(pathname: string): AppRoute {
  if (!pathname.startsWith('/app')) return DEFAULT_APP_ROUTE
  const rest = pathname.slice('/app'.length).replace(/^\/+|\/+$/g, '')
  if (!rest) return DEFAULT_APP_ROUTE
  const match = APP_ROUTES.find((r) => r === rest)
  return match ?? DEFAULT_APP_ROUTE
}

export function isImplemented(route: AppRoute): boolean {
  return IMPLEMENTED_ROUTES.includes(route)
}

/** Current authenticated route, kept in sync with history navigation. */
export function useAppRoute(): AppRoute {
  const [route, setRoute] = useState(() => routeFromPath(currentPath()))
  useEffect(() => {
    const onPop = () => setRoute(routeFromPath(currentPath()))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return route
}

export function goToRoute(route: AppRoute): void {
  navigate(appPath(route))
}

export const ROUTE_META: Record<AppRoute, { label: string; description: string }> = {
  dashboard: { label: 'Dashboard', description: 'Portfolio, equity curve and session performance.' },
  trading: { label: 'Trading', description: 'Live chart, engine controls and the active position.' },
  setup: { label: 'Setup', description: 'Answer the setup questions, then arm the engine.' },
  strategies: { label: 'Strategies', description: 'NOVA, imported Pine and manual strategies.' },
  'strategies/new': { label: 'Add Strategy', description: 'Import a Pine script and validate it before routing.' },
  signals: { label: 'Signals', description: 'Every TradingView alert received and what happened to it.' },
  automations: { label: 'Automations', description: 'Routing rules, guardrails and scheduled jobs.' },
  webhooks: { label: 'Webhooks', description: 'Endpoints TradingView alerts are delivered to.' },
  credentials: { label: 'Credentials', description: 'Broker accounts the router places orders through.' },
  risk: { label: 'Risk', description: 'Server-side limits enforced before any order is routed.' },
  reports: { label: 'Reports', description: 'Daily session reports and monthly summaries.' },
  settings: { label: 'Settings', description: 'Account, notifications and session preferences.' },
}
