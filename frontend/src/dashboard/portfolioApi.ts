import { backendHttpUrl } from '../lib/backend'

export interface PortfolioKpis {
  realized_pnl: number
  realized_pnl_pct: number
  total_trades: number
  wins: number
  losses: number
  breakeven: number
  win_rate: number
  avg_win: number
  avg_loss: number
  avg_trade: number
  profit_factor: number
  best_trade: number
  worst_trade: number
  max_drawdown: number
  max_drawdown_pct: number
  total_charges: number
  avg_hold_minutes: number
  current_streak: { type: 'win' | 'loss' | 'flat'; count: number }
}

export interface PortfolioWallet {
  starting_balance: number | null
  available_balance: number | null
  utilized_amount: number | null
  sod_limit: number | null
  realized_pnl: number | null
  session_pnl: number | null
  equity: number | null
  funds_connected: boolean
}

export interface EquityPoint {
  index: number
  t: string | null
  equity: number
  cumulative_pnl: number
}

export interface DailyPnlPoint {
  date: string
  pnl: number
  trades: number
  wins: number
}

export interface SideStats {
  trades: number
  pnl: number
  win_rate: number
}

export interface SymbolStats {
  symbol: string
  trades: number
  pnl: number
}

export interface PortfolioTrade {
  symbol: string | null
  option_side: 'CE' | 'PE' | null
  qty: number
  entry_price: number | null
  exit_price: number | null
  entry_charges: number | null
  exit_charges: number | null
  gross_pnl: number | null
  realized_pnl: number | null
  pnl_pct: number | null
  entry_order_id: string | null
  exit_order_id: string | null
  signal_id: string | null
  opened_at: string | null
  closed_at: string | null
  hold_minutes: number | null
  result: 'win' | 'loss' | 'flat'
}

export interface OpenPosition {
  symbol: string | null
  option_side: 'CE' | 'PE' | null
  qty: number
  entry_price: number | null
  opened_at: string | null
  entry_order_id: string | null
}

export interface PortfolioAnalytics {
  mode: 'paper' | 'live' | string
  currency: string
  generated_at: string
  funds_connected: boolean
  wallet: PortfolioWallet
  kpis: PortfolioKpis
  equity_curve: EquityPoint[]
  daily_pnl: DailyPnlPoint[]
  side_breakdown: { CE: SideStats; PE: SideStats }
  symbol_breakdown: SymbolStats[]
  open_position: OpenPosition | null
  trades: PortfolioTrade[]
}

export async function getPortfolioAnalytics(): Promise<PortfolioAnalytics> {
  const response = await fetch(backendHttpUrl('/api/dashboard/portfolio'), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`Could not load portfolio analytics: ${response.status}`)
  }
  return response.json() as Promise<PortfolioAnalytics>
}
