import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getRiskOverview: vi.fn() }))
vi.mock('./riskApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./riskApi')>(),
  ...apiMocks,
}))

import { RiskPage } from './RiskPage'

const controls = (over: Record<string, unknown> = {}) => ({
  kill_switch: false,
  max_lots_per_order: 0,
  max_notional_per_trade_paise: 0,
  max_orders_per_day: 0,
  max_loss_per_day_paise: 0,
  ...over,
})

const overview = (over: Record<string, unknown> = {}) => ({
  ok: true,
  available: true,
  trade_date_ist: '2026-07-24',
  scope: 'strategy_fanout',
  user: controls({ max_orders_per_day: 10 }),
  strategies: [{
    strategy_name: 'supertrend',
    kill_switch: false,
    effective: controls({ max_orders_per_day: 10 }),
    usage: { orders_count: 4, notional_used_paise: 0, realized_pnl_paise: 250_000, loss_used_paise: 0 },
    utilisation: {
      orders: { used: 4, limit: 10, unlimited: false, pct: 40 },
      notional: { used: 0, limit: 0, unlimited: true, pct: null },
      loss: { used: 0, limit: 0, unlimited: true, pct: null },
    },
  }],
  ...over,
})

afterEach(() => { cleanup(); apiMocks.getRiskOverview.mockReset() })

describe('RiskPage', () => {
  it('shows usage against a real limit', async () => {
    apiMocks.getRiskOverview.mockResolvedValue(overview())
    render(<RiskPage />)
    expect(await screen.findByText('supertrend')).toBeInTheDocument()
    expect(screen.getByText('4 / 10 · 40%')).toBeInTheDocument()
  })

  it('renders an unconfigured limit as "No limit", never 0% or 100%', async () => {
    apiMocks.getRiskOverview.mockResolvedValue(overview())
    render(<RiskPage />)
    await screen.findByText('supertrend')
    // Both the notional and the loss budget are unconfigured here.
    expect(screen.getAllByText('₹0 / No limit')).toHaveLength(2)
    expect(screen.queryByText(/0 \/ 0 · 100%/)).toBeNull()
    expect(screen.getByText(/zero means no limit is configured/i)).toBeInTheDocument()
  })

  it('states that a profitable day consumes no loss budget', async () => {
    apiMocks.getRiskOverview.mockResolvedValue(overview())
    render(<RiskPage />)
    await screen.findByText('supertrend')
    expect(screen.getByText(/no loss budget consumed/i)).toBeInTheDocument()
  })

  it('does not present fan-out gates as the engine runtime settings', async () => {
    apiMocks.getRiskOverview.mockResolvedValue(overview())
    render(<RiskPage />)
    expect(await screen.findByText(/separate from the engine/i)).toBeInTheDocument()
  })

  it('shows a truthful empty state instead of fabricated rows', async () => {
    apiMocks.getRiskOverview.mockResolvedValue(overview({ strategies: [] }))
    render(<RiskPage />)
    expect(await screen.findByText(/no strategy activity/i)).toBeInTheDocument()
  })

  it('surfaces a load failure instead of showing zeros', async () => {
    apiMocks.getRiskOverview.mockRejectedValue(new Error('Could not load risk limits: 500'))
    render(<RiskPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load risk limits: 500')
  })
})
