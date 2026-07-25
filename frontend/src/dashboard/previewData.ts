import type { RuntimeStatus } from '../api'
import type { SystemHealth } from '../types'
import type { DashboardPreviewBundle } from './PortfolioDashboard'

const days = [
  ['2026-07-04', 820],
  ['2026-07-05', -380],
  ['2026-07-07', 1320],
  ['2026-07-09', 460],
  ['2026-07-10', -1040],
  ['2026-07-11', 370],
  ['2026-07-13', 1820],
  ['2026-07-15', 2510],
  ['2026-07-16', -420],
  ['2026-07-17', 850],
  ['2026-07-18', 260],
  ['2026-07-20', -210],
  ['2026-07-22', 980],
  ['2026-07-23', 850],
] as const

let cumulative = 0
const equity = days.flatMap(([date, pnl], dayIndex) => {
  cumulative += pnl
  return [
    {
      index: dayIndex * 2,
      t: `${date}T10:15:00+05:30`,
      equity: 200_000 + cumulative - Math.round(pnl * 0.45),
      cumulative_pnl: cumulative - Math.round(pnl * 0.45),
    },
    {
      index: dayIndex * 2 + 1,
      t: `${date}T14:45:00+05:30`,
      equity: 200_000 + cumulative,
      cumulative_pnl: cumulative,
    },
  ]
})

export const dashboardPreviewBundle: DashboardPreviewBundle = {
  data: {
    mode: 'paper',
    currency: 'INR',
    generated_at: '2026-07-23T14:58:00+05:30',
    funds_connected: true,
    wallet: {
      starting_balance: 200_000,
      available_balance: 202_420,
      utilized_amount: 43_258,
      sod_limit: 200_000,
      realized_pnl: 8_190,
      session_pnl: 3_395,
      equity: 245_678,
      funds_connected: true,
    },
    kpis: {
      realized_pnl: 8_190,
      realized_pnl_pct: 4.095,
      total_trades: 48,
      wins: 32,
      losses: 16,
      breakeven: 0,
      win_rate: 66.7,
      avg_win: 880,
      avg_loss: -510,
      avg_trade: 170.63,
      profit_factor: 1.72,
      best_trade: 2_510,
      worst_trade: -1_040,
      max_drawdown: -4_310,
      max_drawdown_pct: -2.1,
      total_charges: 682.4,
      avg_hold_minutes: 18,
      current_streak: { type: 'win', count: 3 },
    },
    equity_curve: equity,
    daily_pnl: days.map(([date, pnl], index) => ({
      date,
      pnl,
      trades: 2 + (index % 4),
      wins: 1 + (index % 3),
    })),
    side_breakdown: {
      CE: { trades: 29, pnl: 5_480, win_rate: 69 },
      PE: { trades: 19, pnl: 2_710, win_rate: 63 },
    },
    symbol_breakdown: [
      { symbol: 'NIFTY 22950 CE', trades: 16, pnl: 4_230 },
      { symbol: 'NIFTY 23000 PE', trades: 12, pnl: 2_940 },
    ],
    open_position: {
      symbol: 'NIFTY 22950 CE',
      option_side: 'CE',
      qty: 65,
      entry_price: 88.4,
      opened_at: '2026-07-23T14:30:00+05:30',
      entry_order_id: 'PAPER-01982',
    },
    trades: [],
  },
  risk: {
    ok: true,
    available: true,
    trade_date_ist: '2026-07-23',
    scope: 'owner',
    user: {
      kill_switch: false,
      max_lots_per_order: 5,
      max_notional_per_trade_paise: 1_000_000,
      max_orders_per_day: 150,
      max_loss_per_day_paise: 2_500_000,
    },
    strategies: [
      strategyRisk('NOVA Supertrend', 84, 4_123_000, 6.2),
      strategyRisk('NOVA ORB', 36, 1_294_000, 8.9),
      strategyRisk('NOVA VWAP Bands', 22, -318_000, 11.4),
      strategyRisk('ardine-trend-breakout', 14, 586_000, 4.1),
    ],
  },
  signals: {
    ok: true,
    available: true,
    items: [],
    next_cursor: null,
    counts_window_hours: 24,
    counts: { accepted: 146, rejected: 2 },
  },
  webhooks: {
    ok: true,
    available: true,
    endpoints: [
      { key: 'tradingview', label: 'TradingView', url: '/webhook', method: 'POST', description: 'Signal receiver' },
      { key: 'verified', label: 'Verified', url: '/webhook/verified', method: 'POST', description: 'Signed receiver' },
      { key: 'strategy', label: 'Strategy', url: '/webhook/strategy', method: 'POST', description: 'Strategy receiver' },
    ],
    secret: { set: true, masked: '••••7d31', source: 'account' },
    window_hours: 24,
    deliveries: { counts: { accepted: 146, rejected: 2 }, signature_verified: 148, last_delivery_at: '2026-07-23T14:51:00+05:30' },
    recent: [],
  },
}

export const dashboardPreviewHealth: SystemHealth = {
  dhan: 'connected',
  staticIp: 'verified',
  market: 'open',
  feed: 'live',
  feedReason: null,
  engine: 'listening',
  pubsub: 'healthy',
  lastSignalAt: '2026-07-23T14:51:00+05:30',
  walletOk: true,
}

export const dashboardPreviewRuntime = {
  ok: true,
  owner_user_id: 'preview-owner',
  engine: {
    state: 'RUNNING',
    running: true,
    accepting_signals: true,
    mode: 'paper',
    display: 'RUNNING',
    last_transition_at: '2026-07-23T09:15:00+05:30',
  },
  exit: { state: 'NONE', operation_id: null, requested_at: null },
  position: {
    has_open_position: true,
    security_id: '50241',
    trading_symbol: 'NIFTY 22950 CE',
    option_side: 'CE',
    strike: 22_950,
    expiry: '2026-07-30',
    entry_order_id: 'PAPER-01982',
    qty: 65,
    lots: 1,
    entry_price: 88.4,
    opened_at: '2026-07-23T14:30:00+05:30',
    unrealized_pnl: 660,
    ltp: {
      value: 98.55,
      source: 'dhan-market-feed',
      status: 'live',
      received_at: '2026-07-23T14:58:00+05:30',
      age_seconds: 0.4,
      stale: false,
      message: null,
    },
  },
  pnl: { realized: 2_735, unrealized: 660, session: 3_395, available_balance: 202_420, utilized_amount: 43_258 },
  config: { active: {}, paper: {}, live: {} },
  account: {
    dhan_client_id_masked: '******3142',
    has_dhan_access_token: true,
    dhan_token_saved_at: '2026-07-23T08:30:00+05:30',
    token_age_minutes: 388,
    token_expired: false,
    token_estimated_expiry_at: '2026-07-24T08:30:00+05:30',
  },
  safety: { dhan_mode: 'paper', live_orders_enabled: false },
  selected_strategy: strategy('supertrend', 'NOVA Supertrend', true),
  eligible_strategies: [
    strategy('supertrend', 'NOVA Supertrend', true),
    strategy('orb', 'NOVA ORB'),
    strategy('vwap', 'NOVA VWAP Bands'),
    strategy('ardine', 'ardine-trend-breakout'),
  ],
  selection_issue: null,
} satisfies RuntimeStatus

function strategyRisk(name: string, orders: number, pnlPaise: number, lossPct: number) {
  return {
    strategy_name: name,
    kill_switch: false,
    effective: {
      kill_switch: false,
      max_lots_per_order: 5,
      max_notional_per_trade_paise: 1_000_000,
      max_orders_per_day: 150,
      max_loss_per_day_paise: 2_500_000,
    },
    usage: {
      orders_count: orders,
      notional_used_paise: orders * 82_000,
      realized_pnl_paise: pnlPaise,
      loss_used_paise: Math.round(2_500_000 * lossPct / 100),
    },
    utilisation: {
      orders: { used: orders, limit: 150, unlimited: false, pct: orders / 1.5 },
      notional: { used: orders * 82_000, limit: 1_000_000, unlimited: false, pct: Math.min(orders * 8.2, 100) },
      loss: { used: Math.round(2_500_000 * lossPct / 100), limit: 2_500_000, unlimited: false, pct: lossPct },
    },
  }
}

function strategy(instanceId: string, displayName: string, selected = false) {
  return {
    instance_id: instanceId,
    display_name: displayName,
    strategy_code: instanceId,
    strategy_version: '1.0.0',
    source_type: 'NOVA_SHARED' as const,
    setup_type: null,
    status: 'READY',
    instance_status: 'READY',
    mode: 'paper' as const,
    execution_mode: 'PAPER',
    paper_eligible: true,
    live_eligible: false as const,
    readiness: { paper: true },
    lots: 1,
    credential_status: 'not_required' as const,
    installation_status: null,
    selectable: true,
    selected,
    blocking_reason: null,
    owner: 'self' as const,
  }
}
