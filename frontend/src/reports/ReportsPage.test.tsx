import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({ getReport: vi.fn() }))
vi.mock('./reportsApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./reportsApi')>(),
  ...apiMocks,
}))

import { ReportsPage } from './ReportsPage'

const report = (over: Record<string, unknown> = {}) => ({
  ok: true,
  mode: 'paper',
  period: { start: '2026-06-25', end: '2026-07-24', timezone: 'Asia/Kolkata' },
  totals: {
    trades: 3, winning_trades: 2, losing_trades: 1, scratch_trades: 0,
    gross_profit: 500, gross_loss: 100, net_pnl: 400,
  },
  win_rate: { value: 66.7, reason: null },
  average_winner: { value: 250, reason: null },
  average_loser: { value: -100, reason: null },
  profit_factor: { value: 5, reason: null },
  max_drawdown: { value: 100, reason: null, basis: 'closed_trade_equity' },
  trades: [
    { closed_at: '2026-07-24T09:30:00Z', strategy: 'supertrend', symbol: 'NIFTY CE', option_side: 'CE', qty: 75, entry_price: 100, exit_price: 104, charges: 0, gross_pnl: 300, realized_pnl: 300 },
  ],
  ...over,
})

afterEach(() => { cleanup(); apiMocks.getReport.mockReset() })

describe('ReportsPage', () => {
  it('shows the computed totals', async () => {
    apiMocks.getReport.mockResolvedValue(report())
    render(<ReportsPage />)
    expect(await screen.findByText('₹400')).toBeInTheDocument()
    expect(screen.getByText('66.7%')).toBeInTheDocument()
    expect(screen.getByText('5.00')).toBeInTheDocument()
  })

  it('shows an undefined profit factor as unavailable, never as a number', async () => {
    apiMocks.getReport.mockResolvedValue(report({
      profit_factor: { value: null, reason: 'Undefined without a losing trade.' },
      average_loser: { value: null, reason: 'No losing trades in this period.' },
    }))
    render(<ReportsPage />)
    await screen.findByText('66.7%')
    expect(screen.getAllByText('Not available').length).toBe(2)
    expect(screen.getByText('Undefined without a losing trade.')).toBeInTheDocument()
    expect(screen.queryByText('Infinity')).toBeNull()
    expect(screen.queryByText('0.00')).toBeNull()
  })

  it('names the basis of max drawdown instead of implying an equity curve', async () => {
    apiMocks.getReport.mockResolvedValue(report())
    render(<ReportsPage />)
    expect(await screen.findByText(/does not store an intraday equity curve/i)).toBeInTheDocument()
  })

  it('shows a truthful empty period', async () => {
    apiMocks.getReport.mockResolvedValue(report({
      totals: { trades: 0, winning_trades: 0, losing_trades: 0, scratch_trades: 0, gross_profit: 0, gross_loss: 0, net_pnl: 0 },
      win_rate: { value: null, reason: 'No closed trades in this period.' },
      trades: [],
    }))
    render(<ReportsPage />)
    expect(await screen.findByText(/no closed trades between/i)).toBeInTheDocument()
  })

  it('offers a CSV download pointing at the server aggregate', async () => {
    apiMocks.getReport.mockResolvedValue(report())
    render(<ReportsPage />)
    const link = await screen.findByRole('link', { name: /csv/i })
    expect(link.getAttribute('href')).toContain('/api/reports/export.csv')
    expect(link).toHaveAttribute('download')
  })
})
