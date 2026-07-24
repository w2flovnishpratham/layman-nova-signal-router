import { backendHttpUrl } from '../lib/backend'

// Preferences live in their own table, deliberately separate from the engine's
// runtime settings: changing table density must never touch trading config.
export interface NotificationChannel {
  key: string
  label: string
  available: boolean
  reason: string
}

export interface Preferences {
  timezone: string
  reduced_motion: boolean
  table_density: string
  default_chart_timeframe: string
  notification_preferences: Record<string, boolean>
  revision: number
  stored: boolean
  channels: NotificationChannel[]
}

export const TABLE_DENSITIES = ['comfortable', 'compact'] as const
export const CHART_TIMEFRAMES = ['1m', '3m', '5m', '15m', '1h', '1d'] as const

export async function getPreferences(): Promise<Preferences> {
  const response = await fetch(backendHttpUrl('/api/preferences'), { credentials: 'include', cache: 'no-store' })
  if (!response.ok) throw new Error(`Could not load preferences: ${response.status}`)
  return response.json() as Promise<Preferences>
}

export async function savePreferences(values: Partial<Preferences>): Promise<Preferences> {
  const response = await fetch(backendHttpUrl('/api/preferences'), {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok || body.ok === false) throw new Error(String(body.error ?? `Could not save preferences: ${response.status}`))
  return body as unknown as Preferences
}

/** Resets the Paper session. The server refuses while the engine runs, a
    position is open, or an exit is pending — those guards are not client-side. */
export async function resetPaperSession(): Promise<void> {
  const response = await fetch(backendHttpUrl('/api/runtime/paper/reset'), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as Record<string, unknown>
    throw new Error(String(body.detail ?? `Paper reset failed: ${response.status}`))
  }
}
