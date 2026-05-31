import { useState } from 'react'
import { getDashboardSummary } from '../api/dashboard'
import { usePolling } from './usePolling'
import type { DashboardSummary } from '../types'

export type StatusKind = 'loading' | 'ready' | 'live' | 'blocked' | 'error'

export interface SystemStatus {
  kind: StatusKind
  label: string
  subLabel: string | null
  emergencyStop: boolean
  killSwitch: boolean
  liveOrders: boolean
  dhanMode: string
  balance: number | null
  engineRunning: boolean
  dhanConnected: boolean
  raw: DashboardSummary | null
}

function deriveStatus(data: DashboardSummary | null, fetchError: boolean): SystemStatus {
  const base = {
    emergencyStop: data?.app_state?.emergency_stop ?? false,
    killSwitch: data?.app_state?.global_kill_switch ?? false,
    liveOrders: data?.mode?.live_orders_enabled ?? false,
    dhanMode: data?.mode?.dhan_mode ?? 'UNKNOWN',
    balance: data?.wallet?.available_balance ?? null,
    engineRunning: !!data?.app_state?.engine_started,
    dhanConnected: !!data?.dhan_connected,
    raw: data,
  }

  if (fetchError) {
    return { ...base, kind: 'error', label: 'Backend down', subLabel: 'Cannot reach server' }
  }
  if (!data) {
    return { ...base, kind: 'loading', label: 'Connecting', subLabel: null }
  }
  if (base.emergencyStop) {
    return { ...base, kind: 'blocked', label: 'Emergency stop', subLabel: 'New entries blocked' }
  }
  if (base.killSwitch) {
    return { ...base, kind: 'blocked', label: 'Kill switch', subLabel: 'All trading paused' }
  }
  if (!base.dhanConnected) {
    return { ...base, kind: 'blocked', label: 'Dhan offline', subLabel: 'Check credentials' }
  }
  if (!base.engineRunning) {
    return { ...base, kind: 'blocked', label: 'Engine stopped', subLabel: null }
  }
  if (base.liveOrders) {
    return { ...base, kind: 'live', label: 'LIVE ORDERS', subLabel: 'Real money active' }
  }
  return {
    ...base,
    kind: 'ready',
    label: 'Ready',
    subLabel: base.dhanMode === 'REAL' ? 'Real Dhan' : 'Mock mode',
  }
}

// FE-C2 — Was 5s. Bumped to 10s because this is the global topbar pill,
// rendered on every page; sub-10s freshness for "is the backend alive" is
// overkill. usePolling also pauses while document.hidden (FE-C3).
export function useSystemStatus(intervalMs = 10000): SystemStatus {
  const [data, setData] = useState<DashboardSummary | null>(null)
  const [fetchError, setFetchError] = useState(false)

  const run = () =>
    getDashboardSummary()
      .then(r => { setData(r.data); setFetchError(false) })
      .catch(() => setFetchError(true))

  usePolling(run, intervalMs)

  return deriveStatus(data, fetchError)
}
