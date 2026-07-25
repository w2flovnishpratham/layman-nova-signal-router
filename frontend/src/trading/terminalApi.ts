import { backendHttpUrl } from '../lib/backend'

export interface TerminalRow {
  id: string
  occurred_at: string | null
  [key: string]: unknown
}

export interface TerminalAlerts {
  ok: boolean
  items: Array<TerminalRow & {
    severity: string
    category: string
    message: string
    active: boolean
    acknowledged: boolean
  }>
  unacknowledged_count: number
}

async function getFeed(path: `/${string}`): Promise<{ ok: boolean; items: TerminalRow[] }> {
  const response = await fetch(backendHttpUrl(path), { credentials: 'include', cache: 'no-store' })
  if (!response.ok) throw new Error(`Terminal feed unavailable (${response.status}).`)
  return response.json() as Promise<{ ok: boolean; items: TerminalRow[] }>
}

export const getTradingActivity = () => getFeed('/api/trading/activity')
export const getTradingEngineLog = () => getFeed('/api/trading/engine-log')
export const getTradingExecutions = () => getFeed('/api/trading/executions')

export async function getTradingAlerts(): Promise<TerminalAlerts> {
  const response = await fetch(backendHttpUrl('/api/trading/alerts'), { credentials: 'include', cache: 'no-store' })
  if (!response.ok) throw new Error(`Alerts unavailable (${response.status}).`)
  return response.json() as Promise<TerminalAlerts>
}

export async function acknowledgeVisibleAlerts(eventIds: string[]): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/trading/alerts/acknowledge-visible'), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_ids: eventIds }),
  })
  if (!response.ok) throw new Error(`Could not acknowledge alerts (${response.status}).`)
}
