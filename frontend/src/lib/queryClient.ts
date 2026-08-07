import { QueryClient } from '@tanstack/react-query'
import type { ServerEvent } from '../types'

// Shared cache for every page's read-only data fetch (Reports, Risk,
// Dashboard, ...). staleTime keeps a page's last fetch showing instantly on
// remount instead of a fresh spinner every time you navigate back to it --
// see docs/ or the "why does every page reload" thread this was built for.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
    },
  },
})

// Which cached query families go stale when a given server-pushed event
// arrives. Keyed by the ServerEvent type dispatched as `nova:terminal-delta`
// (see sessionStore.ts) so a trade closing or a position changing refetches
// the pages that show it instead of waiting out staleTime or polling.
const INVALIDATES_ON: Partial<Record<ServerEvent['type'], string[]>> = {
  'trade.exit': ['report', 'risk', 'dashboard-portfolio'],
  'position.update': ['risk', 'dashboard-portfolio'],
  'order.filled': ['risk', 'dashboard-portfolio', 'dashboard-signals'],
}

export function wireQueryInvalidation(): () => void {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<ServerEvent>).detail
    const keys = detail ? INVALIDATES_ON[detail.type] : undefined
    keys?.forEach((key) => void queryClient.invalidateQueries({ queryKey: [key] }))
  }
  window.addEventListener('nova:terminal-delta', handler)
  return () => window.removeEventListener('nova:terminal-delta', handler)
}
