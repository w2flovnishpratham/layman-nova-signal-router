import { backendHttpUrl } from '../lib/backend'

export interface TerminalLifecycleStage {
  stage: string
  occurred_at: string | null
  status?: string | null
  message?: string
  order_id?: string | null
}

export interface TerminalRow {
  id: string
  occurred_at: string | null
  lifecycle?: TerminalLifecycleStage[]
  [key: string]: unknown
}

export interface TerminalFeed {
  ok: boolean
  items: TerminalRow[]
  next_cursor: string | null
  reconciliation_status: 'CURRENT' | 'CURSOR_RESET'
}

export interface TerminalAlerts extends TerminalFeed {
  active_items: Array<TerminalRow & TerminalAlertFields>
  historical_items: Array<TerminalRow & TerminalAlertFields>
  unacknowledged_count: number
}

interface TerminalAlertFields {
  severity: string
  category: string
  message: string
  active: boolean
  acknowledged: boolean
}

export interface TerminalFeedOptions {
  mode?: 'paper' | 'live' | null
  runId?: string | null
  cursor?: string | null
  limit?: number
}

function queryFor(options: TerminalFeedOptions): string {
  const query = new URLSearchParams()
  if (options.mode) query.set('mode', options.mode)
  if (options.runId) query.set('run_id', options.runId)
  if (options.cursor) query.set('cursor', options.cursor)
  query.set('limit', String(options.limit ?? 100))
  return query.toString()
}

async function getFeed(path: `/${string}`, options: TerminalFeedOptions = {}): Promise<TerminalFeed> {
  const response = await fetch(backendHttpUrl(`${path}?${queryFor(options)}`), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Terminal feed unavailable (${response.status}).`)
  return response.json() as Promise<TerminalFeed>
}

export const getTradingActivity = (options?: TerminalFeedOptions) => (
  getFeed('/api/trading/activity', options)
)
export const getTradingEngineLog = (options?: TerminalFeedOptions) => (
  getFeed('/api/trading/engine-log', options)
)
export const getTradingExecutions = (options?: TerminalFeedOptions) => (
  getFeed('/api/trading/executions', options)
)

export async function getTradingAlerts(
  options: TerminalFeedOptions = {},
): Promise<TerminalAlerts> {
  const response = await fetch(
    backendHttpUrl(`/api/trading/alerts?${queryFor(options)}`),
    { credentials: 'include', cache: 'no-store' },
  )
  if (!response.ok) throw new Error(`Alerts unavailable (${response.status}).`)
  return response.json() as Promise<TerminalAlerts>
}

export async function acknowledgeHistoricalAlerts(eventIds: string[]): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/trading/alerts/acknowledge-visible'), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_ids: eventIds }),
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) {
    throw new Error(String(body.error ?? `Could not acknowledge alerts (${response.status}).`))
  }
}
