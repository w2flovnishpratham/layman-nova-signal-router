import { backendHttpUrl } from '../lib/backend'

export interface Metric {
  value: number | null
  reason: string | null
}

export type ReportMode = 'paper' | 'live'
export type TradeOrigin = 'all' | 'automated' | 'manual'

export interface DailySession {
  date: string
  strategy_mix: string[]
  trades: number
  wins: number
  losses: number
  win_rate: Metric
  max_drawdown: Metric & { basis: string }
  net_pnl: number
  manual_orders: number
  mode: ReportMode
}

export interface StrategyReport {
  display_name: string
  realized_pnl: number
  closed_trades: number
  wins: number
  losses: number
  win_rate: Metric
  contribution_percentage: number
}

export interface Report {
  ok: boolean
  mode: ReportMode
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
    sessions: number
  }
  win_rate: Metric
  average_winner: Metric
  average_loser: Metric
  profit_factor: Metric
  max_drawdown: Metric & { basis: string }
  daily_sessions: DailySession[]
  by_strategy: StrategyReport[]
}

function reportQuery(start: string, end: string, mode: ReportMode, tradeOrigin: TradeOrigin): string {
  return new URLSearchParams({ start, end, mode, trade_origin: tradeOrigin }).toString()
}

export async function getReport(
  start: string,
  end: string,
  mode: ReportMode = 'paper',
  tradeOrigin: TradeOrigin = 'all',
): Promise<Report> {
  const response = await fetch(backendHttpUrl(`/api/reports?${reportQuery(start, end, mode, tradeOrigin)}`), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not load the report: ${response.status}`)
  return response.json() as Promise<Report>
}

export function reportExportUrl(
  type: 'csv' | 'pdf',
  start: string,
  end: string,
  mode: ReportMode,
  tradeOrigin: TradeOrigin,
): string {
  return backendHttpUrl(`/api/reports/export.${type}?${reportQuery(start, end, mode, tradeOrigin)}`)
}

export function reportCsvUrl(
  start: string,
  end: string,
  mode: ReportMode = 'paper',
  tradeOrigin: TradeOrigin = 'all',
): string {
  return reportExportUrl('csv', start, end, mode, tradeOrigin)
}

export function rupees(value: number, forceSign = false): string {
  const sign = value < 0 ? '-' : forceSign && value > 0 ? '+' : ''
  return `${sign}₹${Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}
