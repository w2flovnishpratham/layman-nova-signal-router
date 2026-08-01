import { backendHttpUrl } from '../lib/backend'

export type RiskMode = 'paper' | 'live'
export type RiskPresetKey = 'CONSERVATIVE' | 'BALANCED' | 'AGGRESSIVE'
export type RiskChangeSource = 'SETUP' | 'RISK_PAGE' | 'TRADING_TERMINAL'
export type RiskExitMode = 'FLIPS_ONLY' | 'TARGET_PROFIT' | 'CUSTOM_SL_TP'

export interface RiskValues {
  daily_loss_cap: number
  max_loss_per_trade: number
  max_trades_per_day: number
  max_open_positions: number
  lots_per_trade_min: number
  lots_per_trade_max: number
  cooldown_minutes: number
  exit_mode: RiskExitMode
  stop_loss_value: number | null
  take_profit_value: number | null
  stop_loss_basis: 'POINTS' | 'PERCENT'
  take_profit_basis: 'POINTS' | 'PERCENT'
  margin_exposure_cap: number | null
}

export interface RiskPreset {
  key: RiskPresetKey
  name: string
  description: string
  mode: 'PAPER' | 'LIVE'
  values: RiskValues
}

export interface RiskConfiguration {
  mode: 'PAPER' | 'LIVE'
  activeVersion: number
  activeVersionId: string | null
  profileType: RiskPresetKey | 'CUSTOM'
  basedOnPreset: RiskPresetKey
  values: RiskValues
  updatedAt: string | null
  changeSource: RiskChangeSource | 'ADMIN' | 'SYSTEM_MIGRATION' | null
  suggestedDefault: boolean
}

export interface RiskUtilisation {
  used: number
  limit: number
  unlimited: boolean
  pct: number | null
}

export interface RiskControls {
  kill_switch: boolean
  strategy_kill_switch?: boolean
  max_lots_per_order: number
  max_notional_per_trade_paise: number
  max_orders_per_day: number
  max_loss_per_day_paise: number
}

export interface RiskStrategyRow {
  strategy_name: string
  kill_switch: boolean
  effective: RiskControls
  usage: {
    orders_count: number
    notional_used_paise: number
    realized_pnl_paise: number
    loss_used_paise: number
  }
  utilisation: {
    orders: RiskUtilisation
    notional: RiskUtilisation
    loss: RiskUtilisation
  }
}

export interface BreakerEvent {
  timestamp: string
  event: string
  mode: 'PAPER' | 'LIVE'
  engine: string | null
  trigger_type: string
  outcome: string
}

export interface RiskOverview {
  ok: boolean
  available: boolean
  trade_date_ist: string
  scope: string
  user: RiskControls
  strategies: RiskStrategyRow[]
  mode?: RiskMode
  configuration?: RiskConfiguration
  today_usage?: {
    daily_loss: RiskUtilisation
    trades: RiskUtilisation
    open_positions: RiskUtilisation
    margin_exposure: RiskUtilisation
  }
  breaker_history?: BreakerEvent[]
}

export interface RiskPageOverview extends RiskOverview {
  mode: RiskMode
  configuration: RiskConfiguration
  today_usage: {
    daily_loss: RiskUtilisation
    trades: RiskUtilisation
    open_positions: RiskUtilisation
    margin_exposure: RiskUtilisation
  }
  breaker_history: BreakerEvent[]
}

async function riskFetch<T>(path: `/${string}`, init?: RequestInit): Promise<T> {
  const response = await fetch(backendHttpUrl(path), {
    credentials: 'include',
    cache: 'no-store',
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  const body = await response.json().catch(() => null) as (T & { error?: string }) | null
  if (!response.ok || !body) throw new Error(body?.error || `Risk request failed: ${response.status}`)
  return body
}

export async function getRiskPageData(mode: RiskMode): Promise<{
  presets: RiskPreset[]
  configuration: RiskConfiguration
  overview: RiskPageOverview
}> {
  const query = new URLSearchParams({ mode }).toString()
  const [presetResponse, configuration, overview] = await Promise.all([
    riskFetch<{ presets: RiskPreset[] }>(`/api/risk/presets?${query}`),
    riskFetch<RiskConfiguration>(`/api/risk/configuration?${query}`),
    riskFetch<RiskPageOverview>(`/api/risk/overview?${query}`),
  ])
  return { presets: presetResponse.presets, configuration, overview }
}

export function getRiskConfiguration(mode: RiskMode): Promise<RiskConfiguration> {
  return riskFetch(`/api/risk/configuration?${new URLSearchParams({ mode })}`)
}

export function getRiskOverview(mode: RiskMode = 'paper'): Promise<RiskOverview> {
  return riskFetch(`/api/risk/overview?${new URLSearchParams({ mode })}`)
}

export function saveRiskConfiguration(args: {
  mode: RiskMode
  basedOnPreset: RiskPresetKey
  values: RiskValues
  changeSource: RiskChangeSource
  expectedVersion: number
}): Promise<RiskConfiguration> {
  return riskFetch<RiskConfiguration>('/api/risk/configuration', {
    method: 'PUT',
    body: JSON.stringify(args),
  })
}

export function triggerRiskKillSwitch(mode: RiskMode): Promise<{ outcome: string }> {
  return riskFetch('/api/risk/kill-switch', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
}

export function rupees(value: number): string {
  return `₹${Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}

export function paise(value: number): string {
  return rupees(value / 100)
}
