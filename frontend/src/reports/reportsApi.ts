import { backendHttpUrl } from '../lib/backend'

// Mirrors backend/app/services/reports_service.py.
// A metric with a zero denominator is null with a reason — never a fabricated 0.
export interface Metric { value: number | null; reason: string | null }

export interface ReportTrade {
  closed_at: string | null
  strategy: string | null
  symbol: string | null
  option_side: string | null
  qty: number
  entry_price: number | null
  exit_price: number | null
  charges: number
  gross_pnl: number
  realized_pnl: number
}

export type TradeOrigin = 'all' | 'automated' | 'manual'

export interface Report {
  ok: boolean
  mode: string
  trade_origin: TradeOrigin
  period: { start: string; end: string; timezone: string }
  totals: {
    trades: number
    winning_trades: number
    losing_trades: number
    scratch_trades: number
    gross_profit: number
    gross_loss: number
    net_pnl: number
  }
  win_rate: Metric
  average_winner: Metric
  average_loser: Metric
  profit_factor: Metric
  max_drawdown: Metric & { basis: string }
  trades: ReportTrade[]
}

export async function getReport(
  start: string,
  end: string,
  mode = 'paper',
  tradeOrigin?: TradeOrigin,
): Promise<Report> {
  const query = new URLSearchParams({ start, end, mode })
  if (tradeOrigin) query.set('trade_origin', tradeOrigin)
  const response = await fetch(backendHttpUrl(`/api/reports?${query.toString()}`), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not load the report: ${response.status}`)
  return response.json() as Promise<Report>
}

/** The CSV comes from the same server-side aggregate the page displays. */
export function reportCsvUrl(start: string, end: string, mode = 'paper', tradeOrigin?: TradeOrigin): string {
  const query = new URLSearchParams({ start, end, mode })
  if (tradeOrigin) query.set('trade_origin', tradeOrigin)
  return backendHttpUrl(`/api/reports/export.csv?${query.toString()}`)
}

export function rupees(value: number): string {
  const sign = value < 0 ? '-' : ''
  return `${sign}₹${Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}
