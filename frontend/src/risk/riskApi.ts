import { backendHttpUrl } from '../lib/backend'

// Mirrors backend/app/services/risk_overview.py.
// These are the STRATEGY FAN-OUT gates, not the engine's runtime settings.
// Money is in paise. A limit of 0 means "no limit" (unlimited=true, pct=null).
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
  utilisation: { orders: RiskUtilisation; notional: RiskUtilisation; loss: RiskUtilisation }
}

export interface RiskOverview {
  ok: boolean
  available: boolean
  trade_date_ist: string
  scope: string
  user: RiskControls
  strategies: RiskStrategyRow[]
}

export async function getRiskOverview(): Promise<RiskOverview> {
  const response = await fetch(backendHttpUrl('/api/risk/overview'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`Could not load risk limits: ${response.status}`)
  }
  return response.json() as Promise<RiskOverview>
}

/** Paise -> rupees, formatted. Limits of 0 are rendered as "No limit" by callers. */
export function paise(value: number): string {
  return `₹${(value / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}
